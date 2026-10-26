"""全局会话状态 - 与桌面版单实例模型一致"""

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

    def ssh_status(self) -> dict:
        return {
            "connected": self.ssh.connected,
            "host": self.ssh.host if self.ssh.connected else "",
            "port": self.ssh.port if self.ssh.connected else 0,
            "username": self.ssh.username if self.ssh.connected else "",
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
