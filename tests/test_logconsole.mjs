// 模拟浏览器环境, 验证 LogConsole 对 PTY \r\n 输出的渲染(v2.0 修复验证)
globalThis.document = {
  createElement: () => ({
    style: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, append() {}, setAttribute() {},
  }),
};

const { LogConsole } = await import("../frontend/static/js/components.js");

function makeConsole() {
  const pre = {
    _text: "", scrollTop: 0, clientHeight: 100, scrollHeight: 0,
    set textContent(v) { this._text = v; this.scrollHeight = v.split("\n").length * 20; },
    get textContent() { return this._text; },
  };
  return { pre, c: new LogConsole(pre) };
}

let failed = 0;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failed++;
}

// 场景1: PTY 输出 \r\n —— 旧版 bug 的直接复现(每行内容被清空, 只滚不动)
{
  const { pre, c } = makeConsole();
  c.append("[2/4] 上传脚本到容器并执行...\r\n");
  c.append("预检查通过: MODEL_PATH=/mnt/models/ds\r\n");
  c.append("测试 #1 成功\r\n");
  c.append("所有测试完成！\r\n");
  const text = pre.textContent;
  check("PTY CRLF: 脚本内容可见", text.includes("预检查通过") && text.includes("所有测试完成"));
  check("PTY CRLF: 无空行堆积", !text.split("\n").every(l => l === ""));
}

// 场景2: \r\n 跨 chunk 边界('\r' 与 '\n' 分属两次推送)
{
  const { pre, c } = makeConsole();
  c.append("line1\r");
  c.append("\nline2\r");
  c.append("\nline3\r\n");
  const text = pre.textContent;
  check("跨chunk CRLF: 内容完整",
    text.includes("line1") && text.includes("line2") && text.includes("line3"));
}

// 场景3: 单独 \r 进度条刷新(保留最后一段, 不新增行)
{
  const { pre, c } = makeConsole();
  c.append("上传中: 10MB / 100MB (10%)");
  c.append("\r上传中: 50MB / 100MB (50%)");
  c.append("\r上传中: 99MB / 100MB (99%)");
  const text = pre.textContent;
  check("进度条: 只保留最后状态", text.includes("(99%)") && !text.includes("(10%)"));
  check("进度条: 单行刷新不换行", text.split("\n").length === 1);
}

// 场景4: 普通 \n 输出(后端任务日志)
{
  const { pre, c } = makeConsole();
  c.append("[1/5] 检查Docker环境...\n  ✓ Docker version 24\n\n");
  const text = pre.textContent;
  check("普通LF: 行结构正确", text.split("\n").length >= 3 && text.includes("Docker version"));
}

process.exit(failed ? 1 : 0);
