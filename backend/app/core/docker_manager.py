"""Docker管理器 - 通过SSH管理远程Docker容器

迁移自桌面版 docker_manager.py，改动:
- load_image 使用输出捕获解析镜像名(修复桌面版重复 docker load 的问题)
- 新增 get_image_name_from_remote_tar(远程tar包manifest预读)
- 新增 work_dir_has_content
- v2.0: 容器资源池模型 —— 列出/检查/选择已有容器(busy检测), 创建容器
  使用用户自定义的 aisbench_ 前缀名称, 同名冲突报错而非覆盖删除
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


# 容器命名约定: 多用户共享执行机, 所有测试容器统一使用 aisbench_ 前缀,
# 便于识别与隔离; 清理时仅允许操作该前缀的容器, 且必须显式指定名称。
CONTAINER_PREFIX = "aisbench_"
# 兼容旧版本自动生成的 aisbench-test-* 容器, 列表/清理时同样可见
CONTAINER_LIST_PREFIXES = ("aisbench_", "aisbench-")
CONTAINER_NAME_RE = re.compile(r"^aisbench_[A-Za-z0-9][A-Za-z0-9_.-]*$")
# 列表/清理时允许操作的容器名(含旧版 aisbench- 前缀)
MANAGED_CONTAINER_RE = re.compile(r"^aisbench[-_][A-Za-z0-9][A-Za-z0-9_.-]*$")

# 容器内正在执行测试的进程特征(run_tests.sh / aisbench_test.py)
BUSY_PROC_RE = re.compile(r"run_tests\.sh|aisbench_test\.py")


def validate_container_name(name: str) -> Optional[str]:
    """校验容器名(必须 aisbench_ 前缀 + 安全字符), 返回错误信息或 None"""
    name = (name or "").strip()
    if not name:
        return "请填写容器名称"
    if not name.startswith(CONTAINER_PREFIX):
        return f"容器名称必须以 {CONTAINER_PREFIX} 开头 (当前: {name})"
    if not CONTAINER_NAME_RE.match(name):
        return (f"容器名称格式不合法: {name} "
                f"(允许 {CONTAINER_PREFIX} + 字母/数字/下划线/点/连字符)")
    return None


def is_managed_container_name(name: str) -> bool:
    """名称是否属于本工具管理的 aisbench 容器(列表/清理白名单)"""
    return bool(MANAGED_CONTAINER_RE.match((name or "").strip()))


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
        """生成默认容器名称(用户可自定义, 但保持 aisbench_ 前缀)"""
        timestamp = datetime.now().strftime('%m%d_%H%M%S')
        return f"{CONTAINER_PREFIX}test_{timestamp}"

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

    def start_container(self, name: str) -> Tuple[bool, str]:
        """启动已停止的容器"""
        code, out, err = self.ssh.execute(f"docker start {name}")
        if code == 0:
            return True, (out or "").strip() or name
        return False, (err or out).strip() or f"启动失败 (exit_code={code})"

    def list_images(self) -> List[dict]:
        """列出执行机上全部镜像(供用户选择, 前端按需过滤 aisbench)"""
        code, out, _ = self.ssh.execute(
            "docker images --format "
            "'{{.Repository}}\\t{{.Tag}}\\t{{.ID}}\\t{{.Size}}\\t{{.CreatedAt}}'"
        )
        items: List[dict] = []
        if code != 0:
            return items
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            repo, tag, image_id, size = parts[0], parts[1], parts[2], parts[3]
            if repo == "<none>":
                continue
            items.append({
                "repository": repo,
                "tag": tag,
                "name": f"{repo}:{tag}",
                "id": image_id,
                "size": size,
                "created": parts[4] if len(parts) > 4 else "",
            })
        return items

    def list_containers(self,
                        prefixes=CONTAINER_LIST_PREFIXES) -> List[dict]:
        """列出执行机上的 aisbench 测试容器(多用户共享的容器资源池)

        仅返回指定前缀的容器, 其余容器(他人/其他业务)不可见也不可清理。
        """
        code, out, _ = self.ssh.execute(
            "docker ps -a --format "
            "'{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.RunningFor}}'"
        )
        items: List[dict] = []
        if code != 0:
            return items
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, image, status = parts[0], parts[1], parts[2]
            if not any(name.startswith(p) for p in prefixes):
                continue
            items.append({
                "name": name,
                "image": image,
                "status": status,
                "running": status.lower().startswith("up"),
                "created": parts[3] if len(parts) > 3 else "",
            })
        return items

    def container_busy(self, name: str) -> Tuple[bool, str]:
        """检测容器内是否有测试正在执行(docker top 检查测试进程)

        无论测试是否由本服务启动(其他用户/手工 docker exec), 均可检测,
        用于避免多个测试任务同时占用同一容器互相干扰。
        """
        code, out, _ = self.ssh.execute(
            f"docker top {name} -o pid,cmd 2>/dev/null")
        if code != 0:
            return False, ""
        for line in out.strip().splitlines()[1:]:
            if BUSY_PROC_RE.search(line):
                return True, line.strip()[:200]
        return False, ""

    def inspect_container(self, name: str) -> Optional[dict]:
        """docker inspect 读取容器信息(镜像/状态/挂载), 用于选择已有容器

        返回 None 表示容器不存在; 挂载解析:
        - 代码挂载: 容器内路径含 aisbench_auto_tools_prefix
        - 其余挂载视为模型目录挂载
        """
        code, out, _ = self.ssh.execute(
            f"docker inspect --format "
            f"'{{{{.Config.Image}}}}\\t{{{{.State.Status}}}}\\t"
            f"{{{{json .HostConfig.Binds}}}}' {name} 2>/dev/null")
        if code != 0 or not out.strip():
            return None
        line = out.strip().splitlines()[0]
        parts = line.split("\t", 2)
        image = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        try:
            binds = json.loads(parts[2]) if len(parts) > 2 else []
        except (ValueError, IndexError):
            binds = []

        mounts = []
        for bind in binds or []:
            seg = str(bind).split(":")
            if len(seg) >= 2:
                mounts.append({"host": seg[0], "container": seg[1]})

        code_mount = None
        model_mounts = []
        for m in mounts:
            if "aisbench_auto_tools_prefix" in m["container"]:
                code_mount = m
            else:
                model_mounts.append(m)

        return {
            "name": name,
            "image": image,
            "state": state,
            "running": state == "running",
            "mounts": mounts,
            "code_host_path": code_mount["host"] if code_mount else "",
            "code_mount_path": code_mount["container"] if code_mount
            else self.code_mount_path,
            "model_mounts": model_mounts,
            # MODEL_PATH 默认值: 第一个模型挂载的容器内路径
            "model_path_hint": model_mounts[0]["container"] if model_mounts else "",
            "work_dir": "",
        }

    def create_container(self, image_name: str, model_mount: str,
                         code_host_path: str,
                         container_name: str,
                         callback: Callable[[str], None] = None) -> Tuple[bool, str]:
        """创建并启动Docker容器(用户自定义名称, 已存在则报错而非覆盖)

        model_mount: 要映射进容器的模型(父)目录, 宿主机路径原样映射,
        该目录下的任意模型在容器内均以相同路径可见。
        """
        err = validate_container_name(container_name)
        if err:
            return False, err

        # 多用户安全: 同名容器绝不静默删除, 让用户自行清理或换名
        if self.container_exists(container_name):
            return False, (
                f"容器名已被占用: {container_name}\n"
                f"  请换一个名称, 或先在\"容器管理\"中删除该容器")

        cmd = (
            f"docker run -itd "
            f"--name {container_name} "
            f"--shm-size=1g "
            f"--net=host "
            f"-v {model_mount}:{model_mount} "
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
