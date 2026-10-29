/** 长度预设加载失败 → 可见提示 + 再次进入新建任务视图自动重试恢复
 *
 * 修复点: 旧实现 catch 后静默吞掉错误, 选择栏区域永久空白且无任何提示、
 * 无重试途径。新实现: 失败给出可见错误与占位文案, presetsPromise 置空,
 * enterNewTaskView 时自动重试, 成功后清除错误并渲染选择栏。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

window.localStorage.setItem("aisbench_designer", JSON.stringify({
  params: {
    single_kv_cache: 198469, dp: 4, max_request_length: 131072,
    repeat_rate: 0.9, request_rate: 0,
  },
  cases: [{
    input_len: 8192, output_len: 512,
    data_num_recommended: 216, data_num_min: 108,
    concurrency_recommended: 91, concurrency_max: 91,
    repeat_rate: 0.9, dp: 4, is_custom: false,
  }],
  summary: "",
  input_lengths: [8192],
  output_lengths: [512],
}));

let presetsOk = false;          // 先失败, 手动恢复后重试
let presetsCalls = 0;
const routes = {
  "GET /api/designer/presets": () => {
    presetsCalls++;
    if (!presetsOk) throw new Error("backend boom");
    return { input_lengths: [8192, 16384], output_lengths: [128, 512, 1024] };
  },
  "POST /api/designer/cases": body => ({ cases: body.cases, calc_details: "ok" }),
};

globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.Node = window.Node;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.fetch = (url, init = {}) => {
  const u = new URL(url, "http://localhost:8320");
  const key = `${init.method || "GET"} ${u.pathname}`;
  let body = { ok: true }, status = 200;
  try {
    body = routes[key]
      ? routes[key](init.body ? JSON.parse(init.body) : null)
      : { ok: true };
  } catch (e) {
    body = { detail: e.message };
    status = 500;
  }
  return Promise.resolve({
    ok: status < 400, status,
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

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}
const $id = id => document.getElementById(id);
const text = id => $id(id)?.textContent || "";
const chips = id => [...$id(id).querySelectorAll("label.check-chip")];

await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 700));

// ---- 1. 首次加载失败: 有可见提示, 不再静默空白 ----
check("预设请求已发出", presetsCalls >= 1);
check("失败给出可见错误", text("designerMsg").includes("加载失败"));
check("失败时选择栏显示占位文案",
  text("inputLengthChecks").includes("暂无可选长度"));

// ---- 2. 后端恢复 + 再次进入视图 → 自动重试并渲染 ----
presetsOk = true;
window.enterNewTaskView();
await new Promise(r => setTimeout(r, 600));
check("重试后预设再次请求", presetsCalls >= 2);
check("重试后输入选择栏渲染 2 项", chips("inputLengthChecks").length === 2);
check("重试后输出选择栏渲染 3 项", chips("outputLengthChecks").length === 3);
check("成功后清除失败提示", !text("designerMsg").includes("加载失败"));
check("历史勾选保留",
  chips("inputLengthChecks").some(c =>
    c.querySelector('input[value="8192"]')?.checked));

const failed = results.filter(r => !r[1]);
console.log(`长度预设重试检查: ${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  for (const [name] of failed) console.error("  失败:", name);
  process.exit(1);
}
