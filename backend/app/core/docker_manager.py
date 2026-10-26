"""Docker管理器 - 通过SSH管理远程Docker容器

迁移自桌面版 docker_manager.py，改动:
- load_image 使用输出捕获解析镜像名(修复桌面版重复 docker load 的问题)
- 新增 get_image_name_from_remote_tar(远程tar包manifest预读)
- 新增 work_dir_has_content
- 日志/附件下载统一走 ssh.download_file
"""

import re
import time
import os
import json
import tarfile
from datetime import datetime
from typing import Optional, Tuple, List, Callable

from .ssh_manager import SSHManager


class DockerManager:
    """通过SSH管理Docker容器"""

    def __init__(self, ssh: SSHManager):
        self.ssh = ssh
        self.container_name: Optional[str] = None
        self.image_name: Optional[str] = None
        self.code_mount_path = "/benchmark/ais_bench/aisbench_auto_tools_prefix-main"
        self.code_host_path = ""
        self.remote_work_base = "/tmp/AISBench_Prefix_Tools"

    def check_docker_installed(self) -> Tuple[bool, str]:
        """检查Docker是否安装"""
        code, out, err = self.ssh.execute("docker --version 2>&1")
        if code == 0:
            return True, out.strip()
        return False, f"Docker未安装或不在PATH中: {err}"

    def work_dir_exists(self) -> bool:
        """检查远程工作目录是否已存在"""
        return self.ssh.file_exists(self.remote_work_base)

    def work_dir_has_content(self) -> Tuple[bool, bool, List[str]]:
        """检查工作目录: 返回 (exists, has_content, sample_entries)"""
        if not self.work_dir_exists():
            return False, False, []
        code, out, _ = self.ssh.execute(f"ls -A {self.remote_work_base} 2>/dev/null")
        entries = [l for l in out.strip().splitlines() if l.strip()]
        return True, bool(entries), entries[:20]

    def clean_work_dir(self, callback: Callable[[str], None] = None) -> bool:
        """清理远程工作目录"""
        if callback:
            callback(f"清理工作目录: {self.remote_work_base}\n")
        code, _, _ = self.ssh.execute(f"rm -rf {self.remote_work_base}")
        if code == 0:
            self.ssh.mkdir_p(self.remote_work_base)
            if callback:
                callback("  ✓ 清理完成\n")
            return True
        if callback:
            callback("  ✗ 清理失败\n")
        return False

    @staticmethod
    def _repo_tag_from_manifest(data: bytes) -> Optional[str]:
        """从 manifest.json 字节内容解析镜像 RepoTag"""
        try:
            manifest = json.loads(data)
            if isinstance(manifest, list) and manifest:
                repo_tags = manifest[0].get('RepoTags', [])
                if repo_tags:
                    return repo_tags[0]
        except Exception:  # noqa: BLE001
            pass
        return None

    @classmethod
    def get_image_name_from_tar(cls, local_tar_path: str) -> Optional[str]:
        """从本地tar包中读取镜像名(不上传, 不加载)

        docker save 生成的 tar 中 manifest.json 通常为第一个条目, 直接读取。
        """
        try:
            with tarfile.open(local_tar_path, 'r') as tar:
                # 快路径: manifest.json 位于归档根部
                try:
                    f = tar.extractfile('manifest.json')
                    if f:
                        name = cls._repo_tag_from_manifest(f.read())
                        if name:
                            return name
                except KeyError:
                    pass
                # 慢路径: 扫描成员名
                for member in tar.getnames():
                    if member == 'manifest.json' or member.endswith('/manifest.json'):
                        f = tar.extractfile(member)
                        if f:
                            name = cls._repo_tag_from_manifest(f.read())
                            if name:
                                return name
                        break
        except Exception as e:  # noqa: BLE001
            print(f"读取tar包manifest失败: {e}")
        return None

    def get_image_name_from_remote_tar(self, remote_tar_path: str) -> Optional[str]:
        """从远程tar包读取镜像名(通过 tar -xOf 读取 manifest.json)"""
        code, out, err = self.ssh.execute(
            f"tar -xOf {remote_tar_path} manifest.json 2>/dev/null | head -c 65536")
        if code == 0 and out.strip():
            return self._repo_tag_from_manifest(out.encode('utf-8'))
        return None

    def image_exists(self, image_name: str) -> bool:
        """检查远程是否已存在指定镜像(精确匹配 Repository:Tag)"""
        code, out, _ = self.ssh.execute(
            f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep -ix '{image_name}'"
        )
        return bool(out.strip())

    def load_image(self, remote_tar_path: str,
                   callback: Callable[[str], None] = None) -> Tuple[bool, str]:
        """从tar包加载Docker镜像, 返回 (success, image_name)"""
        if callback:
            callback(f"正在加载镜像: docker load -i {remote_tar_path}\n")

        cmd = f"docker load -i {remote_tar_path}"
        exit_code, output = self.ssh.execute_with_callback(
            cmd, callback, timeout=3600, capture=True)

        if exit_code != 0:
            if callback:
                callback(f"\n镜像加载失败 (exit_code={exit_code})\n")
            return False, ""

        # 从输出中解析镜像名称
        image_name = None
        for pattern in (r"Loaded image:\s*(\S+)", r"Loaded image ID:\s*(\S+)"):
            match = re.search(pattern, output)
            if match:
                image_name = match.group(1).strip()
                break

        if not image_name:
            if callback:
                callback("\n无法自动识别镜像名称，请手动指定\n")
            return False, "无法自动识别镜像名称，请手动指定"

        self.image_name = image_name
        if callback:
            callback(f"\n识别到镜像: {image_name}\n")
        return True, image_name

    def generate_container_name(self) -> str:
        """生成唯一的容器名称"""
        timestamp = datetime.now().strftime('%m%d%H%M')
        return f"aisbench-test-{timestamp}"

    def container_exists(self, name: str) -> bool:
        """检查容器是否存在"""
        code, out, _ = self.ssh.execute(
            f"docker ps -a --filter 'name=^{name}$' --format '{{{{.Names}}}}'"
        )
        return name in out

    def remove_container(self, name: str, callback: Callable[[str], None] = None) -> bool:
        """停止并删除容器"""
        if callback:
            callback(f"停止并删除容器: {name}\n")
        self.ssh.execute(f"docker stop {name} 2>/dev/null")
        self.ssh.execute(f"docker rm {name} 2>/dev/null")
        return True

    def create_container(self, image_name: str, model_path: str,
                         code_host_path: str,
                         container_name: str = None,
                         callback: Callable[[str], None] = None) -> Tuple[bool, str]:
        """创建并启动Docker容器"""
        if container_name is None:
            container_name = self.generate_container_name()

        # 如果容器已存在, 先删除
        if self.container_exists(container_name):
            if callback:
                callback(f"容器 {container_name} 已存在，正在删除旧容器...\n")
            self.remove_container(container_name)

        cmd = (
            f"docker run -itd "
            f"--name {container_name} "
            f"--shm-size=1g "
            f"--net=host "
            f"-v {model_path}:{model_path} "
            f"-v {code_host_path}:{self.code_mount_path} "
            f"-w {self.code_mount_path} "
            f"{image_name} python3"
        )

        if callback:
            callback(f"创建容器命令:\n{cmd}\n\n")

        exit_code = self.ssh.execute_with_callback(cmd, callback, timeout=60)

        if exit_code != 0:
            return False, f"容器创建失败 (exit_code={exit_code})"

        # 验证容器是否运行
        time.sleep(2)
        code, out, _ = self.ssh.execute(
            f"docker ps --filter 'name=^{container_name}$' --format '{{{{.Status}}}}'"
        )
        if "Up" in out:
            self.container_name = container_name
            self.code_host_path = code_host_path
            if callback:
                callback(f"\n容器 {container_name} 已启动运行\n")
            return True, container_name
        return False, f"容器启动失败，状态: {out}"

    def upload_directory(self, local_dir: str, remote_dir: str,
                         callback: Callable[[str], None] = None) -> Tuple[bool, str]:
        """递归上传本地目录到远程目录 (SFTP), 返回 (success, remote_dir 或 错误信息)"""
        if not self.ssh.connected or not self.ssh.sftp:
            return False, "SSH未连接"

        local_dir = os.path.abspath(local_dir)
        if not os.path.isdir(local_dir):
            return False, f"本地目录不存在: {local_dir}"

        # 收集文件清单 (排除缓存/版本库文件)
        file_list = []
        for root, dirs, files in os.walk(local_dir):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git')]
            for f in files:
                if f.endswith(('.pyc', '.pyo')):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_dir).replace('\\', '/')
                file_list.append((full, rel))

        total = len(file_list)
        if total == 0:
            return False, f"本地目录为空: {local_dir}"
        if callback:
            callback(f"  共 {total} 个文件待上传\n")

        self.ssh.mkdir_p(remote_dir)
        created_dirs = {remote_dir}

        done = 0
        for full, rel in file_list:
            # 确保远程父目录存在
            cur = remote_dir
            for part in rel.split('/')[:-1]:
                cur = f"{cur}/{part}"
                if cur not in created_dirs:
                    self.ssh.mkdir_p(cur)
                    created_dirs.add(cur)

            remote_path = f"{remote_dir}/{rel}"
            ok = self.ssh.upload_file(full, remote_path)
            done += 1
            if callback:
                callback(f"\r  上传中: {done}/{total}  {rel}")
            if not ok:
                if callback:
                    callback(f"\n  ✗ 上传失败: {rel}\n")
                return False, f"上传失败: {rel}"

        if callback:
            callback("\n")
        return True, remote_dir

    def extract_code_zip(self, remote_zip_path: str,
                         extract_dir: str = None,
                         callback: Callable[[str], None] = None) -> Tuple[bool, str]:
        """在远程主机上解压代码zip包, 返回 (success, extracted_path)"""
        if extract_dir is None:
            extract_dir = os.path.dirname(remote_zip_path)

        self.ssh.execute(f"mkdir -p {extract_dir}")

        cmd = f"cd {extract_dir} && unzip -o {remote_zip_path}"
        if callback:
            callback(f"解压代码包: {cmd}\n")

        exit_code = self.ssh.execute_with_callback(cmd, callback, timeout=120)

        if exit_code != 0:
            # 尝试安装unzip
            if callback:
                callback("unzip不可用，尝试安装...\n")
            self.ssh.execute(
                "yum install -y unzip 2>/dev/null || apt-get install -y unzip 2>/dev/null")
            exit_code = self.ssh.execute_with_callback(
                f"cd {extract_dir} && unzip -o {remote_zip_path}", callback, timeout=120
            )

        if exit_code != 0:
            return False, "解压失败"

        # 查找解压后的目录名
        zip_name = os.path.basename(remote_zip_path).replace('.zip', '')
        possible_dirs = [zip_name, f"{zip_name}-main", "aisbench_auto_tools_prefix-main"]

        for d in possible_dirs:
            full_path = f"{extract_dir}/{d}"
            if self.ssh.file_exists(full_path):
                if callback:
                    callback(f"代码解压到: {full_path}\n")
                return True, full_path

        # 查找最新创建的目录
        code, out, _ = self.ssh.execute(f"ls -dt {extract_dir}/*/ 2>/dev/null | head -5")
        dirs = [d.strip().rstrip('/') for d in out.strip().split('\n') if d.strip()]
        if dirs:
            return True, dirs[0]

        return False, "无法确定解压目录"

    def exec_in_container(self, command: str,
                          callback: Callable[[str], None] = None,
                          timeout: int = 600) -> int:
        """在容器内执行命令"""
        if not self.container_name:
            if callback:
                callback("错误: 容器未创建\n")
            return -1

        cmd = f"docker exec -w {self.code_mount_path} {self.container_name} {command}"
        return self.ssh.execute_with_callback(cmd, callback, timeout=timeout)

    def container_is_running(self) -> bool:
        """检查容器是否在运行"""
        if not self.container_name:
            return False
        code, out, _ = self.ssh.execute(
            f"docker ps --filter 'name=^{self.container_name}$' --format '{{{{.Status}}}}'"
        )
        return "Up" in out

    def get_container_info(self) -> dict:
        """获取容器信息"""
        info = {'name': self.container_name, 'image': self.image_name, 'status': 'unknown'}
        if not self.container_name:
            return info
        code, out, _ = self.ssh.execute(
            f"docker ps -a --filter 'name=^{self.container_name}$' "
            f"--format '{{{{.Status}}}}|{{{{.Image}}}}'"
        )
        if out.strip():
            parts = out.strip().split('|', 1)
            info['status'] = parts[0]
            if len(parts) > 1:
                info['image'] = parts[1]
        return info

    def stop_and_remove(self, callback: Callable[[str], None] = None):
        """停止并删除容器"""
        if self.container_name:
            self.remove_container(self.container_name, callback)
            self.container_name = None

    # ============================================================
    #  测试脚本执行与日志下载
    # ============================================================

    def write_script(self, script_content: str, filename: str = "run_tests.sh") -> str:
        """将测试脚本写入到宿主机挂载目录(容器内可访问), 返回容器内脚本路径"""
        host_path = f"{self.code_host_path}/{filename}"
        self.ssh.write_file(host_path, script_content)
        self.ssh.execute(f"chmod +x {host_path}")
        return f"{self.code_mount_path}/{filename}"

    def run_script(self, script_name: str = "run_tests.sh",
                   callback: Callable[[str], None] = None,
                   timeout: int = 7200) -> int:
        """在容器内运行测试脚本 (bash <script>)"""
        container_script = f"{self.code_mount_path}/{script_name}"
        cmd = f"docker exec -w {self.code_mount_path} {self.container_name} bash {container_script}"
        return self.ssh.execute_with_callback(cmd, callback, timeout=timeout)

    def find_latest_log_dir(self) -> Optional[str]:
        """查找最新的 test_logs_* 目录(宿主机路径)"""
        code, out, _ = self.ssh.execute(
            f"ls -dt {self.code_host_path}/test_logs_*/ 2>/dev/null | head -1"
        )
        result = out.strip().rstrip('/')
        if result:
            return result
        return None

    def download_logs(self, remote_log_dir: str, local_dir: str,
                      callback: Callable[[str], None] = None) -> List[str]:
        """从远程宿主机下载日志文件到本地目录, 返回本地日志文件路径列表"""
        if not self.ssh.connected or not self.ssh.sftp:
            if callback:
                callback("错误: SSH未连接\n")
            return []

        os.makedirs(local_dir, exist_ok=True)

        entries = self.ssh.list_dir(remote_log_dir)
        log_files = [e['name'] for e in entries if e['type'] == 'file' and e['name'].endswith('.log')]

        local_paths = []
        for fname in sorted(log_files):
            remote_path = f"{remote_log_dir}/{fname}"
            local_path = os.path.join(local_dir, fname)

            if callback:
                callback(f"下载: {fname}\n")

            if self.ssh.download_file(remote_path, local_path):
                local_paths.append(local_path)
            elif callback:
                callback(f"  ✗ 下载失败: {fname}\n")

        if callback:
            callback(f"共下载 {len(local_paths)} 个日志文件到 {local_dir}\n")

        return local_paths

    def download_extra_files(self, filenames: List[str], local_dir: str,
                             callback: Callable[[str], None] = None) -> List[str]:
        """从代码目录(容器工作目录)下载指定附加文件

        如 aisbench_all.log / aisbench_result.csv, 返回本地文件路径列表
        """
        if not self.ssh.connected or not self.ssh.sftp:
            if callback:
                callback("错误: SSH未连接\n")
            return []

        os.makedirs(local_dir, exist_ok=True)

        local_paths = []
        for fname in filenames:
            remote_path = f"{self.code_host_path}/{fname}"
            if not self.ssh.file_exists(remote_path):
                if callback:
                    callback(f"跳过(不存在): {fname}\n")
                continue
            local_path = os.path.join(local_dir, fname)
            if callback:
                callback(f"下载: {fname}\n")
            if self.ssh.download_file(remote_path, local_path):
                local_paths.append(local_path)
            elif callback:
                callback(f"  ✗ 下载失败: {fname}\n")

        return local_paths
