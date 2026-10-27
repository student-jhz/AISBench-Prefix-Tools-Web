"""系统状态 / 健康检查 / 环境清理"""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import CleanupRequest
from ..state import AppState
from ..version import __version__
from ..core.docker_manager import (
    DockerManager,
    is_managed_container_name,
)
from .deps import get_state

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/state")
def app_state(state: AppState = Depends(get_state)):
    """聚合会话状态: SSH连接/当前容器/任务状态/步骤解锁"""
    ssh = state.ssh_status()
    deploy_info = state.deploy_info.to_dict() if state.deploy_info else None
    container_running = False
    container_busy = False
    if state.deploy_info and state.docker:
        container_running = state.docker.container_is_running()
        if container_running and state.ssh.connected:
            container_busy, _ = DockerManager(
                state.ssh).container_busy(state.deploy_info.container_name)

    tasks = {kind: state.tasks.status(kind) for kind in ("deploy", "execute", "cleanup")}

    # 步骤解锁规则
    steps = {
        "ssh_connected": ssh["connected"],
        "container_selected": deploy_info is not None,
        "container_running": container_running,
    }

    return {
        "version": __version__,
        "ssh": ssh,
        "deploy": deploy_info,
        "container_busy": container_busy,
        "container_running": container_running,
        "tasks": tasks,
        "steps": steps,
    }


@router.post("/cleanup")
def cleanup(req: CleanupRequest, state: AppState = Depends(get_state)):
    """清理远程环境: 按显式指定的容器名删除 + 清理工作目录

    多用户安全设计:
    - 只允许清理 aisbench 前缀容器, 其余容器(他人/其他业务)直接拒绝;
    - 必须显式勾选容器名, 绝不做模糊/批量通配删除;
    - 正在执行测试的容器会被跳过, 避免误伤。
    """
    if state.tasks.is_running("cleanup"):
        raise HTTPException(400, "清理任务正在运行中")
    if not (req.containers or req.clean_work_dir):
        raise HTTPException(400, "请至少选择一项清理内容")

    for name in req.containers:
        if not is_managed_container_name((name or "").strip()):
            raise HTTPException(400, f"拒绝清理非 aisbench 前缀容器: {name}")
        if state.tasks.is_executing((name or "").strip()):
            raise HTTPException(
                400, f"容器 {name} 正在执行测试, 请先等待完成或取消")

    work_dir = req.work_dir or (state.deploy_info.work_dir if state.deploy_info else "")
    if req.clean_work_dir and not work_dir:
        raise HTTPException(400, "无法确定远程工作目录")

    state.tasks.start_cleanup(
        [n.strip() for n in req.containers if n.strip()],
        req.clean_work_dir, work_dir)
    return {"ok": True, "message": "清理任务已启动"}
