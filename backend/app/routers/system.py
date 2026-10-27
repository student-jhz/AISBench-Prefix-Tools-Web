"""系统状态 / 健康检查 / 环境清理"""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import CleanupRequest
from ..state import AppState
from ..version import __version__
from ..core.docker_manager import is_managed_container_name
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
    # v2.0.2: 本端点被前端高频轮询(10s 一次, 任务运行时 1.2s 一次),
    # 绝不能做 SSH 往返 —— docker ps / docker top 在 SSH 慢或命令阻塞时
    # 可能耗时数秒~分钟, 曾把前端首屏 init 卡在 await /api/state 上,
    # 4 秒启动自检误报"界面加载失败"(刷新也无效, 因为每次都再等一次)。
    # 运行状态取自容器注册表(选择/创建时登记), busy 取自本服务执行分槽;
    # 跨用户实时 busy 检测由 GET /api/containers/busy 单独提供。
    container_running = False
    container_busy = False
    if state.deploy_info:
        name = state.deploy_info.container_name
        info = state.containers.get(name) or {}
        container_running = bool(info.get("running"))
        if state.tasks:
            container_busy = state.tasks.is_executing(name)

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
