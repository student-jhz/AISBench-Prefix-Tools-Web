"""远程日志工具核心逻辑 - 从宿主机/容器中选择日志文件:
1. 筛选 vLLM KV cache 信息(可解析后一键填入测试参数)
2. 查看指定日志文件内容(尾部)

迁移自桌面版 log_tools.py 的非 GUI 部分。
"""

import re
import shlex
from typing import List, Optional, Tuple

from .ssh_manager import SSHManager


# KV cache 查询命令 (与界面展示保持一致)
KV_GREP_COMMAND = "cat vllm_serve.log | grep -e 'GPU KV cache size' -e 'Maximum concurrency'"

# 查看日志时加载文件尾部的最大字节数 / docker logs 行数
VIEW_TAIL_BYTES = 2 * 1024 * 1024
VIEW_DOCKER_LOGS_LINES = 5000


def q(value: str) -> str:
    """POSIX shell 引用, 用于远程命令中的路径/容器名"""
    return shlex.quote(value)


def list_containers(ssh: SSHManager) -> List[dict]:
    """列出运行中的容器 [{"name", "status", "image"}]"""
    code, out, err = ssh.execute(
        "docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}'", timeout=30)
    if code != 0:
        raise RuntimeError((err.strip() or out.strip() or f"exit={code}"))
    containers = []
    for line in out.strip().splitlines():
        parts = line.split("|", 2)
        if parts and parts[0].strip():
            containers.append({
                "name": parts[0].strip(),
                "status": parts[1].strip() if len(parts) > 1 else "",
                "image": parts[2].strip() if len(parts) > 2 else "",
            })
    return containers


def parse_ls_la(out: str) -> List[dict]:
    """解析 ls -la 输出为 [{name, type, size}]"""
    entries = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("total ") or line.endswith(":"):
            continue
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        perms, _links, _owner, _group, size, *_date, name = parts
        if name in (".", ".."):
            continue
        try:
            size_val = int(size)
        except ValueError:
            size_val = 0
        entries.append({
            "name": name,
            "type": "dir" if perms.startswith("d") else "file",
            "size": size_val,
        })
    return sorted(entries, key=lambda e: (e["type"] != "dir", e["name"]))


def list_dir(ssh: SSHManager, mode: str, path: str,
             container: str = None) -> List[dict]:
    """列出目录内容

    mode: "host"(SFTP) 或 "container"(docker exec ls -la)
    """
    path = (path or "/").strip() or "/"
    if mode == "container":
        if not container:
            raise RuntimeError("请先选择容器")
        cmd = f"docker exec {q(container)} ls -la {q(path)}"
        code, out, err = ssh.execute(cmd, timeout=30)
        if code != 0:
            raise RuntimeError((err.strip() or out.strip() or f"exit={code}"))
        return parse_ls_la(out)
    return ssh.list_dir(path)


def build_grep_command(mode: str, container: str = None,
                       file_path: str = None) -> Optional[str]:
    """构造 KV cache 筛选命令"""
    grep = "grep -e 'GPU KV cache size' -e 'Maximum concurrency'"
    if mode == "docker_logs":
        if not container:
            return None
        return f"docker logs {q(container)} 2>&1 | {grep}"
    if not file_path:
        return None
    if mode == "container":
        if not container:
            return None
        return f"docker exec {q(container)} {grep} {q(file_path)}"
    return f"{grep} {q(file_path)}"


def build_view_command(mode: str, container: str = None,
                       file_path: str = None) -> Optional[str]:
    """构造日志查看命令"""
    if mode == "docker_logs":
        if not container:
            return None
        return f"docker logs --tail {VIEW_DOCKER_LOGS_LINES} {q(container)} 2>&1"
    if not file_path:
        return None
    if mode == "container":
        if not container:
            return None
        return (f"docker exec {q(container)} "
                f"tail -c {VIEW_TAIL_BYTES} {q(file_path)}")
    return f"tail -c {VIEW_TAIL_BYTES} {q(file_path)}"


def parse_kv_results(text: str) -> Tuple[Optional[int], Optional[int]]:
    """从 grep 结果解析 (单个DP组KV cache tokens, 模型最大上下文 tokens)"""
    kv = None
    max_len = None

    match = re.search(r"GPU KV cache size:?\s*([\d,]+)\s*tokens", text)
    if match:
        try:
            kv = int(match.group(1).replace(",", ""))
        except ValueError:
            pass

    match = re.search(r"Maximum concurrency for ([\d,]+) tokens per request", text)
    if match:
        try:
            max_len = int(match.group(1).replace(",", ""))
        except ValueError:
            pass

    return kv, max_len


def join_path(base: str, name: str) -> str:
    base = base.rstrip("/")
    return f"{base}/{name}" if base else f"/{name}"


def parent_path(path: str) -> str:
    stripped = path.rstrip("/")
    if not stripped or stripped == "/":
        return "/"
    return stripped.rsplit("/", 1)[0] or "/"
