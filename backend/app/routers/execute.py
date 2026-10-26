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
    if not state.deploy_info:
        raise HTTPException(400, "请先完成 Docker 部署 (步骤 3)")
    if state.tasks.is_running("execute"):
        raise HTTPException(400, "测试任务正在运行中")
    if state.tasks.is_running("deploy"):
        raise HTTPException(400, "部署任务运行中，请等待完成")

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
    state.tasks.start_execute(designer, run_id)
    return {"ok": True, "run_id": run_id, "message": "测试任务已启动"}


@router.get("/status")
def execute_status(state: AppState = Depends(get_state)):
    return state.tasks.status("execute")


@router.post("/cancel")
def cancel_execute(state: AppState = Depends(get_state)):
    ok = state.tasks.cancel("execute")
    return {"ok": ok}
