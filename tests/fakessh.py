"""测试用 FakeSSH - 模拟远程执行机的 SSH/SFTP/Docker 行为"""

import io
import json
import os
import re
import tarfile


SAMPLE_TEST_LOG = """2026-10-20 10:00:00 INFO input token length: 8192
2026-10-20 10:00:00 INFO output token length: 512
2026-10-20 10:00:00 INFO number of dataset: 108
2026-10-20 10:00:00 INFO concurrency: 91
2026-10-20 10:00:01 INFO [开始] 全量数据集测试
2026-10-20 10:05:00 INFO {'current_time': '2026-10-20 10:05:00', 'input_len': 8192, 'output_len': 1, 'result_len': 1}
2026-10-20 10:10:00 INFO {'current_time': '2026-10-20 10:10:00', 'input_len': 8192, 'output_len': 512, 'result_len': 512, 'E2E_time': 12.5, 'qpm': 432.1}
Performance Results of task prefix_cache:
TTFT (ms):  total  200.3 ms 120.5 ms 350.8 ms
TPOT (ms):  total  25.6 ms 15.2 ms 40.1 ms
E2EL (ms):  total  9000.5 ms 8000.1 ms 9500.2 ms
Common Metrics:
  Prefill Token Throughput: 12345.6 token/s
  Input Token Throughput: 12345.6 token/s
  Output Token Throughput: 789.0 token/s
  Total Token Throughput: 13134.6 token/s
  Request Throughput: 1.5 req/s
  Concurrency   91
2026-10-20 10:10:01 INFO 全量数据集测试完成，结果保存在aisbench_result.csv
Prefix cache hit summary:
POD_NAME    hbm_hit_rate  hit_num/total_num  external_hit_rate  hit_num/total_num
ALL_PODS    85.32%  1000/1200  92.15%  1100/1200
"""


