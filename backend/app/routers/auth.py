"""平台登录/会话 —— 登录执行机后才进入任务平台

登录前前端只有登录界面; 平台 API 凭 Bearer token 访问。
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from ..platform.sessions import UserSession, resolve_key_path
from ..schemas import PlatformLoginRequest
from ..state import AppState
from .deps import get_state

router = APIRouter(prefix="/auth", tags=["auth"])


def get_session_token(request: Request) -> str:
    """从 Authorization: Bearer <token> 提取会话 token"""
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_session(request: Request,
                    state: AppState = Depends(get_state)) -> UserSession:
    """平台 API 鉴权依赖: 无有效会话一律 401"""
    session = state.sessions.get(get_session_token(request))
    if not session:
        raise HTTPException(401, "未登录或会话已失效, 请重新登录执行机")
    return session


@router.post("/login")
def login(req: PlatformLoginRequest, state: AppState = Depends(get_state)):
    """登录执行机: 建立该用户的 SSH 会话并签发 token"""
    if not (req.password or req.key_upload_id):
        raise HTTPException(400, "请填写密码或上传密钥文件")
    key_path = resolve_key_path(state.uploads_dir, req.key_upload_id or "")
    if req.key_upload_id and not key_path:
        raise HTTPException(400, "密钥文件不存在或已失效, 请重新上传")
    try:
        session = state.sessions.login(
            req.host, req.port, req.username,
            password=req.password, key_path=key_path,
            timeout=req.timeout)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {
        "token": session.token,
        "user": session.user,
        "host": session.host,
        "port": session.port,
        "message": f"已登录执行机 {session.host}",
    }


@router.post("/logout")
def logout(request: Request, state: AppState = Depends(get_state)):
    """退出登录(断开该用户的执行机会话)"""
    ok = state.sessions.logout(get_session_token(request))
    return {"ok": ok}


@router.get("/status")
def status(request: Request, state: AppState = Depends(get_state)):
    """会话状态: 200 + authenticated=False 表示未登录(不触发前端 401 处理)"""
    session = state.sessions.get(get_session_token(request))
    if not session:
        return {"authenticated": False}
    return session.to_dict()
