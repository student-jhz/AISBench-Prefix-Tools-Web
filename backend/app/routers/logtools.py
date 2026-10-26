"""远程日志工具 - 宿主机/容器/docker logs 三种来源"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..core import kv_log_tools as kvt
from ..schemas import (
    LogtoolsConnectRequest,
    LogtoolsGrepRequest,
    LogtoolsViewRequest,
)
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/logtools", tags=["logtools"])


def _resolve_key(state: AppState, upload_id: str) -> str:
    uploads = Path(state.uploads_dir).resolve()
    path = (uploads / upload_id).resolve()
    if not str(path).startswith(str(uploads)) or not path.is_file():
        raise HTTPException(400, f"密钥文件不存在: {upload_id}")
    return str(path)


@router.post("/other/connect")
def other_connect(req: LogtoolsConnectRequest, state: AppState = Depends(get_state)):
    """临时连接其他服务器(用于查看其日志)"""
    if not req.password and not req.key_upload_id:
        raise HTTPException(400, "请填写密码或上传密钥文件")

    # 复用或新建临时连接
    if state.logtools_other_ssh is None:
        state.logtools_other_ssh = state.ssh_factory()

    key_path = _resolve_key(state, req.key_upload_id) if req.key_upload_id else None
    try:
        ok, msg = state.logtools_other_ssh.connect(
            req.host, req.port, req.username,
            password=req.password, key_path=key_path, timeout=req.timeout)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"连接失败: {e}") from e
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg, "status": state.logtools_status()}


@router.post("/other/disconnect")
def other_disconnect(state: AppState = Depends(get_state)):
    if state.logtools_other_ssh:
        state.logtools_other_ssh.disconnect()
        state.logtools_other_ssh = None
    return {"ok": True}


@router.get("/status")
def status(state: AppState = Depends(get_state)):
    return state.logtools_status()


@router.get("/containers")
def containers(server: str = "session", state: AppState = Depends(get_state)):
    """列出运行中的容器"""
    ssh = state.logtools_ssh(server)
    try:
        items = kvt.list_containers(ssh)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {"containers": items}


@router.get("/list")
def list_dir(server: str = "session", mode: str = "host", path: str = "/",
             container: str = "", state: AppState = Depends(get_state)):
    """列出目录内容(host=SFTP / container=docker exec ls)"""
    if mode not in ("host", "container"):
        raise HTTPException(400, "mode 仅支持 host/container")
    ssh = state.logtools_ssh(server)
    try:
        entries = kvt.list_dir(ssh, mode, path, container or None)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"列出目录失败: {e}") from e
    return {"path": path, "entries": entries}


@router.post("/grep")
def grep(req: LogtoolsGrepRequest, state: AppState = Depends(get_state)):
    """筛选 KV cache 信息并解析"""
    cmd = kvt.build_grep_command(req.mode, req.container, req.path)
    if cmd is None:
        raise HTTPException(400, "无法生成筛选命令, 请检查容器/文件选择")

    ssh = state.logtools_ssh(req.server)
    code, out, err = ssh.execute(cmd, timeout=180)

    result = {
        "command": cmd,
        "code": code,
        "output": "",
        "error": "",
        "kv": None,
        "max_len": None,
        "parsed": False,
    }

    if code == 0 and out.strip():
        result["output"] = out.rstrip() + "\n"
        kv, max_len = kvt.parse_kv_results(out)
        result["kv"] = kv
        result["max_len"] = max_len
        result["parsed"] = kv is not None or max_len is not None
    elif code == 1:
        result["output"] = "(未找到匹配行, 请确认选择的是 vLLM 启动日志)\n"
    else:
        result["error"] = err.strip() or out.strip() or f"exit={code}"
    return result


@router.post("/view")
def view(req: LogtoolsViewRequest, state: AppState = Depends(get_state)):
    """查看日志内容(文件取尾部2MB / docker logs取尾部5000行)"""
    cmd = kvt.build_view_command(req.mode, req.container, req.path)
    if cmd is None:
        raise HTTPException(400, "无法生成查看命令, 请检查容器/文件选择")

    ssh = state.logtools_ssh(req.server)
    code, out, err = ssh.execute(cmd, timeout=180)
    if code != 0:
        raise HTTPException(400, err.strip() or out.strip() or f"exit={code}")

    content = out if out.strip() else "(日志为空)"
    return {
        "command": cmd,
        "content": content,
        "size": len(content),
        "tail_bytes": kvt.VIEW_TAIL_BYTES,
        "tail_lines": kvt.VIEW_DOCKER_LOGS_LINES,
    }
