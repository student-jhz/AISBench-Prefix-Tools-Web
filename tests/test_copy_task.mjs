/** 浏览器级验证: 任务列表/详情复制历史任务 → 基于它新建任务
 *
 * 流程: 登录 → 任务列表出现「⧉ 复制」→ 点击 → 拉取 copy-source →
 *       新建任务表单回填(名称/镜像/配置/长度选择栏/用例) → 提交 →
 *       POST 体中携带完整配置与勾选长度。
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";

const html = fs.readFileSync("frontend/index.html", "utf8")
  .replace(/__APP_VERSION__/g, "test");
const dom = new JSDOM(html, { url: "http://localhost:8320/", pretendToBeVisual: true });
const { window } = dom;

const TASK_ID = "t20261028000001abc";
const TASK = {
  id: TASK_ID, name: "平台冒烟", owner: "root", image: "aisbench:v1",
  status: "done", status_label: "已完成", phase: "", error: "",
  created_at: "2026-10-28 10:00:00", started_at: "2026-10-28 10:00:05",
  ended_at: "2026-10-28 10:05:00", queue_position: 0,
  result_summary: { csv_file: "results_x.csv", row_count: 1 },
};
const COPY_SOURCE = {
  id: TASK_ID, name: "平台冒烟", image: "aisbench:v1",
  config: {
    model_name: "ds", model_path: "/mnt/models/ds",
    host_ip: "10.0.0.1", host_port: "8000",
  },
  designer: {
    single_kv_cache: 198469, dp: 4, max_request_length: 131072,
    repeat_rate: 0.9, request_rate: 0,
    input_lengths: [8192, 16384], output_lengths: [512, 1024],
    cases: [{
      input_len: 8192, output_len: 512,
      data_num_recommended: 216, data_num_min: 108,
      concurrency_recommended: 91, concurrency_max: 91,
      repeat_rate: 0.9, dp: 4, is_custom: false,
    }],
  },
};

let submittedBody = null;
const routes = {
  "GET /api/auth/status": () => ({ authenticated: true, user: "root", host: "10.0.0.1" }),
  "POST /api/auth/login": () => ({ token: "tok", user: "root", host: "10.0.0.1", message: "ok" }),
  "GET /api/platform/tasks": () => ({ tasks: [TASK] }),
  [`GET /api/platform/tasks/${TASK_ID}`]: () => TASK,
  [`GET /api/platform/tasks/${TASK_ID}/copy-source`]: () => COPY_SOURCE,
  "GET /api/platform/images": () => ({ images: [
    { name: "aisbench:v1", id: "abc", size: "1.2GB", created: "2026-01-01" } ] }),
  "GET /api/designer/presets": () => ({
    input_lengths: [8192, 16384, 32768], output_lengths: [128, 512, 1024] }),
  "POST /api/designer/cases": body => ({ cases: body.cases, calc_details: "计算明细(mock)" }),
  "POST /api/platform/tasks": body => {
    submittedBody = body;
    return { id: "tnew0000001", status: "queued", message: "任务已提交" };
  },
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
globalThis.confirm = () => true;

const results = [];
function check(name, cond) {
  results.push([name, !!cond]);
  if (!cond) console.error("FAIL:", name);
}
const $id = id => document.getElementById(id);
const text = id => $id(id)?.textContent || "";
const chips = id => [...$id(id).querySelectorAll("label.check-chip")];
const checkedValues = id => chips(id)
  .filter(c => c.querySelector("input")?.checked)
  .map(c => Number(c.querySelector("input").value));

await import("../frontend/static/js/main.js");
await new Promise(r => setTimeout(r, 700));

// 登录(带 token 进入应用主体)
window.localStorage.setItem("aisbench_token", "tok");
$id("loginHost").value = "10.0.0.1";
$id("loginUser").value = "root";
$id("loginPassword").value = "pass";
$id("loginBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 800));
check("登录后进入应用", $id("appShell").hidden === false);

// ---- 1. 任务列表提供复制入口 ----
await new Promise(r => setTimeout(r, 500));
const copyBtn = [...$id("tasksTableBody").querySelectorAll("button")]
  .find(b => b.textContent.includes("复制"));
check("任务列表出现「复制」按钮", !!copyBtn);

// ---- 2. 点击复制 → 拉取 copy-source → 回填新建任务表单 ----
copyBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 900));

check("切换到新建任务视图", $id("view-newtask").classList.contains("active"));
check("任务名回填为副本", $id("taskNameInput").value === "平台冒烟 副本");
check("镜像回填并选中", $id("imagesTableBody").querySelector("tr.row-selected") !== null);
check("模型名称回填", $id("cfgModelName").value === "ds");
check("模型路径回填", $id("cfgModelPath").value === "/mnt/models/ds");
check("服务 IP 回填", $id("cfgHostIp").value === "10.0.0.1");
check("服务端口回填", $id("cfgHostPort").value === "8000");
check("KV cache 回填", $id("kvCache").value === "198469");
check("输入长度选择栏渲染", chips("inputLengthChecks").length === 3);
check("历史勾选输入长度回显", JSON.stringify(checkedValues("inputLengthChecks")) === "[8192,16384]");
check("历史勾选输出长度回显", JSON.stringify(checkedValues("outputLengthChecks")) === "[512,1024]");
check("用例表回填", $id("casesTableBody").querySelectorAll("tr").length === 1);
check("计算明细回填", text("calcDetails").includes("计算明细"));
check("提交区给出复制提示", text("submitMsg").includes("复制配置"));

// ---- 3. 直接提交 → 请求体携带完整复制内容 ----
$id("submitTaskBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await new Promise(r => setTimeout(r, 900));
check("提交请求已发出", submittedBody !== null);
check("提交体含副本任务名", submittedBody?.name === "平台冒烟 副本");
check("提交体含历史镜像", submittedBody?.image === "aisbench:v1");
check("提交体含测试配置", submittedBody?.config?.model_path === "/mnt/models/ds");
check("提交体含勾选输入长度", JSON.stringify(submittedBody?.designer?.input_lengths) === "[8192,16384]");
check("提交体含测试用例", submittedBody?.designer?.cases?.length === 1);

// ---- 4. 任务详情页同样提供复制入口 ----
window.showTaskDetail(TASK_ID);
await new Promise(r => setTimeout(r, 700));
const detailCopy = [...$id("taskDetailActions").querySelectorAll("button")]
  .find(b => b.textContent.includes("复制为新任务"));
check("详情页出现「复制为新任务」", !!detailCopy);

const failed = results.filter(r => !r[1]);
console.log(`复制任务流程检查: ${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
  for (const [name] of failed) console.error("  失败:", name);
  process.exit(1);
}
