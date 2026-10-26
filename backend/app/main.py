"""AISBench-Prefix-Tools-Web FastAPI 应用入口"""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .logbus import LogBus
from .paths import bundle_dir, is_frozen
from .state import AppState
from .tasks import TaskManager
from .version import APP_NAME, __version__
from .routers import (
    compare,
    config,
    deploy,
    designer,
    execute,
    files,
    logtools,
    results,
    ssh,
    system,
)


def _base_dir() -> Path:
    """项目根目录(backend/ 的上级); PyInstaller 打包后为解包目录"""
    return bundle_dir()


def _data_dir() -> Path:
    env = os.environ.get("AISBENCH_DATA_DIR")
    if env:
        return Path(env)
    if is_frozen():
        # 可执行文件: 数据放在运行目录(而非每次启动都会重建的临时解包目录)
        return Path.cwd() / "data"
    return _base_dir() / "data"


def create_app(ssh_manager=None, data_dir=None, ssh_factory=None) -> FastAPI:
    """创建应用(ssh_manager/data_dir/ssh_factory 参数用于测试注入)"""
    app = FastAPI(title=APP_NAME, version=__version__)

    # 全局状态
    state = AppState(data_dir or _data_dir(), ssh=ssh_manager,
                     ssh_factory=ssh_factory)
    bus = LogBus()
    state.tasks = TaskManager(bus, state)
    app.state.app_state = state
    app.state.logbus = bus

    # 业务异常统一转为 400
    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(request: Request, exc: RuntimeError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # API 路由
    app.include_router(system.router, prefix="/api")
    app.include_router(ssh.router, prefix="/api")
    app.include_router(ssh.fs_router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(files.uploads_router, prefix="/api")
    app.include_router(deploy.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(designer.router, prefix="/api")
    app.include_router(execute.router, prefix="/api")
    app.include_router(results.router, prefix="/api")
    app.include_router(logtools.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")

    # WebSocket: 实时日志
    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket):
        await ws.accept()
        # 客户端可通过 query 参数订阅通道(默认全部)
        channels_param = ws.query_params.get("channels", "deploy,exec")
        channels = [c.strip() for c in channels_param.split(",") if c.strip()]
        cursor = {ch: 0 for ch in channels}
        try:
            while True:
                sent = False
                for ch in channels:
                    events = bus.since(ch, cursor[ch])
                    for seq, text in events:
                        await ws.send_json({
                            "channel": ch, "seq": seq, "text": text,
                        })
                        cursor[ch] = seq
                        sent = True
                if not sent:
                    await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass

    # 静态资源与首页
    static_dir = _base_dir() / "frontend" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(_base_dir() / "frontend" / "index.html")

    return app


app = create_app()
