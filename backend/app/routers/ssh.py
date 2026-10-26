"""SSH 连接与远程目录浏览"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.ssh_manager import RemoteFSError
from ..schemas import SSHConnectRequest
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/ssh", tags=["ssh"])


def _resolve_key_path(state: AppState, upload_id: str) -> str:
    from pathlib import Path
    uploads = Path(state.uploads_dir).resolve()
    path = (uploads / upload_id).resolve()
    if not str(path).startswith(str(uploads)) or not path.is_file():
        raise HTTPException(400, f"密钥文件不存在: {upload_id}")
    return str(path)


@router.post("/connect")
def connect(req: SSHConnectRequest, state: AppState = Depends(get_state)):
    if not req.password and not req.key_upload_id:
        raise HTTPException(400, "请填写密码或上传密钥文件")
    if state.tasks.is_running("deploy") or state.tasks.is_running("execute"):
        raise HTTPException(400, "部署/测试任务运行中，请先取消或等待完成")

    key_path = None
    if req.key_upload_id:
        key_path = _resolve_key_path(state, req.key_upload_id)

    try:
        ok, msg = state.ssh.connect(req.host, req.port, req.username,
                                    password=req.password, key_path=key_path,
                                    timeout=req.timeout)
    except Exception as e:  # noqa: BLE001 - paramiko底层异常
        raise HTTPException(400, f"连接失败: {e}") from e

    if not ok:
        raise HTTPException(400, msg)

    # 持久化连接参数: 服务重启后自动重连, 页面刷新后表单回填
    state.save_session(ssh_params={
        "host": req.host,
        "port": req.port,
        "username": req.username,
        "password": req.password,
        "key_upload_id": req.key_upload_id,
        "timeout": req.timeout,
    })

    # 新连接意味着旧的部署信息失效
    state.reset_deploy()
    return {"ok": True, "message": msg, "status": state.ssh_status()}


@router.post("/disconnect")
def disconnect(state: AppState = Depends(get_state)):
    if state.tasks.is_running("deploy") or state.tasks.is_running("execute"):
        raise HTTPException(400, "部署/测试任务运行中，无法断开连接")
    state.ssh.disconnect()
    state.reset_deploy()
    state.clear_session()
    return {"ok": True}


@router.get("/status")
def status(state: AppState = Depends(get_state)):
    return state.ssh_status()


fs_router = APIRouter(prefix="/fs", tags=["ssh"])


@fs_router.get("/list")
def list_dir(path: str = "/", state: AppState = Depends(get_state)):
    """SFTP 列出远程目录(模型路径选择器)"""
    if not state.ssh.connected:
        raise HTTPException(400, "请先连接执行机")
    try:
        entries = state.ssh.list_dir(path or "/")
    except RemoteFSError as e:
        raise HTTPException(400, str(e)) from e
    return {"path": path, "entries": entries}
