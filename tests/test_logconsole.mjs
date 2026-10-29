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

// 场景5: 历史快照一次性载入(v3.0.2: 打开任务日志直接整屏展示,
// 不逐条重放; 单次渲染后直接定位到最新位置)
{
  const { pre, c } = makeConsole();
  c.setHistory("[1/6] 上传测试代码...\n[2/6] 准备执行环境...\n[4/6] 执行测试中...\n");
  const text = pre.textContent;
  check("历史快照: 内容完整",
    text.includes("[1/6]") && text.includes("[4/6]"));
  check("历史快照: 定位到底部", pre.scrollTop === pre.scrollHeight);

  // 快照后实时增量继续追加
  c.append("[6/6] 解析日志生成结果...\n");
  check("快照后: 实时增量追加", pre.textContent.includes("[6/6]"));

  // 断线重连: 再次收到完整快照时整体替换, 不重复追加
  c.setHistory("[1/6] 上传测试代码...\n[6/6] 解析日志生成结果...\n✓ 任务完成\n");
  const again = pre.textContent;
  check("重连快照: 整体替换不重复",
    again.split("[1/6]").length === 2 && again.includes("✓ 任务完成"));
}

// 场景6: 进度帧以 \n 结尾(远端按行输出进度) —— 同一条进度条的
// 连续刷新帧应原地折叠为一行, 而不是刷出成百上千行日志
{
  const { pre, c } = makeConsole();
  c.append("2026-10-29 10:00:00 - INFO - test start\n");
  c.append("  0%|          | 0/108 [00:00<?, ?it/s]\n");
  c.append(" 25%|██▌       | 27/108 [00:05<00:15, 5.4it/s]\n");
  c.append(" 50%|█████     | 54/108 [00:10<00:10, 5.4it/s]\n");
  c.append(" 75%|███████▌  | 81/108 [00:15<00:05, 5.4it/s]\n");
  c.append("100%|██████████| 108/108 [00:20<00:00, 5.4it/s]\n");
  c.append("2026-10-29 10:00:21 - INFO - test done\n");
  const lines = pre.textContent.split("\n");
  check("LF进度帧: 折叠为一行", lines.filter(l => l.includes("/108")).length === 1);
  check("LF进度帧: 保留最新帧", pre.textContent.includes("100%|██████████| 108/108"));
  check("LF进度帧: 前后日志行保留",
    pre.textContent.includes("test start") && pre.textContent.includes("test done"));
}

// 场景7: 不同总量的进度条不能互相覆盖(上一条结束后新开一条)
{
  const { pre, c } = makeConsole();
  c.append("100%|██████████| 108/108 [00:20<00:00, 5.4it/s]\n");
  c.append("  0%|          | 0/50 [00:00<?, ?it/s]\n");
  c.append(" 50%|█████     | 25/50 [00:10<00:10, 2.5it/s]\n");
  const bars = pre.textContent.split("\n").filter(l => l.includes("|"));
  check("不同总量: 两条进度条分别成行", bars.length === 2);
  check("不同总量: 第二条保留最新帧", pre.textContent.includes("25/50"));
}

// 场景8: 非进度类重复行不折叠(避免吞掉真实日志)
{
  const { pre, c } = makeConsole();
  c.append("2026-10-29 - INFO - retry 1/1\n");
  c.append("2026-10-29 - INFO - retry 1/1\n");
  check("非进度重复行: 不折叠",
    pre.textContent.split("retry 1/1").length === 3);
}

// 场景9: 上传进度按行输出(上传中: n/m + 文件名)也折叠
{
  const { pre, c } = makeConsole();
  c.append("  共 100 个文件待上传\n");
  c.append("  上传中: 1/100\n");
  c.append("  上传中: 2/100\n");
  c.append("  上传中: 3/100\n");
  const ups = pre.textContent.split("\n").filter(l => l.includes("上传中:"));
  check("上传进度按行输出: 折叠为一行", ups.length === 1);
  check("上传进度: 保留最新", ups[0]?.includes("3/100"));
}

// 场景10: 进度帧尾部内容不同(如文件名)不折叠 —— 那是真实信息
{
  const { pre, c } = makeConsole();
  c.append("  上传中: 1/100  a.py\n");
  c.append("  上传中: 2/100  b.py\n");
  const ups = pre.textContent.split("\n").filter(l => l.includes("上传中:"));
  check("进度帧尾部不同: 不折叠", ups.length === 2);
}

process.exit(failed ? 1 : 0);