class FakeSSH:
    """模拟 SSHManager 的行为"""

    def __init__(self):
        self.connected = False
        self.sftp = True  # docker_manager 检查该属性
        self.host = ""
        self.port = 22
        self.username = ""
        self.files = {}       # remote_path -> bytes/str
        self.images = set()   # 已加载镜像
        self.containers = {}  # name -> status
        self.container_meta = {}    # name -> {"image", "binds"}
        self.busy_containers = set()  # 模拟正在执行测试的容器
        self.container_fs = {}  # name -> {容器内路径: [子目录名, ...]}(ls 浏览用)
        self.container_logs = {}  # name -> 容器日志文本(docker logs / 引擎 KV grep)
        self.exec_log = []

    # ------------------------------------------------------------ 连接

    def connect(self, host, port, username, password=None,
                key_path=None, timeout=10):
        if password == "wrong":
            return False, "认证失败，请检查用户名/密码/密钥"
        self.connected = True
        self.host = host
        self.port = port
        self.username = username
        return True, f"连接成功: {host}:{port}"

    def disconnect(self):
        self.connected = False

    def cancel_active(self):
        pass

    # ------------------------------------------------------------ 命令执行

    def execute(self, command, timeout=300):
        self.exec_log.append(command)

        if command.startswith("docker --version"):
            return 0, "Docker version 24.0.0, build abc\n", ""

        # ---- v2.0.1 容器资源池: 镜像列表(逐行 JSON, 规避 \t 转义问题) ----
        if "docker images --format" in command and "{{json .}}" in command:
            out = ""
            for img in sorted(self.images):
                repo, _, tag = img.rpartition(":")
                out += json.dumps({
                    "Repository": repo, "Tag": tag, "ID": "abc123",
                    "Size": "1.2GB",
                    "CreatedAt": "2026-01-01 00:00:00 +0800 CST",
                }) + "\n"
            return 0, out, ""

        # ---- v2.0.1 容器资源池: 容器列表(逐行 JSON, 含已停止) ----
        # v2.0.5: Names 用逗号分隔字符串 —— 与真实 docker 完全一致。
        # (旧替身返回数组, 掩盖了生产端 list_containers 按数组取
        # names[0] 得到首字符 "a"、列表恒为空的 bug)
        if "docker ps -a --format" in command and "{{json .}}" in command:
            out = ""
            for name, status in self.containers.items():
                meta = self.container_meta.get(name, {})
                state = "running" if status.lower().startswith("up") else "exited"
                out += json.dumps({
                    "Names": name,
                    "Image": meta.get("image", "fakeimage:latest"),
                    "State": state,
                    "Status": status,
                    "RunningFor": "About a minute ago",
                }) + "\n"
            return 0, out, ""

        # ---- v3.0.2 引擎参数获取: 运行中容器列表(管道分隔格式) ----
        if "docker ps --format" in command and "{{.Names}}" in command:
            out = ""
            for name, status in self.containers.items():
                if not status.lower().startswith("up"):
                    continue   # docker ps(不带 -a)只列运行中
                meta = self.container_meta.get(name, {})
                out += (f"{name}|{status}|"
                        f"{meta.get('image', 'fakeimage:latest')}\n")
            return 0, out, ""

        # ---- v2.0.1: docker inspect(标准 JSON 输出, 镜像/状态/挂载) ----
        if command.startswith("docker inspect"):
            m = re.search(r"docker inspect '?([\w.-]+)'? 2>/dev/null", command)
            name = m.group(1) if m else ""
            if name not in self.containers:
                return 1, "", "Error: No such object: " + name
            status = self.containers.get(name, "Up 1 second")
            state = "running" if status.lower().startswith("up") else "exited"
            meta = self.container_meta.get(name, {})
            binds = meta.get("binds", [])
            info = {
                "Config": {"Image": meta.get("image", "fakeimage:latest")},
                "State": {
                    "Status": state,
                    "Running": state == "running",
                },
                "HostConfig": {"Binds": binds},
            }
            return 0, json.dumps([info]) + "\n", ""

        # ---- v2.0.1: 容器内目录浏览(docker exec ls, 步骤3 MODEL_PATH) ----
        if "docker exec" in command and "ls -1Ap" in command:
            m = re.search(r"docker exec '([\w.-]+)' ls -1Ap '([^']*)'", command)
            name, path = (m.group(1), m.group(2)) if m else ("", "/")
            if name not in self.containers:
                return 1, "", "Error: No such container: " + name
            subdirs = self.container_fs.get(name, {}).get(path)
            if subdirs is None:
                return 2, "", f"ls: cannot access '{path}': No such file or directory"
            out = "".join(f"{d}/\n" for d in subdirs)
            return 0, out, ""

        # ---- v2.0: docker top(测试执行中检测) ----
        if command.startswith("docker top"):
            m = re.search(r"docker top (\S+)", command)
            name = m.group(1) if m else ""
            if name not in self.containers:
                return 1, "", "Error: No such container: " + name
            # 始终带一行 defunct 僵尸(命令行含 run_tests.sh):
            # 回归保障 —— 僵尸进程不得判定为"正在执行测试"
            out = "PID\tCMD\n1234\tpython3\n1300\tbash run_tests.sh <defunct>\n"
            if name in self.busy_containers:
                out += "2345\tbash run_tests.sh\n"
            return 0, out, ""

        # ---- v2.0: docker start ----
        if command.startswith("docker start"):
            m = re.search(r"docker start (\S+)", command)
            if m and m.group(1) in self.containers:
                self.containers[m.group(1)] = "Up 1 second"
                return 0, m.group(1) + "\n", ""
            return 1, "", "Error: No such container"

        if "docker ps --format" in command and "{{.Names}}|{{.Status}}|{{.Image}}" in command:
            # logtools: 列出运行中容器
            out = "".join(f"{n}|{s}|fakeimage:latest\n" for n, s in self.containers.items())
            return 0, out, ""

        if "docker images --format" in command:
            m = re.search(r"grep -ix '([^']+)'", command)
            name = m.group(1) if m else ""
            return 0, (name + "\n" if name in self.images else ""), ""

        if "docker ps" in command and "--format" in command:
            m = re.search(r"name=\^(\S+)\$", command)
            name = m.group(1) if m else ""
            if ".Names}}" in command and "|" not in command:
                # container_exists / ps names
                return 0, (name + "\n" if name in self.containers else ""), ""
            status = self.containers.get(name, "")
            if ".Status}}|{{.Image}}" in command:
                return 0, (f"{status}|fakeimage:latest\n" if status else ""), ""
            return 0, (status + "\n" if status else ""), ""

        if command.startswith("docker stop") or command.startswith("docker rm"):
            m = re.search(r"(?:stop|rm) (\S+)", command)
            if m:
                self.containers.pop(m.group(1), None)
                self.container_meta.pop(m.group(1), None)
                self.busy_containers.discard(m.group(1))
            return 0, "", ""

        if "ls -A" in command:
            m = re.search(r"ls -A (\S+)", command)
            d = m.group(1) if m else ""
            entries = [p[len(d):].lstrip("/").split("/")[0] for p in self.files
                       if p.startswith(d + "/") and p.count("/") == d.count("/") + 1]
            return 0, "\n".join(entries) + "\n", ""

        if "ls -dt" in command and "test_logs_" in command:
            m = re.search(r"ls -dt (\S+)/test_logs_", command)
            base = m.group(1) if m else ""
            dirs = {p.rsplit("/", 1)[0] for p in self.files
                    if p.startswith(base + "/test_logs_")}
            if dirs:
                return 0, sorted(dirs)[-1] + "/\n", ""
            return 0, "", ""

        if command.startswith("rm -rf"):
            m = re.search(r"rm -rf (\S+)", command)
            if m:
                d = m.group(1)
                self.files = {p: v for p, v in self.files.items()
                              if not p.startswith(d + "/") and p != d}
            return 0, "", ""

        if "mkdir -p" in command or "chmod +x" in command:
            return 0, "", ""

        if "_check_model_path.py" in command:
            return 0, "OK: MODEL_PATH=/mnt/models/ds\n", ""

        # ---- v3.0.2 引擎参数获取: 容器内目录浏览(docker exec ls -la) ----
        if "docker exec" in command and "ls -la" in command:
            m = re.search(r"docker exec '?([\w.-]+)'? ls -la (\S+)", command)
            name, path = (m.group(1), m.group(2)) if m else ("", "")
            if name not in self.containers:
                return 1, "", f"Error: No such container: {name}"
            # 容器 FS 用 container_fs 描述; 未描述时给含 vllm_serve.log 的
            # 默认列表(与 execute_with_callback 的既有行为一致)
            names = self.container_fs.get(name, {}).get(path)
            if names is None:
                names = ["vllm_serve.log"]
            out = "total 4\n"
            for n in names:
                out += f"-rw-r--r-- 1 root root 1024 Jan 1 00:00 {n}\n"
            return 0, out, ""

        if "tar -xOf" in command:
            return 0, "", ""

        # ---- v3.0.2 引擎参数获取: vLLM KV cache grep(host/容器文件/docker logs) ----
        # 忠实模拟 grep: 从文件/容器日志中筛选含标记的行, 无匹配则 exit 1
        if "grep" in command and "GPU KV cache size" in command \
                and "Maximum concurrency" in command:
            if command.startswith("docker logs"):
                # shlex.quote 仅对含特殊字符的名称加引号
                m = re.search(r"docker logs '?([\w.-]+)'?", command)
                cname = m.group(1) if m else ""
                if cname not in self.containers:
                    return 1, "", f"Error: No such container: {cname}"
                source = self.container_logs.get(cname, "")
            else:
                # 路径是命令最后一个参数; shlex.quote 仅对含特殊字符的
                # 路径加引号, 简单路径(/root/a.log)不带引号
                m = re.search(r"Maximum concurrency'\s+(.+)$", command)
                path = (m.group(1) if m else "").strip().strip("'")
                content = self.files.get(path)
                if content is None:
                    return 1, "", ""
                source = content if isinstance(content, str) \
                    else content.decode("utf-8", errors="replace")
            lines = [l for l in source.splitlines()
                     if "GPU KV cache size" in l
                     or "Maximum concurrency" in l]
            out = "\n".join(lines) + ("\n" if lines else "")
            return (0 if lines else 1), out, ""

        if command.startswith("tail "):
            m = re.search(r"tail -c \d+ (\S+)", command)
            path = m.group(1).strip("'\"") if m else ""
            content = self.files.get(path)
            if content is None:
                return 1, "", f"tail: cannot open '{path}'"
            return 0, (content if isinstance(content, str)
                       else content.decode("utf-8")), ""

        return 0, "", ""

    def execute_with_callback(self, command, callback=None, timeout=600,
                              capture=False):
        self.exec_log.append(command)

        if "docker load" in command:
            image = "aisbench:test"
            out = f"Loaded image: {image}\n"
            if callback:
                callback(out)
            self.images.add(image)
            return (0, out) if capture else 0

        if command.startswith("docker run"):
            m = re.search(r"--name (\S+)", command)
            name = m.group(1) if m else "unknown"
            self.containers[name] = "Up 2 seconds"
            # 记录镜像与挂载, 供 docker inspect 模拟使用
            image = command.strip().split()[-2] if command.strip().endswith("python3") \
                else "fakeimage:latest"
            binds = re.findall(r"-v (\S+)", command)
            self.container_meta[name] = {"image": image, "binds": binds}
            out = "abc123def456\n"
            if callback:
                callback(out)
            return (0, out) if capture else 0

        if "run_tests.sh" in command:
            # 模拟测试脚本执行: 生成日志文件(路径取自该容器实际代码挂载,
            # 与真实 docker 行为一致 —— v3.0 平台任务用独立工作目录)
            m = re.search(r"docker exec -w \S+ (\S+) bash", command)
            cname = m.group(1) if m else ""
            binds = self.container_meta.get(cname, {}).get("binds", [])
            workdir = next(
                (b.split(":")[0] for b in binds
                 if "aisbench_auto_tools_prefix" in b),
                "/tmp/AISBench_Prefix_Tools/aisbench_auto_tools_prefix-main")
            log_dir = f"{workdir}/test_logs_20261020_101010"
            self.files[f"{log_dir}/test_1_8192_512.log"] = SAMPLE_TEST_LOG
            self.files[f"{workdir}/aisbench_all.log"] = "aisbench all log"
            out = "预检查通过: MODEL_PATH=/mnt/models/ds\n[开始] 全量数据集测试\n测试 #1 成功\n所有测试完成！\n"
            if callback:
                callback(out)
            return (0, out) if capture else 0

        if "unzip" in command:
            out = "inflating: aisbench_auto_tools_prefix-main/aisbench_test.py\n"
            if callback:
                callback(out)
            return (0, out) if capture else 0

        if command.startswith("docker exec") and "ls -la" in command:
            m = re.search(r"ls -la (\S+)", command)
            out = "total 4\n-rw-r--r-- 1 root root 1024 Jan 1 00:00 vllm_serve.log\n"
            if callback:
                callback(out)
            return (0, out) if capture else 0

        if callback:
            callback("")
        return (0, "") if capture else 0

    # ------------------------------------------------------------ 文件操作

    def upload_file(self, local_path, remote_path, progress_callback=None):
        if not self.connected:
            return False
        try:
            with open(local_path, "rb") as f:
                self.files[remote_path] = f.read()
            if progress_callback:
                progress_callback(100, 100)
            return True
        except OSError:
            return False

    def list_dir(self, path):
        if not self.connected:
            return []
        prefix = path.rstrip("/") + "/"
        depth = prefix.count("/")  # 直接子文件路径的斜杠数与 prefix 相同
        names = set()
        for p in self.files:
            if p.startswith(prefix) and p.count("/") == depth:
                names.add((p.rsplit("/", 1)[-1], "file"))
            elif p.startswith(prefix) and p.count("/") > depth:
                names.add((p[len(prefix):].split("/")[0], "dir"))
        result = [{"name": n, "type": t, "size": 100}
                  for n, t in sorted(names)]
        return sorted(result, key=lambda x: (x["type"] != "dir", x["name"]))

    def file_exists(self, path):
        if path in self.files:
            return True
        prefix = path.rstrip("/") + "/"
        return any(p.startswith(prefix) for p in self.files)

    def mkdir(self, path):
        return True

    def mkdir_p(self, path):
        pass

    def write_file(self, remote_path, content):
        if not self.connected:
            return False
        self.files[remote_path] = content
        return True

    def read_file(self, remote_path):
        content = self.files.get(remote_path)
        if content is None:
            return None
        return content if isinstance(content, str) else content.decode("utf-8")

    def download_file(self, remote_path, local_path):
        content = self.files.get(remote_path)
        if content is None:
            return False
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return True


def make_image_tar(path: str, image_name: str = "aisbench:test"):
    """生成带 manifest.json 的模拟镜像 tar"""
    import json
    manifest = [{"Config": "config.json", "RepoTags": [image_name], "Layers": []}]
    with tarfile.open(path, "w") as tar:
        info = tarfile.TarInfo("manifest.json")
        data = json.dumps(manifest).encode("utf-8")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
