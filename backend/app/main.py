"""AISBench-Prefix-Tools-Web FastAPI 应用入口"""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .logbus import LogBus
from .paths import bundle_dir, is_frozen
from .platform.scheduler import TaskScheduler
from .platform.sessions import SessionRegistry
from .platform.tasks import TaskStore
from .state import AppState
from .tasks import TaskManager
from .version import APP_NAME, __version__
from .routers import (
    auth,
    compare,
    config,
    containers,
    deploy,
    designer,
    execute,
    files,
    logtools,
    platform,
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

    # v3.0 平台层: 登录会话 + 任务队列调度(容器对用户隐藏)
    state.sessions = SessionRegistry(state)
    state.platform_tasks = TaskStore(state.data_dir / "platform")
    core_code = bundle_dir() / "backend" / "core_code" / "aisbench_auto_tools_prefix"
    state.scheduler = TaskScheduler(
        bus, state.platform_tasks, state.sessions,
        state.results_dir, core_code)
    state.scheduler.start()

    # 恢复上次会话: 后台自动重连SSH并恢复部署信息(如有)
    state.restore_session()
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
    app.include_router(containers.router, prefix="/api")
    app.include_router(deploy.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(designer.router, prefix="/api")
    app.include_router(execute.router, prefix="/api")
    app.include_router(results.router, prefix="/api")
    app.include_router(logtools.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(platform.router, prefix="/api")

    # WebSocket: 实时日志
    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket):
        await ws.accept()
        # 客户端可通过 query 参数订阅通道(默认全部)
        channels_param = ws.query_params.get("channels", "deploy,exec")
        channels = [c.strip() for c in channels_param.split(",") if c.strip()]
        cursor = {ch: 0 for ch in channels}
        replayed = {ch: False for ch in channels}
        try:
            while True:
                sent = False
                for ch in channels:
                    events = bus.since(ch, cursor[ch])
                    if not events:
                        continue
                    if not replayed[ch]:
                        # 首批事件: 合并为单条历史快照(history=true)一次性
                        # 下发。前端整屏渲染一次并定位到最新, 避免"打开任务
                        # 日志从头逐条重放"的观感; 断线重连时快照整体替换
                        # 控制台内容, 也不会重复追加已显示的日志。
                        await ws.send_json({
                            "channel": ch, "seq": events[-1][0],
                            "text": "".join(text for _, text in events),
                            "history": True,
                        })
                        replayed[ch] = True
                    else:
                        for seq, text in events:
                            await ws.send_json({
                                "channel": ch, "seq": seq, "text": text,
                            })
                    cursor[ch] = events[-1][0]
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
        @app.get("/", include_in_schema=False)
        async def index():
            # 入口页: 注入版本号作为静态资源缓存键(?v=), 并以 no-cache 提供。
            # 升级部署后浏览器立即拿到新入口, 引用新版本化 URL 的 JS/CSS,
            # 绝不会与旧缓存的同名文件混用(文件重命名/删除时尤为关键)。
            html = (_base_dir() / "frontend" / "index.html").read_text(
                encoding="utf-8")
            html = html.replace("__APP_VERSION__", __version__)
            return HTMLResponse(
                html,
                headers={
                    "Cache-Control": "no-cache, must-revalidate",
                    "ETag": f'W/"index-{__version__}"',
                },
            )

        # StaticFiles 自带 ETag/Last-Modified 协商缓存; 入口页引用带 ?v=
        # 的版本化 URL, 版本不变时命中缓存, 升级后 URL 变化自动取新文件。
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
