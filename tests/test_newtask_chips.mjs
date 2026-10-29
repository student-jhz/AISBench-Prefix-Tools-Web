/** 回归测试: 有历史设计时, 输入/输出长度选择栏必须随预设到达而渲染
 *
 * 现象(用户报告): 新建任务界面有概率不显示测试用例的输入/输出长度选择栏。
 * 根因: initNewTask 先以"空 presets"渲染回填的历史设计; loadPresets 成功后
 *       又因"存在历史设计"提前 return, 不再重渲染 → 选择栏渲染一次空白后
 *       永远不再刷新, 直到用户手动修改最大上下文才触发 input 事件。
 * 修复: 预设到达后无条件重渲染; 回填流程等待预设就绪后再渲染。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

// 关键前置: 浏览器里保存过历史设计(触发旧 bug 的必要条件)
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

const PRESETS = {
  input_lengths: [8192, 16384],
  output_lengths: [128, 512, 1024],
};

const routes = {
  "GET /api/designer/presets": () => PRESETS,
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
  const body = routes[key]
    ? routes[key](init.body ? JSON.parse(init.body) : null)
    : { ok: true };
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

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}
const $id = id => document.getElementById(id);
const text = id => $id(id)?.textContent || "";
const chips = id => [...$id(id).querySelectorAll("label.check-chip")];
const chipByValue = (id, v) => chips(id).find(c =>
  c.querySelector(`input[value="${v}"]`));

await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 700));

// ---- 核心回归: 选择栏必须渲染出预设长度 ----
check("输入长度选择栏渲染 2 项", chips("inputLengthChecks").length === 2);
check("输出长度选择栏渲染 3 项", chips("outputLengthChecks").length === 3);
check("历史勾选的输入长度 8192 仍选中",
  chipByValue("inputLengthChecks", 8192)?.querySelector("input")?.checked === true);
check("未勾选的输入长度 16384 不选中",
  chipByValue("inputLengthChecks", 16384)?.querySelector("input")?.checked === false);
check("历史勾选的输出长度 512 仍选中",
  chipByValue("outputLengthChecks", 512)?.querySelector("input")?.checked === true);
check("预设加载成功不报错", !text("designerMsg").includes("加载失败"));

// ---- 历史设计其余部分正常回填 ----
check("KV cache 回填", $id("kvCache").value === "198469");
check("最大上下文回填", $id("kvMaxLen").value === "131072");
check("用例表回填 1 行", $id("casesTableBody").querySelectorAll("tr").length === 1);

// ---- 每次进入新建任务视图触发(镜像加载 + 预设兜底) ----
check("enterNewTaskView 已注册", typeof window.enterNewTaskView === "function");
window.enterNewTaskView();
await new Promise(r => setTimeout(r, 400));
check("重复进入不破坏选择栏", chips("inputLengthChecks").length === 2);

const failed = results.filter(r => !r[1]);
console.log(`新建任务长度选择栏检查: ${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  for (const [name] of failed) console.error("  失败:", name);
  process.exit(1);
}
