"""路径解析 - 兼容源码运行与 PyInstaller 打包两种形态

源码运行时资源根目录为仓库根(backend/ 的上级);
PyInstaller onefile 打包后为临时解包目录(sys._MEIPASS),
打包时按源码布局打入了 frontend/ 与 backend/core_code。
"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """资源根目录(静态前端 / 内置测试代码的父目录)"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).parent
    # paths.py 位于 <root>/backend/app/ 下
    return Path(__file__).resolve().parents[2]
