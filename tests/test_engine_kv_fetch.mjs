/** 引擎参数自动获取浏览器级验证
 *
 * 新建任务 → 测试用例设计 → 「从引擎日志自动获取」:
 * - 三种日志来源(宿主机文件/容器内文件/docker logs)切换正确显示对应控件;
 * - 筛选后解析值展示, 「填入参数」回填 KV cache 与最大请求长度;
 * - 无匹配时给出提示且不允许误填。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

let token = "testtoken123";
let authed = false;
let grepCalls = [];
let otherStatus = { connected: false, host: "", port: 0, username: "" };
let requestUrl = "";
let copiedText = "";
const serverOf = () => new URL(requestUrl).searchParams.get("server") || "session";

const routes = {
  "GET /api/auth/status": () => authed
    ? { authenticated: true, user: "root", host: "10.0.0.1", port: 22 }
    : { authenticated: false },
  "POST /api/auth/login": () => { authed = true;
    return { token, user: "root", host: "10.0.0.1" }; },
  "GET /api/platform/tasks": () => ({ tasks: [] }),
  "GET /api/platform/images": () => ({ images: [
    { name: "aisbench:v1", id: "abc", size: "1.2GB", created: "2026-01-01" }] }),
  "GET /api/designer/presets": () => ({
    input_lengths: [8192, 16384, 32768], output_lengths: [128, 512, 1024] }),
  "GET /api/platform/engine/other/status": () => otherStatus,
  "POST /api/platform/engine/other/connect": (body) => {
    otherStatus = { connected: true, host: body.host, port: body.port, username: body.username };
    return { ok: true, message: `已连接 ${body.host}:${body.port}`, status: otherStatus };
  },
  "POST /api/platform/engine/other/disconnect": () => {
    otherStatus = { connected: false, host: "", port: 0, username: "" };
    return { ok: true, status: otherStatus };
  },
  "GET /api/platform/engine/containers": () => (serverOf() === "other"
    ? { containers: [{ name: "vllm_other", status: "Up 3 hours", image: "vllm/vllm-openai:other" }] }
    : { containers: [{ name: "vllm_engine", status: "Up 2 hours", image: "vllm/vllm-openai:latest" }] }),
  "GET /api/platform/engine/list": () => (serverOf() === "other"
    ? { path: "/var/log", entries: [{ name: "other.log", type: "file", size: 2048 }] }
    : { path: "/root", entries: [{ name: "vllm_serve.log", type: "file", size: 1024 }] }),
  "POST /api/platform/engine/grep": (body) => {
    grepCalls.push(body);
    if (body.server === "other") {
      return {
        command: `grep -e 'GPU KV cache size' -e 'Maximum concurrency' ${body.path || ""}`,
        code: 0,
        output: "GPU KV cache size: 222,222 tokens\n"
          + "Maximum concurrency for 65,536 tokens per request: 2.00x\n",
        error: "", kv: 222222, max_len: 65536, parsed: true,
      };
    }
    if (body.path === "/root/no_match.log") {
      return { command: "grep ...", code: 1,
        output: "(未找到匹配行, 请确认选择的是 vLLM 启动日志)\n",
        error: "", kv: null, max_len: null, parsed: false };
    }
    return {
      command: `grep -e 'GPU KV cache size' -e 'Maximum concurrency' ${body.path || ""}`,
      code: 0,
      output: "GPU KV cache size: 198,469 tokens\n"
        + "Maximum concurrency for 131,072 tokens per request: 3.84x\n",
      error: "", kv: 198469, max_len: 131072, parsed: true,
    };
  },
};

globalThis.window = window;
Object.defineProperty(window.navigator, "clipboard", {
  value: { writeText: async (text) => { copiedText = text; } },
  configurable: true,
});
Object.defineProperty(globalThis, "navigator", {
  value: window.navigator,
  configurable: true,
});
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.Node = window.Node;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.fetch = (url, init = {}) => {
  const u = new URL(url, "http://localhost:8320");
  requestUrl = u.toString();
  const key = `${init.method || "GET"} ${u.pathname}`;
  const body = init.body ? JSON.parse(init.body) : null;
  const handler = routes[key] || (() => ({ ok: true }));
  const result = typeof handler === "function" ? handler(body) : handler;
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(result),
    headers: { get: () => "" },
  });
};
class FakeWS {
  constructor() {}
  addEventListener() {} close() {} send() {}
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
const $id = id => document.getElementById(id);
const modalText = () => $id("modalRoot")?.textContent || "";
const modalButtons = () => [...($id("modalRoot")?.querySelectorAll("button") || [])];
const btnByText = (t) => modalButtons().find(b => b.textContent.includes(t));

await import("../frontend/static/js/main.js");       // 运行 init(注册事件)
const { showView } = await import("../frontend/static/js/state.js");  // 同一实例
await new Promise(r => setTimeout(r, 700));
window.showView = showView;

// ---- 登录并进入新建任务 ----
$id("loginHost").value = "10.0.0.1";
$id("loginUser").value = "root";
$id("loginPassword").value = "pass";
$id("loginBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 800));
showView("newtask");
await new Promise(r => setTimeout(r, 500));

// ---- 1. 打开引擎参数获取弹窗 ----
check("自动获取按钮存在", !!$id("kvAutoFetchBtn"));
$id("kvAutoFetchBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("弹窗已打开", modalText().includes("从引擎日志获取"));
check("默认宿主机文件模式", modalText().includes("宿主机日志文件"));
check("映射提示显示",
  modalText().includes("GPU KV cache size") && modalText().includes("Maximum concurrency"));

// 宿主机模式: 不显示容器选择, 显示路径
const containerRowEl = () => [...$id("modalRoot").querySelectorAll("div")]
  .find(d => d.textContent.startsWith("引擎容器:"));
const containerHidden = () => containerRowEl()?.hidden === true;
check("host 模式隐藏容器选择", containerHidden());

// ---- 2. 宿主机文件: 填路径 → 筛选 → 填入 ----
const pathInput = $id("modalRoot").querySelector(".browser-path-row input");
pathInput.value = "/root/vllm_serve.log";
btnByText("筛选 KV cache").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));

check("筛选请求发送", grepCalls.length === 1 && grepCalls[0].mode === "host"
  && grepCalls[0].path === "/root/vllm_serve.log");
check("解析值展示", modalText().includes("198,469") && modalText().includes("131,072"));
check("命令展示", modalText().includes("grep -e 'GPU KV cache size'"));
btnByText("复制命令").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 200));
check("复制命令使用当前路径",
  copiedText === "grep -e 'GPU KV cache size' -e 'Maximum concurrency' /root/vllm_serve.log");
const fillBtn = btnByText("填入参数");
check("填入按钮可用", fillBtn && fillBtn.disabled === false);

fillBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 300));
check("KV cache 已回填", $id("kvCache").value === "198469");
check("最大请求长度已回填", $id("kvMaxLen").value === "131072");
check("回填后弹窗关闭", !modalText().includes("从引擎日志获取"));
// 最大上下文生效: 超长输入项被禁用(16384 < 131072 仍可选)
check("长度选择栏按新最大上下文重渲染",
  $id("inputLengthChecks").querySelectorAll("input").length === 3);

// ---- 3. docker logs 模式: 选容器 → 筛选 ----
$id("kvAutoFetchBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
const dockerRadio = [...$id("modalRoot").querySelectorAll("input[name=engineLogMode]")]
  .find(r => r.value === "docker_logs");
dockerRadio.checked = true;
dockerRadio.dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));

check("docker_logs 模式显示容器选择", !containerHidden());
check("docker_logs 模式隐藏路径行",
  $id("modalRoot").querySelector(".browser-path-row").hidden === true
  || $id("modalRoot").querySelector(".browser-path-row input").hidden === true);
const containerSelect = $id("modalRoot").querySelector("select");
check("容器列表已加载",
  containerSelect && containerSelect.value === "vllm_engine");

btnByText("筛选 KV cache").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("docker_logs 请求携带容器",
  grepCalls.at(-1)?.mode === "docker_logs" && grepCalls.at(-1)?.container === "vllm_engine");
check("docker_logs 解析值展示", modalText().includes("198,469"));
btnByText("填入参数").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 300));
check("docker_logs 回填成功", $id("kvCache").value === "198469");

// ---- 4. 无匹配: 提示且不允许误填 ----
$id("kvAutoFetchBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
$id("modalRoot").querySelector(".browser-path-row input").value = "/root/no_match.log";
btnByText("筛选 KV cache").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("无匹配给出提示", modalText().includes("未解析到"));
check("无匹配时填入按钮禁用", btnByText("填入参数").disabled === true);
btnByText("取消").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

// ---- 5. 其他服务器: 未连接提示 → 连接 → 路由到 other → 复制/粘贴解析 ----
$id("kvAutoFetchBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
const otherBox = () => [...$id("modalRoot").querySelectorAll(".advanced-box")]
  .find(d => d.textContent.includes("主机"));
check("默认隐藏其他服务器表单", otherBox()?.hidden === true);

const otherRadio = [...$id("modalRoot").querySelectorAll("input[name=engineLogServer]")]
  .find(r => r.value === "other");
otherRadio.checked = true;
otherRadio.dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("选择其他服务器后显示连接表单", otherBox()?.hidden === false);
check("其他服务器未连接提示", modalText().includes("未连接"));

const otherInputs = [...otherBox().querySelectorAll("input")]
  .filter(i => i.type !== "file");
otherInputs[0].value = "10.0.0.2";
otherInputs[1].value = "22";
otherInputs[2].value = "root";
otherInputs[3].value = "pass";
btnByText("连接").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 800));
check("其他服务器连接成功", modalText().includes("已连接 root@10.0.0.2:22"));

btnByText("断开").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("其他服务器可断开", modalText().includes("未连接"));

btnByText("连接").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 800));
const otherPathInput = $id("modalRoot").querySelector(".browser-path-row input");
otherPathInput.value = "/var/log/other.log";
btnByText("筛选 KV cache").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 500));
check("筛选请求路由到其他服务器",
  grepCalls.at(-1)?.server === "other" && grepCalls.at(-1)?.path === "/var/log/other.log");
check("其他服务器日志解析值展示", modalText().includes("222,222")
  && modalText().includes("65,536"));

copiedText = "";
btnByText("复制命令").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 200));
check("复制命令使用其他服务器选择的路径",
  copiedText === "grep -e 'GPU KV cache size' -e 'Maximum concurrency' /var/log/other.log");

const pasteInput = $id("modalRoot").querySelector("textarea");
pasteInput.value = "no engine parameters here";
btnByText("解析粘贴的输出").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 200));
check("无效粘贴输出提示", modalText().includes("粘贴内容中未找到"));
check("无效粘贴后填入按钮禁用", btnByText("填入参数").disabled === true);

pasteInput.value = "GPU KV cache size: 333,333 tokens\n"
  + "Maximum concurrency for 131,071 tokens per request: 2.50x\n";
btnByText("解析粘贴的输出").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 200));
check("粘贴输出解析成功", modalText().includes("333,333")
  && modalText().includes("131,071"));
check("粘贴输出解析后填入按钮可用", btnByText("填入参数").disabled === false);
btnByText("填入参数").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 300));
check("粘贴解析结果可回填", $id("kvCache").value === "333333"
  && $id("kvMaxLen").value === "131071");

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`引擎参数获取检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
