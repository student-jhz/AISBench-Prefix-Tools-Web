"""平台会话管理 —— 登录执行机 = 建立该用户的 SSH 会话

v3.0 交互理念:
- 登录前: 前端只有登录界面, 平台 API 一律 401;
- 登录: 用户提交执行机 SSH 凭据, 服务端为该用户建立独立 SSH 会话并签发 token;
- 每个浏览器持有自己的 token, 多用户互不干扰;
- 任务通过创建者的会话在执行机上执行; 会话显式登出或服务重启后失效。
"""

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from ..core.ssh_manager import SSHManager
from ..state import AppState


@dataclass
class UserSession:
    """一个已登录用户(浏览器)对应的执行机会话"""

    token: str
    user: str
    host: str
    port: int
    ssh: SSHManager
    created_at: str
    # 引擎日志获取的“其他服务器”临时连接(可选):
    # vLLM 引擎不一定部署在当前登录的执行机上, 用户可临时连接日志
    # 所在服务器; 该连接挂在会话上, 多用户互不干扰。
    engine_ssh: Optional[SSHManager] = None

    def to_dict(self) -> dict:
        return {
            "authenticated": True,
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "created_at": self.created_at,
        }

    def engine_other_status(self) -> dict:
        """其他服务器连接状态(未连接时字段为空)"""
        ssh = self.engine_ssh
        connected = bool(ssh and ssh.connected)
        return {
            "connected": connected,
            "host": ssh.host if connected else "",
            "port": ssh.port if connected else 0,
            "username": ssh.username if connected else "",
        }


class SessionRegistry:
    """token → UserSession 注册表(内存态; 服务重启后需重新登录)"""

    def __init__(self, state: AppState):
        self.state = state
        self._sessions: dict = {}
        self._lock = threading.Lock()
        self._login_hooks: List[Callable[[UserSession], None]] = []

    # ------------------------------------------------------------ 钩子

    def add_login_hook(self, fn: Callable[[UserSession], None]):
        """登录成功后的回调(如孤儿任务容器清理), 异常不影响登录"""
        self._login_hooks.append(fn)

    # ------------------------------------------------------------ 会话生命周期

    def login(self, host: str, port: int, username: str,
              password: str = None, key_path: str = None,
              timeout: int = 10) -> UserSession:
        """建立执行机 SSH 会话并签发 token; 认证失败抛 RuntimeError"""
        ssh = self.state.ssh_factory()
        ok, msg = ssh.connect(host, int(port), username,
                              password=password, key_path=key_path,
                              timeout=timeout)
        if not ok:
            raise RuntimeError(msg or "登录失败, 请检查主机/端口/用户名/密码")
        token = secrets.token_hex(20)
        session = UserSession(
            token=token, user=username, host=host, port=int(port), ssh=ssh,
            created_at=datetime.now().isoformat(timespec="seconds"))
        with self._lock:
            self._sessions[token] = session
        for fn in self._login_hooks:
            try:
                fn(session)
            except Exception:  # noqa: BLE001 - 钩子失败不影响登录
                pass
        return session

    def get(self, token: str) -> Optional[UserSession]:
        token = (token or "").strip()
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    def logout(self, token: str) -> bool:
        session = self.get(token)
        if not session:
            return False
        with self._lock:
            self._sessions.pop(token, None)
        try:
            session.ssh.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            if session.engine_ssh:
                session.engine_ssh.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return True

    # ------------------------------------------------------------ 其他服务器(引擎日志)

    def engine_other_connect(self, token: str, host: str, port: int,
                             username: str, password: str = None,
                             key_path: str = None,
                             timeout: int = 10) -> UserSession:
        """为该会话建立“其他服务器”临时连接; 认证失败抛 RuntimeError"""
        session = self.get(token)
        if not session:
            raise RuntimeError("未登录或会话已失效")
        ssh = self.state.ssh_factory()
        ok, msg = ssh.connect(host, int(port), username,
                              password=password, key_path=key_path,
                              timeout=timeout)
        if not ok:
            try:
                ssh.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(msg or "连接失败, 请检查主机/端口/用户名/密码")
        old = session.engine_ssh
        session.engine_ssh = ssh
        if old:
            try:
                old.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return session

    def engine_other_disconnect(self, token: str) -> bool:
        """断开该会话的其他服务器连接"""
        session = self.get(token)
        if not session:
            return False
        ssh = session.engine_ssh
        session.engine_ssh = None
        if ssh:
            try:
                ssh.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return True

    def active(self) -> List[UserSession]:
        with self._lock:
            return list(self._sessions.values())


def resolve_key_path(uploads_dir: Path, key_upload_id: str) -> Optional[str]:
    """登录密钥上传 ID → 本地路径(校验位于 uploads 目录内, 防路径穿越)"""
    if not key_upload_id:
        return None
    uploads = Path(uploads_dir).resolve()
    p = (uploads / key_upload_id).resolve()
    if not str(p).startswith(str(uploads)) or not p.is_file():
        return None
    return str(p)
