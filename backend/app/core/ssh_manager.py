"""SSH管理器 - 处理与远程执行机的SSH连接、命令执行、文件传输

迁移自桌面版 ssh_manager.py，并做以下增强:
- 密钥加载自动尝试 RSA / Ed25519 / ECDSA
- execute_with_callback 支持捕获累计输出(用于解析 docker load 结果)
- 跟踪当前活动 channel, 支持 cancel_active() 主动中断远程命令
- list_dir 失败时抛出 RemoteFSError(便于 Web 层返回明确错误)
"""

import os
import stat
import time
import threading
import paramiko
from typing import Optional, Tuple, List, Callable


class RemoteFSError(Exception):
    """远程文件系统操作失败"""


class SSHManager:
    """SSH连接管理器"""

    def __init__(self):
        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.host = ""
        self.port = 22
        self.username = ""
        self._connected = False
        self._active_channel = None
        self._channel_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected and self.client is not None

    @staticmethod
    def _load_pkey(key_path: str):
        """依次尝试多种密钥类型加载私钥"""
        errors = []
        for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return key_cls.from_private_key_file(key_path)
            except Exception as e:  # noqa: BLE001 - 记录每种类型的失败原因
                errors.append(f"{key_cls.__name__}: {e}")
        raise paramiko.ssh_exception.SSHException(
            "无法加载密钥文件: " + "; ".join(errors))

    def connect(self, host: str, port: int, username: str,
                password: str = None, key_path: str = None,
                timeout: int = 10) -> Tuple[bool, str]:
        """连接到远程主机, 返回 (success, message)"""
        self.disconnect()
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                'hostname': host,
                'port': port,
                'username': username,
                'timeout': timeout,
            }

            if key_path:
                key_path = os.path.expanduser(key_path)
                connect_kwargs['pkey'] = self._load_pkey(key_path)
            elif password:
                connect_kwargs['password'] = password
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False

            self.client.connect(**connect_kwargs)

            # 设置大窗口以提高传输速度
            transport = self.client.get_transport()
            transport.default_window_size = 2147483647
            transport.packetizer.MAX_PACKET_SIZE = 32768

            self.sftp = transport.open_sftp_client()
            self.host = host
            self.port = port
            self.username = username
            self._connected = True
            return True, f"连接成功: {host}:{port}"
        except paramiko.AuthenticationException:
            self._connected = False
            return False, "认证失败，请检查用户名/密码/密钥"
        except paramiko.SSHException:
            self._connected = False
            raise
        except Exception as e:  # noqa: BLE001
            self._connected = False
            return False, f"连接失败: {e}"

    def execute(self, command: str, timeout: int = 300) -> Tuple[int, str, str]:
        """执行远程命令, 返回 (return_code, stdout, stderr)"""
        if not self.connected or not self.client:
            return -1, "", "未连接到远程主机"

        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode('utf-8', errors='replace')
            err = stderr.read().decode('utf-8', errors='replace')
            return exit_code, out, err
        except Exception as e:  # noqa: BLE001
            return -1, "", str(e)

    def execute_with_callback(self, command: str,
                              callback: Callable[[str], None] = None,
                              timeout: int = 600,
                              capture: bool = False) -> "int | tuple":
        """执行命令并实时输出(通过回调函数)

        capture=False: 返回 exit_code
        capture=True:  返回 (exit_code, accumulated_output)
        """
        if not self.connected or not self.client:
            return (-1, "") if capture else -1

        chunks: List[str] = []
        try:
            transport = self.client.get_transport()
            channel = transport.open_session()
            channel.set_combine_stderr(True)
            channel.get_pty()
            channel.exec_command(command)
            with self._channel_lock:
                self._active_channel = channel

            deadline = time.time() + timeout
            while True:
                if channel.recv_ready():
                    data = channel.recv(4096).decode('utf-8', errors='replace')
                    if data:
                        if capture:
                            chunks.append(data)
                        if callback:
                            callback(data)
                if channel.exit_status_ready():
                    while channel.recv_ready():
                        data = channel.recv(4096).decode('utf-8', errors='replace')
                        if data:
                            if capture:
                                chunks.append(data)
                            if callback:
                                callback(data)
                    break
                if timeout and time.time() > deadline:
                    channel.close()
                    if callback:
                        callback(f"\n[ERROR] 命令超时 ({timeout}s): {command}\n")
                    return (-1, "".join(chunks)) if capture else -1
                time.sleep(0.1)

            return (channel.exit_status, "".join(chunks)) if capture else channel.exit_status
        except Exception as e:  # noqa: BLE001
            if callback:
                callback(f"\n[ERROR] {e}\n")
            return (-1, "".join(chunks)) if capture else -1
        finally:
            with self._channel_lock:
                self._active_channel = None

    def cancel_active(self):
        """主动关闭当前活动 channel, 用于取消正在执行的远程命令"""
        with self._channel_lock:
            channel = self._active_channel
        if channel is not None:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass

    def upload_file(self, local_path: str, remote_path: str,
                    progress_callback: Callable[[int, int], None] = None) -> bool:
        """上传文件到远程主机(大窗口 + pipelined write, 速度快数倍)"""
        if not self.connected or not self.sftp:
            return False

        try:
            file_size = os.path.getsize(local_path)
            transferred = 0
            chunk_size = 1024 * 1024
            with open(local_path, 'rb') as fl:
                with self.sftp.file(remote_path, 'wb') as fr:
                    fr.set_pipelined(True)
                    while True:
                        data = fl.read(chunk_size)
                        if not data:
                            break
                        fr.write(data)
                        transferred += len(data)
                        if progress_callback:
                            progress_callback(transferred, file_size)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"上传失败: {e}")
            return False

    def list_dir(self, path: str) -> List[dict]:
        """列出远程目录内容

        返回 [{"name":..., "type": "dir"/"file", "size":...}, ...]
        失败抛出 RemoteFSError
        """
        if not self.connected or not self.sftp:
            raise RemoteFSError("SSH未连接")

        result = []
        try:
            entries = self.sftp.listdir_attr(path)
            for entry in entries:
                entry_type = "dir" if stat.S_ISDIR(entry.st_mode) else "file"
                result.append({
                    'name': entry.filename,
                    'type': entry_type,
                    'size': entry.st_size if entry_type == "file" else 0,
                })
        except IOError as e:
            raise RemoteFSError(f"无法访问目录 {path}: {e}") from e
        return sorted(result, key=lambda x: (x['type'] != 'dir', x['name']))

    def list_dirs_only(self, path: str) -> List[str]:
        """只列出目录"""
        entries = self.list_dir(path)
        return [e['name'] for e in entries if e['type'] == 'dir']

    def file_exists(self, path: str) -> bool:
        """检查远程文件/目录是否存在"""
        if not self.connected or not self.sftp:
            return False
        try:
            self.sftp.stat(path)
            return True
        except IOError:
            return False

    def mkdir(self, path: str) -> bool:
        """创建远程目录"""
        if not self.connected or not self.sftp:
            return False
        try:
            self.sftp.mkdir(path)
            return True
        except Exception:  # noqa: BLE001
            return False

    def mkdir_p(self, path: str):
        """递归创建目录"""
        if not self.connected or not self.sftp:
            return
        parts = path.strip('/').split('/')
        current = ''
        for part in parts:
            current += '/' + part
            try:
                self.sftp.stat(current)
            except IOError:
                try:
                    self.sftp.mkdir(current)
                except Exception:  # noqa: BLE001
                    pass

    def write_file(self, remote_path: str, content: str) -> bool:
        """写入远程文件"""
        if not self.connected or not self.sftp:
            return False
        try:
            with self.sftp.open(remote_path, 'wb') as f:
                f.write(content.encode('utf-8'))
            return True
        except Exception as e:  # noqa: BLE001
            print(f"写入文件失败: {remote_path}: {e}")
            return False

    def read_file(self, remote_path: str) -> Optional[str]:
        """读取远程文件"""
        if not self.connected or not self.sftp:
            return None
        try:
            with self.sftp.open(remote_path, 'r') as f:
                return f.read().decode('utf-8')
        except Exception as e:  # noqa: BLE001
            print(f"读取文件失败: {remote_path}: {e}")
            return None

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """SFTP下载单个文件"""
        if not self.connected or not self.sftp:
            return False
        try:
            self.sftp.get(remote_path, local_path)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"SFTP下载失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        try:
            if self.sftp:
                self.sftp.close()
                self.sftp = None
            if self.client:
                self.client.close()
                self.client = None
            self._connected = False
        except Exception:  # noqa: BLE001
            pass
