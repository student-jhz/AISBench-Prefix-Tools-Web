"""平台任务 API —— 任务为中心, 容器概念对用户完全隐藏

用户视角只有: 镜像目录 / 测试配置 / 测试用例 / 任务状态 / 结果。
容器的创建、执行、清理全部由调度器自动完成。
"""

import shutil
from fastapi import APIRouter, Depends, HTTPException

from ..core import config_manager as cm
from ..core import kv_log_tools as kvt
from ..core.docker_manager import DockerManager
from ..core.ssh_manager import RemoteFSError
from ..core.test_designer import TestDesigner
from ..platform.sessions import UserSession
from ..platform.sessions import resolve_key_path
from ..schemas import (
    EngineGrepRequest,
    EngineOtherConnectRequest,
    PlatformTaskCreateRequest,
)
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


@router.get("/tasks/{task_id}/copy-source")
def task_copy_source(task_id: str, state: AppState = Depends(get_state),
                     session: UserSession = Depends(require_session)):
    """复制历史任务: 返回基于它新建任务所需的用户提交内容

    - 只返回镜像/测试配置/用例设计 —— 内部执行信息(container/token/work_dir)
      仍然不暴露, 与"容器对用户不可见"的平台理念一致;
    - 早期任务 designer 中未持久化勾选长度时, 从用例反推输入/输出长度,
      保证前端复制后长度选择栏能正确回显。
    """
    task = state.platform_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    designer = dict(task.designer or {})
    cases = designer.get("cases") or []
    if cases and not designer.get("input_lengths"):
        designer["input_lengths"] = sorted(
            {int(c["input_len"]) for c in cases
             if c.get("input_len") is not None})
    if cases and not designer.get("output_lengths"):
        designer["output_lengths"] = sorted(
            {int(c["output_len"]) for c in cases
             if c.get("output_len") is not None})
    return {
        "id": task.id,
        "name": task.name,
        "image": task.image,
        "config": task.config or {},
        "designer": designer,
    }


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


# ------------------------------------------------------------
# 引擎参数自动获取: 从 vLLM 引擎日志筛选 GPU KV cache size /
# Maximum concurrency, 用于测试用例设计的 KV cache 与最大请求长度
# (方法迁移自桌面版 AISBench-Prefix-Tools 的日志工具, 三种日志来源)
# ------------------------------------------------------------

def _engine_ssh(session: UserSession, server: str = "session"):
    """解析日志来源服务器: 当前执行机 或 用户临时连接的其他服务器

    vLLM 引擎不一定部署在当前登录的执行机上 —— 引擎日志可能在另一台
    机器; 用户通过 /engine/other/connect 建立自己的临时连接。
    """
    if server == "other":
        ssh = session.engine_ssh
        if not ssh or not ssh.connected:
            raise HTTPException(400, "请先连接其他服务器")
        return ssh
    return session.ssh


@router.post("/engine/other/connect")
def engine_other_connect(req: EngineOtherConnectRequest,
                         state: AppState = Depends(get_state),
                         session: UserSession = Depends(require_session)):
    """连接其他服务器(引擎日志所在机器)

    连接挂在该用户会话上(多用户互不干扰); 密码或密钥二选一,
    密钥复用登录密钥的上传通道(/api/upload/ssh-key)。
    """
    if not (req.password or req.key_upload_id):
        raise HTTPException(400, "请填写密码或上传密钥文件")
    key_path = resolve_key_path(state.uploads_dir, req.key_upload_id or "")
    if req.key_upload_id and not key_path:
        raise HTTPException(400, "密钥文件不存在或已失效, 请重新上传")
    try:
        state.sessions.engine_other_connect(
            session.token, req.host, req.port, req.username,
            password=req.password, key_path=key_path,
            timeout=req.timeout)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "message": f"已连接 {req.host}:{req.port}",
        "status": session.engine_other_status(),
    }


@router.post("/engine/other/disconnect")
def engine_other_disconnect(session: UserSession = Depends(require_session),
                             state: AppState = Depends(get_state)):
    """断开其他服务器连接"""
    state.sessions.engine_other_disconnect(session.token)
    return {"ok": True, "status": session.engine_other_status()}


@router.get("/engine/other/status")
def engine_other_status(session: UserSession = Depends(require_session)):
    """其他服务器连接状态(打开弹窗时回显)"""
    return session.engine_other_status()


@router.get("/engine/containers")
def engine_containers(server: str = "session",
                      state: AppState = Depends(get_state),
                      session: UserSession = Depends(require_session)):
    """列出执行机上运行中的容器(定位 vLLM 引擎日志用)

    这里列出的是用户/运维自行启动的推理服务容器, 不是平台自动创建的
    测试执行容器 —— 后者对用户始终不可见。
    """
    if server not in ("session", "other"):
        raise HTTPException(400, "server 仅支持 session/other")
    try:
        items = kvt.list_containers(_engine_ssh(session, server))
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {"containers": items}


@router.get("/engine/list")
def engine_list(server: str = "session", mode: str = "host", path: str = "/",
                container: str = "",
                state: AppState = Depends(get_state),
                session: UserSession = Depends(require_session)):
    """浏览引擎日志所在目录(host=SFTP / container=docker exec ls)"""
    if server not in ("session", "other"):
        raise HTTPException(400, "server 仅支持 session/other")
    if mode not in ("host", "container"):
        raise HTTPException(400, "mode 仅支持 host/container")
    try:
        entries = kvt.list_dir(_engine_ssh(session, server), mode, path,
                               container or None)
    except RemoteFSError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"列出目录失败: {e}") from e
    return {"path": path, "entries": entries}


@router.post("/engine/grep")
def engine_grep(req: EngineGrepRequest,
                session: UserSession = Depends(require_session)):
    """在选定日志来源中筛选 vLLM KV cache 信息并解析

    返回 (单个DP组KV cache tokens, 模型最大上下文 tokens):
      GPU KV cache size: 198,469 tokens
      Maximum concurrency for 131,072 tokens per request: 3.84x
    """
    cmd = kvt.build_grep_command(req.mode, req.container, req.path)
    if cmd is None:
        raise HTTPException(400, "无法生成筛选命令, 请检查容器/文件选择")

    code, out, err = _engine_ssh(session, req.server).execute(cmd, timeout=180)
    result = {
        "command": cmd,
        "code": code,
        "output": "",
        "error": "",
        "kv": None,
        "max_len": None,
        "parsed": False,
    }
    if code == 0 and out.strip():
        result["output"] = out.rstrip() + "\n"
        kv, max_len = kvt.parse_kv_results(out)
        result["kv"] = kv
        result["max_len"] = max_len
        result["parsed"] = kv is not None or max_len is not None
    elif code == 1:
        result["output"] = "(未找到匹配行, 请确认选择的是 vLLM 启动日志)\n"
    else:
        result["error"] = err.strip() or out.strip() or f"exit={code}"
    return result
