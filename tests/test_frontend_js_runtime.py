"""前端 JS 运行时测试(pytest 包装, 使 node 脚本进入 CI)

语法守卫(test_frontend_syntax)只能保证模块可解析; 这里的 .mjs 脚本
用 jsdom 真实加载 main.js 模块图并派发事件, 验证交互行为本身。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
RUNTIME_SCRIPTS = [
    "test_bootwatchdog.mjs",
    "test_logconsole.mjs",
    "test_password_toggle.mjs",
    "test_platform_flow.mjs",
]


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.parametrize("script", RUNTIME_SCRIPTS, ids=lambda s: s)
def test_frontend_runtime_script(script):
    """在 jsdom 中加载前端模块图并执行行为断言"""
    if not _node_available():
        pytest.skip("node 不可用, 跳过前端运行时测试")
    path = TESTS_DIR / script
    assert path.is_file(), f"缺失测试脚本: {path}"
    proc = subprocess.run(
        ["node", str(path)],
        cwd=TESTS_DIR.parent, capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, (
        f"{script} 失败:\n{proc.stdout}\n{proc.stderr}")
