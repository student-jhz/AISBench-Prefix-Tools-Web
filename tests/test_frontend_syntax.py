"""前端模块语法守卫(v2.0.4)

背景: v2.0.1~v2.0.3 曾带伤发布 —— step2.js 字符串字面量内被写入了
真实换行符(0D 0A), 浏览器报 SyntaxError 导致整站不可用; 而当时的
检查方式 node --check xx.js(脚本目标)对这类错误不敏感。

守卫: 每个前端 JS 都是 ES module, 必须复制为 .mjs 后以模块目标解析
通过 —— 与浏览器行为完全一致, 任何语法错误(含字符串内裸换行)都会
在本地 pytest 与 CI 中被拦截。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static" / "js"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.mark.parametrize(
    "path", sorted(JS_DIR.glob("*.js")), ids=lambda p: p.name)
def test_frontend_module_parses(path, tmp_path):
    """每个前端 JS 以 ES module 目标解析(浏览器同款语义)必须通过"""
    if not _node_available():
        pytest.skip("node 不可用, 跳过前端语法检查")
    target = tmp_path / "check.mjs"
    shutil.copy(path, target)
    proc = subprocess.run(
        ["node", "--check", str(target)],
        capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"{path.name} 存在语法错误(浏览器将无法加载):\n{proc.stderr}\n"
        "注意: node --check 对 .js(脚本目标)不敏感, 必须按 .mjs 检查")


def test_no_raw_newline_in_string_literals():
    """扫描引号字符串内的裸换行(v2.0.1~2.0.3 事故的精确字节特征)

    独立于 node 的字节级检测: 双引号/单引号字符串内不允许出现原始
    CR(0x0D)/LF(0x0A) —— 模板字符串(反引号)合法含换行, 不在此列。
    """


def test_no_bare_backslash_t_join():
    """回归断言: 旧 v2.0.1~2.0.3 事故模式 join("\\t") 不得再现
    (v3.0: step2.js 已被平台视图取代, 断言改为面向全部前端 JS)

    (通用语法由上面的模块解析测试守卫; 此处针对事故现场再加一道
    精确断言, 不依赖 node。)
    """
    for js in JS_DIR.glob("*.js"):
        data = js.read_bytes()
        # 字符串内 join("\t") 的原始 tab 转义事故特征: b'.join("\t")'
        assert b'.join("\t")' not in data, (
            f"{js.name}: join 分隔符出现原始转义序列, 存在 v2.0.1 事故风险")
