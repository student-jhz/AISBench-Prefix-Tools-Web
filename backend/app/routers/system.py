"""系统状态 / 健康检查 / 环境清理"""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import CleanupRequest
from ..state import AppState
from ..version import __version__
from .deps import get_state

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/state")
def app_state(state: AppState = Depends(get_state)):
    """聚合会话状态: SSH连接/部署信息/任务状态/步骤解锁"""
    ssh = state.ssh_status()
    deploy_info = state.deploy_info.to_dict() if state.deploy_info else None
    container_running = False
    if state.deploy_info and state.docker:
        container_running = state.docker.container_is_running()

    tasks = {kind: state.tasks.status(kind) for kind in ("deploy", "execute", "cleanup")}

    # 步骤解锁规则
    steps = {
        "ssh_connected": ssh["connected"],
        "deployed": deploy_info is not None,
        "container_running": container_running,
    }

    return {
        "version": __version__,
        "ssh": ssh,
        "deploy": deploy_info,
        "container_running": container_running,
        "tasks": tasks,
        "steps": steps,
    }


@router.post("/cleanup")
def cleanup(req: CleanupRequest, state: AppState = Depends(get_state)):
    """清理远程环境: 删除容器 + 清理工作目录"""
    if state.tasks.is_running("cleanup"):
        raise HTTPException(400, "清理任务正在运行中")
    if not (req.remove_container or req.clean_work_dir):
        raise HTTPException(400, "请至少选择一项清理内容")

    work_dir = req.work_dir or (state.deploy_info.work_dir if state.deploy_info else "")
    if req.clean_work_dir and not work_dir:
        raise HTTPException(400, "无法确定远程工作目录")

    state.tasks.start_cleanup(req.remove_container, req.clean_work_dir, work_dir)
    return {"ok": True, "message": "清理任务已启动"}
