"""独立启动入口

- 源码直接运行: python backend/run.py
- PyInstaller 打包入口(见 deploy/aisbench-web.spec), 生成自包含单文件可执行
"""

import os

import uvicorn

from app.main import app
from app.paths import bundle_dir


def _log_paths() -> None:
    """启动时打印资源路径, 便于确认打包完整性"""
    core = bundle_dir() / "backend" / "core_code" / "aisbench_auto_tools_prefix"
    print(f"[aisbench-web] 资源目录: {bundle_dir()}")
    print(f"[aisbench-web] 内置测试代码: {'OK' if core.is_dir() else '缺失!'}",
          flush=True)


def main() -> None:
    _log_paths()
    port = int(os.environ.get("AISBENCH_PORT", "8320"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
