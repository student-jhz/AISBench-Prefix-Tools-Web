"""执行测试"""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import ExecuteStartRequest
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/execute", tags=["execute"])


@router.post("/start")
def start_execute(req: ExecuteStartRequest, state: AppState = Depends(get_state)):
    """启动测试执行(后台任务), 日志通过 WS exec 通道推送"""
    # 目标容器: 请求显式指定优先(多用户各自选择), 兼容回退到当前容器
    container = (req.container or "").strip()
    if not container:
        if not state.deploy_info:
            raise HTTPException(400, "请先在步骤 2 选择或创建测试容器")
        container = state.deploy_info.container_name

    if state.tasks.is_running("deploy"):
        raise HTTPException(400, "容器创建任务运行中，请等待完成")
    if state.tasks.is_executing(container):
        raise HTTPException(400, f"容器 {container} 已有测试任务正在执行")

    # 感知容器实时状态: 未运行/正在执行测试均不允许启动
    docker = state.docker_for_container(container)
    if not docker.container_is_running():
        raise HTTPException(400, f"容器 {container} 未运行，请先启动容器")
    busy, busy_detail = docker.container_busy(container)
    if busy:
        raise HTTPException(400, (
            f"容器 {container} 正在执行测试，请等待完成或另选/新建容器\n"
            f"检测到进程: {busy_detail}"))

    designer = req.designer
    if not isinstance(designer, dict) or not designer.get("cases"):
        raise HTTPException(400, "请先生成测试用例")

    # 生成 run_id
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = (req.run_name or "").strip()
    if run_name:
        safe = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff\-]+", "_", run_name)[:40]
        run_id = f"run_{stamp}_{safe}" if safe else f"run_{stamp}"
    else:
        run_id = f"run_{stamp}"

    state.tasks.bus.clear("exec")
    state.tasks.bus.clear(f"exec@{container}")
    state.tasks.start_execute(designer, run_id, container)
    return {"ok": True, "run_id": run_id, "container": container,
            "message": "测试任务已启动"}


@router.get("/status")
def execute_status(container: str = "", state: AppState = Depends(get_state)):
    """执行任务状态(指定容器查该容器, 不指定查最近一次)"""
    return state.tasks.exec_status((container or "").strip())


@router.post("/cancel")
def cancel_execute(container: str = "", state: AppState = Depends(get_state)):
    ok = state.tasks.cancel_execute((container or "").strip())
    return {"ok": ok}
