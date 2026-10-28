/** v3.0 登录界面: 密码显示/隐藏切换的浏览器级验证
 *
 * 在 jsdom 中加载 main.js 模块图后真实派发 click, 断言:
 * - input.type 在 password/text 间切换;
 * - 按钮图标 👁/🙈、title、aria-pressed 同步变化(可感知反馈)。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

const routes = {
  "GET /api/auth/status": () => ({ authenticated: false }),
  "GET /api/designer/presets": () => ({ input_lengths: [], output_lengths: [] }),
};
globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.Node = window.Node;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.fetch = (url, init = {}) => {
  const u = new URL(url, "http://localhost:8320");
  const body = routes[`${init.method || "GET"} ${u.pathname}`] ?? { ok: true };
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

// ---- 登录界面: 未登录时唯一可见 ----
check("未登录: 登录界面可见", document.getElementById("loginWrap").hidden === false);
check("未登录: 应用主体隐藏", document.getElementById("appShell").hidden === true);

// ---- 登录密码切换 ----
const pw = document.getElementById("loginPassword");
const btn = document.getElementById("loginPwToggle");
check("登录密码框初始为密文", pw.type === "password");
check("登录切换按钮初始图标 👁", btn.textContent === "👁");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check("点击后明文显示", pw.type === "text");
check("图标切换为 🙈", btn.textContent === "🙈");
check("title 更新为隐藏密码", btn.title === "隐藏密码");
check("aria-pressed 为 true", btn.getAttribute("aria-pressed") === "true");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
check("再次点击恢复密文", pw.type === "password");
check("图标恢复 👁", btn.textContent === "👁");
check("aria-pressed 为 false", btn.getAttribute("aria-pressed") === "false");

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`登录界面检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
