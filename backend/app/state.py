"""全局会话状态 - 与桌面版单实例模型一致"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .core.docker_manager import DockerManager
from .core.ssh_manager import SSHManager


@dataclass
class DeployInfo:
    """部署成功后的环境信息"""
    container_name: str = ""
    image_name: str = ""
    code_host_path: str = ""
    code_mount_path: str = "/benchmark/ais_bench/aisbench_auto_tools_prefix-main"
    model_path: str = ""
    work_dir: str = ""
    deployed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "container_name": self.container_name,
            "image_name": self.image_name,
            "code_host_path": self.code_host_path,
            "code_mount_path": self.code_mount_path,
            "model_path": self.model_path,
            "work_dir": self.work_dir,
            "deployed_at": self.deployed_at,
        }


class AppState:
    """Web 版全局会话(单用户)"""

    def __init__(self, data_dir: Path, ssh: SSHManager = None,
                 ssh_factory=None):
        self.data_dir = Path(data_dir)
        self.uploads_dir = self.data_dir / "uploads"
        self.results_dir = self.data_dir / "results"
        # 会话持久化文件: SSH凭据 + 部署信息, 服务重启后自动恢复
        # (注意: 凭据为明文保存, 仅适用于内网自用场景)
        self.session_file = self.data_dir / "session.json"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self.ssh = ssh or SSHManager()
        # SSH 实例工厂(用于日志工具临时连接; 测试可注入替身)
        self.ssh_factory = ssh_factory or SSHManager
        self.docker: Optional[DockerManager] = None
        self.deploy_info: Optional[DeployInfo] = None
        self.logtools_other_ssh: Optional[SSHManager] = None

        # 连接/部署互斥锁
        self.lock = threading.RLock()

        # 任务管理器在 tasks.py 中初始化(避免循环导入)
        self.tasks = None  # type: ignore[assignment]

    # ------------------------------------------------------------ SSH

    def save_session(self, ssh_params: dict = None, deploy_info: dict = None):
        """保存/更新会话到 data/session.json (None 参数表示不变, {} 表示清除)"""
        data = self.load_session()
        if ssh_params is not None:
            if ssh_params:
                data["ssh"] = ssh_params
            else:
                data.pop("ssh", None)
        if deploy_info is not None:
            if deploy_info:
                data["deploy"] = deploy_info
            else:
                data.pop("deploy", None)
        try:
            self.session_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def load_session(self) -> dict:
        try:
            if self.session_file.is_file():
                data = json.loads(self.session_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return {}

    def clear_session(self):
        """清除持久化会话(用户主动断开时调用)"""
        try:
            if self.session_file.exists():
                self.session_file.unlink()
        except OSError:
            pass

    def restore_session(self):
        """服务启动时恢复上次会话: 后台自动重连SSH并恢复部署信息

        重连失败(如执行机暂不可达)不影响服务启动, 用户可稍后手动重连;
        步骤1表单会从 remembered 信息回填, 无需重新输入。
        """
        data = self.load_session()
        ssh_params = data.get("ssh") or {}
        deploy = data.get("deploy") or {}
        if not ssh_params.get("host") or not ssh_params.get("username"):
            return

        def _worker():
            key_path = None
            key_id = ssh_params.get("key_upload_id")
            if key_id:
                uploads = self.uploads_dir.resolve()
                p = (uploads / key_id).resolve()
                if str(p).startswith(str(uploads)) and p.is_file():
                    key_path = str(p)
            try:
                ok, _msg = self.ssh.connect(
                    ssh_params["host"],
                    int(ssh_params.get("port") or 22),
                    ssh_params["username"],
                    password=ssh_params.get("password") or None,
                    key_path=key_path,
                    timeout=int(ssh_params.get("timeout") or 10),
                )
            except Exception:  # noqa: BLE001 - 重连失败不阻断启动
                return
            if not ok:
                return
            # 恢复部署信息(容器是否仍在运行以实际检查为准)
            if deploy.get("container_name"):
                info = DeployInfo(
                    container_name=deploy.get("container_name", ""),
                    image_name=deploy.get("image_name", ""),
                    code_host_path=deploy.get("code_host_path", ""),
                    code_mount_path=deploy.get("code_mount_path")
                    or "/benchmark/ais_bench/aisbench_auto_tools_prefix-main",
                    model_path=deploy.get("model_path", ""),
                    work_dir=deploy.get("work_dir", ""),
                    deployed_at=deploy.get("deployed_at", ""),
                )
                docker = DockerManager(self.ssh)
                docker.container_name = info.container_name
                docker.image_name = info.image_name
                docker.code_host_path = info.code_host_path
                docker.code_mount_path = info.code_mount_path
                docker.remote_work_base = info.work_dir or "/tmp/AISBench_Prefix_Tools"
                with self.lock:
                    self.docker = docker
                    self.deploy_info = info

        threading.Thread(target=_worker, daemon=True,
                         name="session-restore").start()

    def ssh_status(self) -> dict:
        remembered = self.load_session().get("ssh") or {}
        return {
            "connected": self.ssh.connected,
            "host": self.ssh.host if self.ssh.connected else "",
            "port": self.ssh.port if self.ssh.connected else 0,
            "username": self.ssh.username if self.ssh.connected else "",
            # 上次连接参数(用于前端表单回填; 不含密码)
            "remembered": {
                "host": remembered.get("host", ""),
                "port": remembered.get("port", 22),
                "username": remembered.get("username", ""),
                "has_credential": bool(remembered.get("password")
                                       or remembered.get("key_upload_id")),
            } if remembered else None,
        }

    def require_ssh(self) -> SSHManager:
        if not self.ssh.connected:
            raise RuntimeError("请先连接执行机 (步骤 1)")
        return self.ssh

    def require_deploy(self) -> DockerManager:
        if not self.docker or not self.deploy_info:
            raise RuntimeError("请先完成 Docker 部署 (步骤 3)")
        return self.docker

    def reset_deploy(self):
        """部署信息重置(断开连接/清理环境后)"""
        self.docker = None
        self.deploy_info = None
        # 同步清除持久化会话中的部署信息
        self.save_session(deploy_info={})

    # ------------------------------------------------------------ 日志工具

    def logtools_ssh(self, server: str = "session") -> SSHManager:
        if server == "other":
            ssh = self.logtools_other_ssh
            if ssh is None or not ssh.connected:
                raise RuntimeError("请先连接其他服务器")
            return ssh
        return self.require_ssh()

    def logtools_status(self) -> dict:
        other = self.logtools_other_ssh
        return {
            "session": self.ssh_status(),
            "other": {
                "connected": bool(other and other.connected),
                "host": other.host if other and other.connected else "",
                "port": other.port if other and other.connected else 0,
                "username": other.username if other and other.connected else "",
            },
        }
