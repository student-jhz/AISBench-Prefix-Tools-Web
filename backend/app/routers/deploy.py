"""容器创建(原 Docker 部署步骤)"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.docker_manager import validate_container_name
from ..schemas import DeployStartRequest, RemoteTarCheckRequest
from ..paths import bundle_dir
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/deploy", tags=["deploy"])


def _core_code_dir(state: AppState) -> str:
    """内置测试代码目录(随 Web 服务容器分发)"""
    core_code = (bundle_dir() / "backend" / "core_code" /
                 "aisbench_auto_tools_prefix")
    if not core_code.is_dir():
        raise HTTPException(500, f"内置测试代码目录缺失: {core_code}")
    return str(core_code)


@router.get("/preflight")
def preflight(work_dir: str = "/tmp/AISBench_Prefix_Tools",
              state: AppState = Depends(get_state)):
    """检查远程工作目录是否存在/是否有内容"""
    if not state.ssh.connected:
        raise HTTPException(400, "请先连接执行机")
    work_dir = (work_dir or "").strip() or "/tmp/AISBench_Prefix_Tools"

    docker = state.docker
    if docker is None:
        from ..core.docker_manager import DockerManager
        docker = DockerManager(state.ssh)
    docker.remote_work_base = work_dir

    exists, has_content, entries = docker.work_dir_has_content()
    return {
        "work_dir": work_dir,
        "exists": exists,
        "has_content": has_content,
        "entries": entries,
    }


@router.post("/start")
def start_deploy(req: DeployStartRequest, state: AppState = Depends(get_state)):
    """创建新容器(后台任务), 日志通过 WS deploy 通道推送

    流程: 选择镜像(已有优先) → 模型父目录映射 → 自定义容器名 → 创建。
    """
    if not state.ssh.connected:
        raise HTTPException(400, "请先连接执行机 (步骤 1)")
    if state.tasks.is_running("deploy"):
        raise HTTPException(400, "部署任务正在运行中")

    # 参数校验
    image = req.image
    if image.type == "existing" and not (image.name or "").strip():
        raise HTTPException(400, "请选择执行机上已有的镜像")
    if image.type == "upload" and not image.upload_id:
        raise HTTPException(400, "请上传镜像 tar 包")
    if image.type == "remote" and not (image.remote_path or "").strip():
        raise HTTPException(400, "请填写远程镜像 tar 路径")
    if req.code.source == "zip" and not req.code.zip_upload_id:
        raise HTTPException(400, "请上传代码 zip 包")
    if not req.model_mount.strip():
        raise HTTPException(400, "请填写要映射进容器的模型目录")
    err = validate_container_name(req.container_name.strip())
    if err:
        raise HTTPException(400, err)

    # 清理旧日志通道
    state.tasks.bus.clear("deploy")

    params = {
        "image": {"type": image.type,
                  "name": (image.name or "").strip(),
                  "upload_id": image.upload_id,
                  "remote_path": image.remote_path},
        "model_mount": req.model_mount,
        "container_name": req.container_name,
        "work_dir": req.work_dir,
        "code": {"source": req.code.source,
                 "zip_upload_id": req.code.zip_upload_id},
    }
    state.tasks.start_deploy(params, _core_code_dir(state))
    return {"ok": True, "message": "部署任务已启动"}


@router.get("/status")
def deploy_status(state: AppState = Depends(get_state)):
    return state.tasks.status("deploy")


@router.post("/cancel")
def cancel_deploy(state: AppState = Depends(get_state)):
    ok = state.tasks.cancel("deploy")
    return {"ok": ok}


@router.post("/check-remote-tar")
def check_remote_tar(req: RemoteTarCheckRequest, state: AppState = Depends(get_state)):
    """检查远程tar包并预读镜像名"""
    if not state.ssh.connected:
        raise HTTPException(400, "请先连接执行机")
    from ..core.docker_manager import DockerManager
    docker = DockerManager(state.ssh)
    exists = state.ssh.file_exists(req.remote_path)
    image_name = docker.get_image_name_from_remote_tar(req.remote_path) if exists else None
    return {"exists": exists, "image_name": image_name}
