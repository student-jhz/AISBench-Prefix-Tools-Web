/** 回归测试: 入口 main.js 带版本参数(?v=)时, 密码切换必须仍然生效
 *
 * 真实故障(v3.0.0): index.html 加载 `/static/js/main.js?v=版本`, 而
 * login/tasks/newtask 又 `import "./main.js"`(不带版本)。浏览器按完整 URL
 * 缓存 ES module, 于是 main.js 被实例化两份 → initLogin() 执行两次 →
 * 密码按钮挂两个 click 监听器:
 *   第 1 个: password → text
 *   第 2 个: text → password
 * 最终肉眼看到"点击后依然隐藏"。
 *
 * 本测试刻意用带 query 的 URL 导入 main.js(与 index.html 行为一致)。
 * 若有模块反向 import "./main.js", 会再实例化一份不带 query 的 main.js,
 * 双监听器复现, 本测试失败。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

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
  constructor() {}
  addEventListener() {} removeEventListener() {} send() {} close() {}
  setChannels() {}
}
globalThis.WebSocket = FakeWS;
globalThis.XMLHttpRequest = window.XMLHttpRequest;
globalThis.location = window.location;
globalThis.history = window.history;

const errors = [];
window.addEventListener("error", e => errors.push(e.message));

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}

// 关键: 与 index.html 一样, 以带版本参数的 URL 导入入口模块
const entryUrl = pathToFileURL("frontend/static/js/main.js").href + "?v=test";
await import(entryUrl);
await new Promise(r => setTimeout(r, 600));

const pw = document.getElementById("loginPassword");
const btn = document.getElementById("loginPwToggle");

// ---- 结构守卫: 任何模块都不得反向 import 入口 main.js ----
// (反向 import 在 "?v=" 入口下必然产生第二份 main.js 实例)
const jsFiles = fs.readdirSync("frontend/static/js", { withFileTypes: true })
  .filter(d => d.isFile() && d.name.endsWith(".js"))
  .map(d => `frontend/static/js/${d.name}`);
const offenders = jsFiles.filter(f =>
  /from\s+"\.\/main\.js"/.test(fs.readFileSync(f, "utf8")));
check("没有模块反向 import ./main.js", offenders.length === 0);
if (offenders.length) console.error("反向 import main.js 的模块:", offenders);

check("初始为密文", pw.type === "password");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 100));
check("真实入口 URL 下点击后显示明文", pw.type === "text");
check("图标切换为 🙈", btn.textContent === "🙈");
check("aria-pressed 为 true", btn.getAttribute("aria-pressed") === "true");

btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 100));
check("再次点击恢复密文", pw.type === "password");
check("图标恢复 👁", btn.textContent === "👁");
check("aria-pressed 为 false", btn.getAttribute("aria-pressed") === "false");

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`版本化入口密码切换检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
