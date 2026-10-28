"""平台任务 API —— 任务为中心, 容器概念对用户完全隐藏

用户视角只有: 镜像目录 / 测试配置 / 测试用例 / 任务状态 / 结果。
容器的创建、执行、清理全部由调度器自动完成。
"""

import shutil
from fastapi import APIRouter, Depends, HTTPException

from ..core import config_manager as cm
from ..core.docker_manager import DockerManager
from ..core.ssh_manager import RemoteFSError
from ..core.test_designer import TestDesigner
from ..platform.sessions import UserSession
from ..schemas import PlatformTaskCreateRequest
from ..state import AppState
from .auth import require_session
from .deps import get_state

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/tasks")
def list_tasks(state: AppState = Depends(get_state),
               session: UserSession = Depends(require_session)):
    """任务列表(平台共享: 每个登录用户都能看到执行机上的全部任务)"""
    tasks = state.platform_tasks.list()
    queue_no = 0
    items = []
    for t in tasks:
        if t.status == "queued":
            queue_no += 1
            items.append(t.public(queue_position=queue_no))
        else:
            items.append(t.public())
    return {"tasks": items}


@router.post("/tasks")
def create_task(req: PlatformTaskCreateRequest,
                state: AppState = Depends(get_state),
                session: UserSession = Depends(require_session)):
    """提交测试任务: 校验后入队, 由调度器自动准备环境并执行"""
    image = (req.image or "").strip()
    if not image:
        raise HTTPException(400, "请选择测试镜像")

    errors = cm.validate_config(req.config)
    if errors:
        raise HTTPException(400, "测试配置不完整: " + "; ".join(errors))

    try:
        designer = TestDesigner.from_payload(req.designer)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, f"测试用例参数无效: {e}")
    err = designer.validate_case_lengths()
    if err:
        raise HTTPException(400, err)
    if not designer.test_cases:
        raise HTTPException(400, "请先生成测试用例")

    task = state.platform_tasks.create(
        name=(req.name or "").strip(),
        owner=session.user,
        token=session.token,
        image=image,
        config=dict(req.config),
        designer=dict(req.designer),
    )
    state.scheduler.submit(task)
    return {"id": task.id, "status": task.status,
            "message": f"任务已提交: {task.name or task.id}"}


@router.get("/tasks/{task_id}")
def task_detail(task_id: str, state: AppState = Depends(get_state),
                session: UserSession = Depends(require_session)):
    """任务详情(含完整结果行); 容器等内部执行细节不返回"""
    task = state.platform_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task.public(detail=True)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, state: AppState = Depends(get_state),
                session: UserSession = Depends(require_session)):
    """取消任务(排队中立即取消; 执行中中断当前命令)"""
    ok = state.scheduler.cancel(task_id)
    return {"ok": ok}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, state: AppState = Depends(get_state),
                session: UserSession = Depends(require_session)):
    """删除任务及其结果(进行中的任务不可删除)"""
    task = state.platform_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if state.scheduler.is_active(task_id):
        raise HTTPException(400, "任务仍在进行中, 请先等待完成或取消")
    state.platform_tasks.delete(task_id)
    # 结果目录一并清理(任务即结果来源)
    shutil.rmtree(state.results_dir / task_id, ignore_errors=True)
    return {"ok": True}


@router.get("/images")
def list_images(session: UserSession = Depends(require_session)):
    """镜像目录: 用户提交任务时选择执行机上已有的镜像"""
    docker = DockerManager(session.ssh)
    return {"images": docker.list_images()}


@router.get("/fs/list")
def list_remote_dir(path: str = "/",
                    session: UserSession = Depends(require_session)):
    """浏览执行机目录(任务配置中选择模型权重目录)"""
    try:
        entries = session.ssh.list_dir(path or "/")
    except RemoteFSError as e:
        raise HTTPException(400, str(e))
    return {"path": path, "entries": entries}
