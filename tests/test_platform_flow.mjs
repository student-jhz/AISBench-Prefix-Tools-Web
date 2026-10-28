/** v3.0 平台流程浏览器级验证
 *
 * 在 jsdom 中加载完整前端模块图, 验证:
 * 1. 登录门禁: 未登录只见登录界面;
 * 2. 登录成功 → 进入应用主体, 任务列表加载;
 * 3. 任务列表渲染状态徽标/结果列(容器概念不出现);
 * 4. 查看任务详情: 状态/日志通道/结果表;
 * 5. 任务从执行中 → 完成后列表自动刷新(轮询)。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

const TASK_RUNNING = {
  id: "t20261028000001abc", name: "冒烟任务", owner: "root",
  image: "aisbench:v1", status: "running", status_label: "执行测试",
  phase: "执行测试脚本", error: "", created_at: "2026-10-28 10:00:00",
  started_at: "2026-10-28 10:00:05", ended_at: "", queue_position: 0,
  result_summary: { csv_file: "", row_count: 0 },
};
const TASK_DONE = {
  ...TASK_RUNNING, status: "done", status_label: "已完成",
  phase: "", ended_at: "2026-10-28 10:05:00",
  result_summary: { csv_file: "results_x.csv", row_count: 1 },
  result: {
    run_id: "t20261028000001abc", log_dir_name: "test_logs_x",
    csv_file: "results_x.csv", row_count: 1,
    rows: [{ input_len: 8192, output_len: 512, qps: 5, ttft_avg: 200,
      tpot_avg: 25, e2e_avg: 9000, concurrency: 91, data_num: 216,
      total_throughput: 13134, hbm_hit_rate: 85.32,
      external_hit_rate: 92.15, repeat_rate: 0.9 }],
  },
};

let token = "testtoken123";
let tasks = [TASK_RUNNING];
let authed = false;

const routes = {
  "GET /api/auth/status": () => authed
    ? { authenticated: true, user: "root", host: "10.0.0.1", port: 22,
        created_at: "2026-10-28 09:00:00" }
    : { authenticated: false },
  "POST /api/auth/login": () => { authed = true;
    return { token, user: "root", host: "10.0.0.1", port: 22, message: "已登录执行机 10.0.0.1" }; },
  "GET /api/platform/tasks": () => ({ tasks }),
  "GET /api/platform/tasks/t20261028000001abc": () => tasks[0],
  "GET /api/platform/images": () => ({ images: [
    { name: "aisbench:v1", id: "abc", size: "1.2GB", created: "2026-01-01" } ] }),
  "GET /api/designer/presets": () => ({
    input_lengths: [8192, 16384], output_lengths: [128, 512, 1024] }),
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
  constructor(u) { this.url = u; this.channels = []; }
  addEventListener() {} removeEventListener() {} send() {} close() {}
  setChannels(c) { this.channels.push(c); }
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
const $id = id => document.getElementById(id);
const text = id => $id(id)?.textContent || "";

const { G } = await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 700));

// ---- 1. 登录门禁 ----
check("未登录: 登录界面可见", $id("loginWrap").hidden === false);
check("未登录: 应用主体隐藏", $id("appShell").hidden === true);

// ---- 2. 登录 ----
$id("loginHost").value = "10.0.0.1";
$id("loginUser").value = "root";
$id("loginPassword").value = "pass";
$id("loginBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 800));

check("登录后: 登录界面隐藏", $id("loginWrap").hidden === true);
check("登录后: 应用主体可见", $id("appShell").hidden === false);
check("顶栏显示用户@主机", text("connStatus").includes("root@10.0.0.1"));
check("token 已保存", localStorage.getItem("aisbench_token") === token);

// ---- 3. 任务列表 ----
await new Promise(r => setTimeout(r, 600));
const rows = $id("tasksTableBody").querySelectorAll("tr");
check("任务列表渲染 1 行", rows.length === 1);
check("任务名显示", text("tasksTableBody").includes("冒烟任务"));
check("执行中状态显示", text("tasksTableBody").includes("执行测试"));
check("容器概念不出现", !text("tasksTableBody").includes("容器"));

// ---- 4. 任务详情 ----
window.showTaskDetail("t20261028000001abc");
await new Promise(r => setTimeout(r, 600));
check("详情标题显示", text("taskDetailTitle").includes("冒烟任务"));
check("详情状态徽标", text("taskDetailStatus").includes("执行测试"));
check("日志通道已订阅", G.logSocket && G.logSocket.channels.includes("task@t20261028000001abc"));

// ---- 5. 任务完成 → 列表自动刷新(2s 轮询) ----
tasks = [TASK_DONE];
await new Promise(r => setTimeout(r, 3000));
check("完成后列表显示已完成", text("tasksTableBody").includes("已完成"));
check("完成后结果列显示", text("tasksTableBody").includes("1 条结果"));

// 详情页同步刷新
check("详情状态同步为已完成", text("taskDetailStatus").includes("已完成"));
check("详情结果表渲染", $id("taskResultBody").querySelectorAll("tr").length === 1);
check("结果表含数据", text("taskResultBody").includes("8,192"));

check("无运行时错误", errors.length === 0);
if (errors.length) console.error("捕获错误:", errors);

const failed = results.filter(([, ok]) => !ok);
console.log(`平台流程检查: ${results.length - failed.length}/${results.length} 通过`);
process.exit(failed.length ? 1 : 0);
