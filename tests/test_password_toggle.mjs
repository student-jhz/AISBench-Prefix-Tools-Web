/** 密码显示/隐藏切换的浏览器级验证(v2.0.5)
 *
 * 用户报告"步骤1密码显隐不生效"。jsdom 全量加载 main.js 模块图后
 * 真实派发 click, 断言:
 * - input.type 在 password/text 间切换(功能本身);
 * - 按钮图标 👁/🙈、title、aria-pressed 同步变化(可感知反馈,
 *   旧版图标不变导致"看不出是否生效")。
 * 同时覆盖远程日志工具的其他服务器密码框。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

const stateByPath = {
  "/api/state": {
    version: "test", ssh: { connected: false },
    deploy: null, container_busy: false, container_running: false,
    tasks: {
      deploy: { running: false, status: "idle" },
      execute: { running: false, status: "idle" },
      cleanup: { running: false, status: "idle" },
    },
    steps: { ssh_connected: false, container_selected: false, container_running: false },
  },
  "/api/ssh/status": { connected: false, remembered: null },
  "/api/logtools/status": { session: { connected: false }, other: { connected: false } },
  "/api/designer/presets": { input_lengths: [], output_lengths: [], grep_command: "" },
  "/api/config/defaults": { defaults: {}, dataset_path_default: "" },
  "/api/results": { runs: [] },
  "/api/compare/sources": { runs: [] },
};

globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.Node = window.Node;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.fetch = url => {
  const u = new URL(url, "http://localhost:8320");
  const body = stateByPath[u.pathname] ?? { ok: true };
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    headers: { get: () => "" },
  });
};
class FakeWS {
  constructor(u) { this.url = u; }
  addEventListener() {} removeEventListener() {} send() {} close() {}
}
globalThis.WebSocket = FakeWS;
globalThis.XMLHttpRequest = window.XMLHttpRequest;
globalThis.location = window.location;
globalThis.history = window.history;
globalThis.confirm = () => true;

const errors = [];
window.addEventListener("error", e => errors.push(e.message));

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}

await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 600));

// ---- 步骤1: SSH 密码切换 ----
const pw = document.getElementById("sshPassword");
const btn = document.getElementById("sshPwToggle");
check("步骤1初始为密码框", pw.type === "password");
check("步骤1初始图标为 👁", btn.textContent === "👁");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check("步骤1点击后明文显示", pw.type === "text");
check("步骤1图标切换为 🙈", btn.textContent === "🙈");
check("步骤1提示更新为隐藏", btn.title === "隐藏密码");
check("步骤1aria状态已按下", btn.getAttribute("aria-pressed") === "true");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check("步骤1再次点击恢复密文", pw.type === "password");
check("步骤1图标恢复 👁", btn.textContent === "👁");
check("步骤1aria状态弹起", btn.getAttribute("aria-pressed") === "false");

// ---- 日志工具: 其他服务器密码切换 ----
const ltPw = document.getElementById("ltPassword");
const ltBtn = document.getElementById("ltPwToggle");
check("日志工具密码框存在", !!ltPw && !!ltBtn);
if (ltPw && ltBtn) {
  ltBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("日志工具点击后明文显示", ltPw.type === "text");
  check("日志工具图标切换为 🙈", ltBtn.textContent === "🙈");
  ltBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("日志工具再次点击恢复密文", ltPw.type === "password");
}

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`密码切换检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
