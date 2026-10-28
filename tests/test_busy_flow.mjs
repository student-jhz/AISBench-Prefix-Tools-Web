/** 容器 busy 状态展示与自动消除的浏览器级验证(v2.0.5)
 *
 * 用户报告: 测试任务结束后界面仍显示"容器正在执行测试"。
 * 本测试在 jsdom 中完整加载前端模块图, 模拟:
 * 1. 已选择容器 + /busy 返回"本服务任务执行中" → 卡片/横幅正确显示;
 * 2. 任务结束(注册表迁移出 running)后, /busy 返回空闲 →
 *    busy 轮询自动消除横幅(无需手动刷新);
 * 3. 全程无 JS 运行时错误(曾发现 st 变量作用域 bug 导致卡片刷新崩溃)。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

const CONTAINER = "aisbench_flow_box";

// busy 状态可动态切换: 任务结束前后 /busy 返回不同数据
let busyState = {
  name: CONTAINER,
  running: true,
  state: "running",
  image: "aisbench:v1",
  busy: true,
  busy_source: "registry",
  executing_here: true,
  process_detected: false,
  busy_detail: "",
  task: {
    container: CONTAINER, status: "running", phase: "执行测试脚本",
    run_id: "run_flow", started_at: "2026-10-28 10:00:00",
    ended_at: "", script_running: true, error: "",
  },
};

const routes = {
  "GET /api/state": () => ({
    version: "test",
    ssh: { connected: true, host: "10.0.0.1", port: 22, username: "root" },
    deploy: { container_name: CONTAINER, image_name: "aisbench:v1" },
    container_busy: busyState.busy,
    container_running: true,
    tasks: {
      deploy: { running: false, status: "idle" },
      execute: { running: busyState.executing_here, status: busyState.task.status },
      cleanup: { running: false, status: "idle" },
    },
    steps: { ssh_connected: true, container_selected: true, container_running: true },
  }),
  "GET /api/ssh/status": () => ({ connected: true, remembered: null }),
  "GET /api/logtools/status": () => ({ session: { connected: true }, other: { connected: false } }),
  "GET /api/designer/presets": () => ({ input_lengths: [], output_lengths: [], grep_command: "" }),
  "GET /api/config/defaults": () => ({ defaults: {}, dataset_path_default: "" }),
  "GET /api/results": () => ({ runs: [] }),
  "GET /api/compare/sources": () => ({ runs: [] }),
  "GET /api/containers/busy": () => busyState,
  "POST /api/containers/select": () => ({
    selected: true,
    busy: busyState.busy,
    busy_detail: busyState.busy_detail,
    executing_here: busyState.executing_here,
    process_detected: busyState.process_detected,
    busy_source: busyState.busy_source,
    task: busyState.task,
    info: {
      name: CONTAINER, image: "aisbench:v1", state: "running", running: true,
      mounts: [], code_host_path: "/tmp/x", code_mount_path: "/benchmark/x",
      model_mounts: [], model_path_hint: "",
    },
    deploy: { container_name: CONTAINER },
  }),
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
  const body = routes[key] ? routes[key]() : { ok: true };
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

// 模拟该浏览器上次选择了容器(restoreContainerSelection 会重新 select)
localStorage.setItem("aisbench_container", CONTAINER);

const errors = [];
window.addEventListener("error", e => errors.push(e.message));

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}
const text = sel => document.querySelector(sel)?.textContent || "";

await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 800));

// ---- 任务执行中: 卡片显示"测试执行中" ----
check("无运行时错误(加载+恢复选择)", errors.length === 0);
check("步骤2卡片已渲染容器名", text("#currentContainerGrid").includes(CONTAINER));
check("步骤2显示测试执行中", text("#currentContainerGrid").includes("测试执行中"));
check("步骤2横幅提示执行中", text("#currentBusyBanner").includes("正在执行测试"));
check("步骤5卡片显示执行中", text("#execContainerGrid").includes("测试执行中"));
check("步骤5横幅提示执行中", text("#execBusyBanner").includes("正在执行测试"));

// ---- 任务结束: 注册表迁移出 running, /busy 返回空闲 ----
busyState = {
  ...busyState,
  busy: false, executing_here: false, process_detected: false,
  busy_source: "",
  task: {
    ...busyState.task, status: "done", phase: "",
    ended_at: "2026-10-28 10:05:00", script_running: false,
  },
};

// busy 轮询周期 5s: 等待自动刷新(无需任何用户操作)
await new Promise(r => setTimeout(r, 5600));

check("任务结束后无新增运行时错误", errors.length === 0);
check("步骤2卡片显示空闲", text("#currentContainerGrid").includes("空闲"));
check("步骤2横幅已自动消除", document.querySelector("#currentBusyBanner").hidden === true);
check("步骤5卡片显示空闲", text("#execContainerGrid").includes("空闲"));
check("步骤5横幅已自动消除", document.querySelector("#execBusyBanner").hidden === true);
check("执行按钮恢复可用", document.querySelector("#executeStartBtn").disabled === false);

if (errors.length) console.error("捕获错误:", errors);
const failed = results.filter(([, ok]) => !ok);
console.log(`busy 流程检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
