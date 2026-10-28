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

    def to_dict(self) -> dict:
        return {
            "authenticated": True,
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "created_at": self.created_at,
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
