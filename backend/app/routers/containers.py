"""容器资源池管理 - 列出/选择/启动/忙碌检测(多用户共享执行机)

交互理念(v2.0):
- 执行机上的 aisbench_* 容器是共享的测试资源池, 每个用户选择自己的容器;
- 选择容器时可感知其是否正在执行测试(busy), 避免多个任务挤同一容器;
- 创建容器不是必须的 —— 已有容器可直接进入配置/测试流程。
"""

from fastapi import APIRouter, Depends, HTTPException

from ..core.docker_manager import (
    DockerManager,
    is_managed_container_name,
)
from ..schemas import ContainerNameRequest, ContainerSelectRequest
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/containers", tags=["containers"])


def _docker(state: AppState) -> DockerManager:
    if not state.ssh.connected:
        raise HTTPException(400, "请先连接执行机 (步骤 1)")
    return DockerManager(state.ssh)


@router.get("/list")
def list_containers(state: AppState = Depends(get_state)):
    """列出执行机上的 aisbench 容器(含运行状态与测试执行状态)"""
    docker = _docker(state)
    items = docker.list_containers()
    for item in items:
        if item["running"]:
            busy, detail = docker.container_busy(item["name"])
            item["busy"] = busy
            item["busy_detail"] = detail
        else:
            item["busy"] = False
            item["busy_detail"] = ""
    # 正在运行的排前面, 同状态按名称排序
    items.sort(key=lambda x: (not x["running"], x["name"]))
    return {"containers": items}


@router.get("/images")
def list_images(state: AppState = Depends(get_state)):
    """列出执行机上的镜像(优先选择已有 aisbench 镜像, 无需重复上传)"""
    docker = _docker(state)
    return {"images": docker.list_images()}


@router.post("/select")
def select_container(req: ContainerSelectRequest,
                     state: AppState = Depends(get_state)):
    """选择已有容器作为当前测试容器

    - 容器不存在 → 400
    - 容器正在执行测试 → 返回 busy 信息, 前端确认后带 force=true 重试
    - 容器已停止 → 允许选择, 前端提示可先启动
    """
    docker = _docker(state)
    name = req.name.strip()
    if not is_managed_container_name(name):
        raise HTTPException(400, f"仅支持选择 aisbench 前缀的容器: {name}")

    info = docker.inspect_container(name)
    if not info:
        raise HTTPException(400, f"容器不存在或无法读取信息: {name}")

    busy, busy_detail = (docker.container_busy(name)
                         if info.get("running") else (False, ""))
    if busy and not req.force:
        return {
            "selected": False,
            "busy": True,
            "busy_detail": busy_detail,
            "info": info,
            "message": (
                f"容器 {name} 正在执行测试, 不建议选择。"
                "建议创建一个新的 aisbench_ 容器执行新测试任务。"),
        }

    state.register_container(info, source="selected")
    return {
        "selected": True,
        "busy": busy,
        "busy_detail": busy_detail,
        "info": info,
        "deploy": state.deploy_info.to_dict(),
    }


@router.post("/start")
def start_container(req: ContainerNameRequest,
                    state: AppState = Depends(get_state)):
    """启动已停止的容器"""
    docker = _docker(state)
    name = req.name.strip()
    if not is_managed_container_name(name):
        raise HTTPException(400, f"仅支持操作 aisbench 前缀的容器: {name}")
    if not docker.container_exists(name):
        raise HTTPException(400, f"容器不存在: {name}")
    ok, msg = docker.start_container(name)
    if not ok:
        raise HTTPException(400, f"容器启动失败: {msg}")
    return {"ok": True, "message": f"容器 {name} 已启动"}


@router.get("/busy")
def container_busy(name: str, state: AppState = Depends(get_state)):
    """查询容器是否正在执行测试(选择容器/执行测试前的实时感知)"""
    docker = _docker(state)
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "请指定容器名")
    if not docker.container_exists(name):
        raise HTTPException(400, f"容器不存在: {name}")
    info = docker.inspect_container(name) or {}
    running = bool(info.get("running"))
    busy, detail = (docker.container_busy(name) if running else (False, ""))
    executing_here = state.tasks.is_executing(name) if state.tasks else False
    return {
        "name": name,
        "running": running,
        "busy": busy or executing_here,
        "busy_detail": detail,
        "executing_here": executing_here,
    }
