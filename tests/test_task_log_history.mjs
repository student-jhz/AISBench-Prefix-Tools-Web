/** 任务日志历史快照浏览器级验证(v3.0.2)
 *
 * 服务端 ws 首批事件以 history=true 快照下发; 前端 LogConsole
 * 整屏一次性渲染而非逐条重放; 断线重连收到快照时整体替换不重复。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.Node = window.Node;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.fetch = () => Promise.resolve({
  ok: true, status: 200,
  json: () => Promise.resolve({ authenticated: false }),
  headers: { get: () => "" },
});

class FakeWS {
  constructor(url) { this.url = url; }
  close() {}
}
globalThis.WebSocket = FakeWS;
globalThis.location = window.location;
globalThis.history = window.history;

const errors = [];
window.addEventListener("error", e => errors.push(e.message));

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}
const $id = id => document.getElementById(id);
const text = id => $id(id)?.textContent || "";

const { G, subscribeTaskLog } = await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 500));

// ---- 1. 订阅任务日志通道 ----
subscribeTaskLog("t20261029000001aa");
const channel = "task@t20261029000001aa";
check("日志通道已切换", $id("taskLogConsole").getAttribute("channel") === channel);
check("WS 订阅该通道", G.logSocket.channels === channel);

const push = msg => G.logSocket.ws.onmessage({ data: JSON.stringify(msg) });

// ---- 2. 打开任务: 服务端历史快照一次性整屏渲染 ----
push({ channel, seq: 5, history: true,
  text: "任务 冒烟任务 开始执行\n[1/6] 上传测试代码...\n[4/6] 执行测试中...\n" });
const snapText = text("taskLogConsole");
check("快照: 完整内容直接显示",
  snapText.includes("任务 冒烟任务") && snapText.includes("[4/6]"));
check("快照: 一次性渲染无逐条重放", G.consoles[channel].lines.length >= 3);

// ---- 3. 快照之后: 实时增量逐条追加 ----
push({ channel, seq: 6, text: "[6/6] 解析日志生成结果...\n" });
check("实时增量追加", text("taskLogConsole").includes("[6/6]"));

// ---- 4. 断线重连: 再次收到完整快照时整体替换(不重复) ----
push({ channel, seq: 7, history: true,
  text: "任务 冒烟任务 开始执行\n[1/6] 上传测试代码...\n[4/6] 执行测试中...\n"
    + "[6/6] 解析日志生成结果...\n✓ 任务完成!\n" });
const afterReconnect = text("taskLogConsole");
check("重连快照: 不重复追加",
  afterReconnect.split("任务 冒烟任务").length === 2);
check("重连快照: 保留最新内容", afterReconnect.includes("✓ 任务完成!"));

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`任务日志快照检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
