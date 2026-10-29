# -*- coding: utf-8 -*-
"""index.html 结构守卫

背景: v3.0.0 发布后登录卡片出现在屏幕左上角 —— 根因是 index.html 中残留了一个
未闭合的 <header class="topbar">, 登录卡片与应用主体全部被嵌进这个 56px 高的
sticky 顶栏, flex 居中样式全部失效; 文件尾部还残留了重复的 </body></html>。

浏览器对这类畸形 HTML 会"尽力恢复", 页面不报 JS 错误但布局彻底崩坏 —— JS 语法
守卫检测不到。因此这里用栈式标签配平 + 关键结构断言直接把关 HTML 本身。
"""

import re
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
CSS = Path(__file__).resolve().parent.parent / "frontend" / "static" / "css" / "app.css"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}


class StructureParser(HTMLParser):
    """栈式解析: 记录配平错误与关键元素的父链"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []            # [(tag, attrs), ...]
        self.errors = []
        self.doctypes = 0
        self.login_parent = None
        self.topbar_in_appshell = []   # 每个 topbar header 是否在 appShell 内

    def handle_decl(self, decl):
        if decl.lower() == "doctype html":
            self.doctypes += 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "loginWrap":
            self.login_parent = self.stack[-1][0] if self.stack else None
        if tag == "header" and "topbar" in (attrs.get("class") or ""):
            in_app = any(a.get("id") == "appShell" for _, a in self.stack)
            self.topbar_in_appshell.append(in_app)
        if tag not in VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"闭合标签 </{tag}> 没有对应的开始标签")
            return
        open_tag, _ = self.stack.pop()
        if open_tag != tag:
            self.errors.append(f"标签配平失败: <{open_tag}> 被 </{tag}> 闭合")


def _parse():
    p = StructureParser()
    p.feed(INDEX.read_text(encoding="utf-8"))
    p.close()
    return p


def test_tags_are_balanced():
    """所有非 void 标签必须严格配平(拦截未闭合 header / 重复 </body>)"""
    p = _parse()
    leftover = [t for t, _ in p.stack]
    assert not p.errors, "HTML 结构错误: " + "; ".join(p.errors)
    assert not leftover, f"未闭合的标签: {leftover}"


def test_single_doctype_and_single_body_html_close():
    s = INDEX.read_text(encoding="utf-8")
    assert len(re.findall(r"<!DOCTYPE html>", s, re.I)) == 1
    assert s.count("</body>") == 1, "</body> 只能出现一次(v3.0.0 曾重复)"
    assert s.count("</html>") == 1, "</html> 只能出现一次(v3.0.0 曾重复)"


def test_login_wrap_is_direct_child_of_body():
    """登录界面必须是 body 的直接子元素, 否则会被其他容器破坏居中"""
    p = _parse()
    assert p.login_parent == "body", (
        f"#loginWrap 的父元素是 <{p.login_parent}>, 必须是 <body> —— "
        "v3.0.0 曾被残留的未闭合 <header class=topbar> 包裹导致登录卡片钉在左上角")


def test_topbar_only_exists_inside_app_shell():
    """顶栏只允许出现在应用主体(#appShell)内部, 登录页不得出现顶栏"""
    p = _parse()
    assert p.topbar_in_appshell, "应用主体应包含顶栏 header.topbar"
    assert all(p.topbar_in_appshell), (
        "存在不在 #appShell 内的 header.topbar —— 登录页不得有顶栏")


def test_login_css_keeps_full_screen_centering():
    """登录容器必须保持全屏 flex 居中(防止 CSS 回归)"""
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"\.login-wrap\s*\{([^}]*)\}", css)
    assert m, "缺少 .login-wrap 样式"
    block = m.group(1)
    for prop in ("display: flex", "align-items: center",
                 "justify-content: center", "min-height: 100vh"):
        assert prop in block, f".login-wrap 丢失居中关键样式: {prop}"
    assert "#loginWrap[hidden]" in css and "#appShell[hidden]" in css, (
        "缺少 #loginWrap/#appShell 的 [hidden] display:none 兜底")
