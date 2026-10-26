# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 - 生成自包含的单文件 Web 服务

用法(在仓库根目录执行):
    pip install -r backend/requirements.txt pyinstaller
    pyinstaller deploy/aisbench-web.spec --noconfirm

产物: dist/aisbench-web, 内含后端 + 前端静态资源 + 内置测试代码,
运行时只需一个可执行文件, 数据目录默认为运行目录下的 data/。
"""

import os

# spec 文件位于 deploy/ 下, 仓库根为其上一级
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    # 按源码布局打入资源, 与 app/paths.py 的解析逻辑保持一致
    (os.path.join(ROOT, "backend", "core_code"), "backend/core_code"),
    (os.path.join(ROOT, "frontend", "static"), "frontend/static"),
    (os.path.join(ROOT, "frontend", "index.html"), "frontend"),
]

a = Analysis(
    [os.path.join(ROOT, "backend", "run.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # uvicorn 通过 importlib 动态加载, PyInstaller 无法静态发现
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="aisbench-web",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
