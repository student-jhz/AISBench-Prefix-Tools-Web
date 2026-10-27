"""config.py 配置管理"""

from fastapi import APIRouter, Depends, HTTPException

from ..core import config_manager as cm
from ..schemas import ConfigPayload
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/config", tags=["config"])


def _resolve(state: AppState, container: str):
    """解析目标容器: 显式指定优先, 兼容旧客户端回退到当前容器"""
    name = (container or "").strip()
    if name:
        return state.docker_for_container(name)
    return state.require_deploy()


def _code_mount(state: AppState, container: str) -> str:
    name = (container or "").strip()
    if name:
        return state.resolve_container(name).get("code_mount_path", "")
    return state.deploy_info.code_mount_path if state.deploy_info else ""


@router.get("/defaults")
def defaults(state: AppState = Depends(get_state)):
    """默认配置 + 部署后的建议值"""
    dataset_default = ""
    if state.deploy_info:
        dataset_default = state.deploy_info.code_mount_path
    return {
        "defaults": cm.DEFAULT_CONFIG,
        "dataset_path_default": dataset_default,
    }


@router.post("/preview")
def preview(req: ConfigPayload, state: AppState = Depends(get_state)):
    """预览生成的 config.py 内容"""
    errors = cm.validate_config(req.config)
    dataset_default = _code_mount(state, req.container)
    content = cm.generate_config_content(req.config, dataset_default)
    return {"content": content, "errors": errors}


@router.post("/save")
def save(req: ConfigPayload, state: AppState = Depends(get_state)):
    """保存配置到容器(写入挂载目录)并回读验证"""
    docker = _resolve(state, req.container)

    errors = cm.validate_config(req.config)
    if errors:
        raise HTTPException(400, "配置验证失败: " + "; ".join(errors))

    dataset_default = _code_mount(state, req.container)
    content = cm.generate_config_content(req.config, dataset_default)

    if not cm.write_config_to_host(docker.ssh, docker.code_host_path, content):
        raise HTTPException(400, "写入 config.py 失败(SFTP写入挂载目录失败)")

    # 回读验证
    read_back = cm.read_config_from_host(docker.ssh, docker.code_host_path)
    if read_back is None:
        return {
            "ok": True,
            "content": content,
            "read_back": None,
            "mismatches": [],
            "warning": "config.py 已写入，但回读失败，请点击\"验证配置\"手动检查",
        }

    mismatches, saved = cm.compare_config(req.config, read_back)
    return {
        "ok": True,
        "content": content,
        "read_back": read_back,
        "mismatches": mismatches,
        "saved_values": saved,
        "host_path": f"{docker.code_host_path}/config.py",
        "container_path": f"{docker.code_mount_path}/config.py",
    }


@router.post("/verify")
def verify(req: ConfigPayload, state: AppState = Depends(get_state)):
    """从容器回读 config.py 与界面值对比"""
    docker = _resolve(state, req.container)
    read_back = cm.read_config_from_host(docker.ssh, docker.code_host_path)
    if read_back is None:
        raise HTTPException(400, "读取容器内 config.py 失败")
    mismatches, saved = cm.compare_config(req.config, read_back)
    return {
        "ok": len(mismatches) == 0,
        "read_back": read_back,
        "mismatches": mismatches,
        "saved_values": saved,
    }


@router.get("/read")
def read(container: str = "", state: AppState = Depends(get_state)):
    """读取容器内当前 config.py"""
    docker = _resolve(state, container)
    content = cm.read_config_from_host(docker.ssh, docker.code_host_path)
    if content is None:
        raise HTTPException(400, "读取容器内 config.py 失败")
    return {
        "content": content,
        "fields": cm.parse_config_content(content),
    }
