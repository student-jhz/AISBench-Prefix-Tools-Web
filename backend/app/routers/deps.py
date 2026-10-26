"""路由依赖"""

from fastapi import Request

from ..state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.app_state
