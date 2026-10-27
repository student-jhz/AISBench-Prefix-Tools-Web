/** 应用入口: 全局状态 / 导航 / WebSocket 日志 */

import { $, $$, toast } from "./util.js";
import { api, LogSocket } from "./api.js";
import { LogConsole } from "./components.js";
import { initStep1 } from "./step1.js";
import { initStep2 } from "./step2.js";
import { initStep3 } from "./step3.js";
import { initStep4 } from "./step4.js";
import { initStep5 } from "./step5.js";
import { initLogtools } from "./logtools.js";
import { initCompare } from "./compare.js";

// 模块图启动标记: index.html 的内联 watchdog 据此判断前端是否真正启动。
// 放在模块顶层(所有 import 求值完成后) —— 任何模块加载/求值失败都到不了这里。
window.__appBooted = true;

/** 全局应用状态 */
export const G = {
  state: null,            // /api/state 聚合状态
  currentView: "step1",
  consoles: {},           // channel -> LogConsole
  // 当前浏览器(用户)选择的测试容器 —— 多用户各自选择, 互不干扰
  container: localStorage.getItem("aisbench_container") || "",
  containerInfo: null,    // 最近一次 select 返回的容器信息
  // 步骤2 表单数据(跨步骤共享 + localStorage 持久化)
  form: loadForm(),
  // 步骤5 设计器状态(前端持有, 执行时提交)
  designer: loadDesigner(),
};


function loadForm() {
  try {
    return JSON.parse(localStorage.getItem("aisbench_form") || "{}");
  } catch {
    return {};
  }
}

export function saveForm() {
  try {
    localStorage.setItem("aisbench_form", JSON.stringify(G.form));
  } catch { /* ignore */ }
}

/** 记住当前浏览器选择的容器(多用户各用各的容器) */
export function saveContainer(name) {
  G.container = name || "";
  try {
    if (name) localStorage.setItem("aisbench_container", name);
    else localStorage.removeItem("aisbench_container");
  } catch { /* ignore */ }
}


/* ============ 步骤5 设计器状态持久化 ============ */

function loadDesigner() {
  try {
    const d = JSON.parse(localStorage.getItem("aisbench_designer") || "null");
    if (d && d.params && Array.isArray(d.cases)) {
      return {
        params: d.params,                              // KV参数
        cases: d.cases,                                // 测试用例列表
        summary: d.summary || "",                      // 摘要文本
        input_lengths: d.input_lengths || [],          // 勾选的输入长度
        output_lengths: d.output_lengths || [],        // 勾选的输出长度
      };
    }
  } catch { /* ignore */ }
  return {
    params: null,         // {single_kv_cache, dp, max_request_length, repeat_rate, request_rate}
    cases: [],            // 当前用例列表
    summary: "",
    input_lengths: [],
    output_lengths: [],
  };
}

export function saveDesigner() {
  try {
    localStorage.setItem("aisbench_designer", JSON.stringify({
      params: G.designer.params,
      cases: G.designer.cases,
      summary: G.designer.summary,
      input_lengths: G.designer.input_lengths,
      output_lengths: G.designer.output_lengths,
    }));
  } catch { /* ignore */ }
}


/* ============ 导航 ============ */

const VIEW_ORDER = ["step1", "step2", "step3", "step4", "step5",
  "logtools", "compare"];

export function showView(view) {
  if (!VIEW_ORDER.includes(view)) return;
  G.currentView = view;
  $$(".view").forEach(v => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === view));
  if (view === "step3") window.syncConfigFromContainer?.();
  if (view === "step5") {
    window.refreshRuns?.();
    window.refreshExecContainer?.();
  }
  if (view === "compare") window.refreshCompareSources?.();
  if (view === "logtools") window.refreshLogtoolsStatus?.();
}


function isViewLocked(view) {
  const steps = G.state?.steps;
  if (!steps) return view !== "step1";
  if (["step2", "step3", "step4", "step5", "logtools"].includes(view)
      && !steps.ssh_connected) return true;
  const hasContainer = !!(G.containerInfo?.name || steps.container_selected);
  if (["step3", "step4", "step5"].includes(view) && !hasContainer) return true;
  return false;
}


function updateNavLocks() {
  $$(".nav-item").forEach(n => {
    const locked = isViewLocked(n.dataset.view);
    n.classList.toggle("locked", locked);
    n.title = locked ? "请先完成前置步骤" : "";
  });
}


/* ============ 状态刷新 ============ */

export async function refreshState({ silent = false } = {}) {
  try {
    G.state = await api.get("/api/state");
  } catch (e) {
    if (!silent) toast(`获取状态失败: ${e.message}`, "error");
    return;
  }
  const s = G.state;

  // 顶栏连接状态
  const pill = $("#connStatus");
  if (s.ssh.connected) {
    pill.textContent = `● 已连接 ${s.ssh.host}`;
    pill.className = "status-pill connected";
  } else {
    pill.textContent = "● 未连接";
    pill.className = "status-pill disconnected";
  }
  $("#appVersion").textContent = `v${s.version}`;

  // 步骤1 断开按钮
  const disconnectBtn = $("#sshDisconnectBtn");
  if (disconnectBtn) disconnectBtn.disabled = !s.ssh.connected;

  updateNavLocks();

  // 容器信息卡片(步骤2) —— 优先用本浏览器选择的容器
  window.updateContainerInfo?.();
  // 任务状态
  window.updateTaskStatus?.(s.tasks);
}


/* ============ WebSocket 日志 ============ */

function initLogConsoles() {
  for (const pre of $$(".log-console")) {
    const channel = pre.getAttribute("channel");
    G.consoles[channel] = new LogConsole(pre);
  }
  G.logSocket = new LogSocket(
    data => {
      const target = G.consoles[data.channel];
      if (target) target.append(data.text);
    },
    () => { /* 重连后服务端按 seq 补发历史 */ });

  $("#deployLogClearBtn").addEventListener("click", () => G.consoles.deploy?.clear());
  $("#execLogClearBtn").addEventListener("click", () => G.execConsole()?.clear());
}

/** 执行日志通道: 按当前选择的容器订阅 exec@<容器名>, 多用户日志不串扰 */
export function switchExecChannel(container) {
  const pre = $("#execLogConsole");
  if (!pre || !G.logSocket) return;
  const oldChannel = pre.getAttribute("channel");
  const newChannel = container ? `exec@${container}` : "exec";
  if (oldChannel === newChannel) return;
  if (oldChannel && G.consoles[oldChannel]) delete G.consoles[oldChannel];
  pre.setAttribute("channel", newChannel);
  pre.textContent = "";
  G.consoles[newChannel] = new LogConsole(pre);
  G.logSocket.setChannels(`deploy,${newChannel}`);
}

/** 当前执行日志控制台 */
G.execConsole = () => {
  const pre = $("#execLogConsole");
  return pre ? G.consoles[pre.getAttribute("channel")] : null;
};




/* ============ 初始化 ============ */

async function init() {
  initLogConsoles();

  initStep1();
  initStep2();
  initStep3();
  initStep4();
  initStep5();
  initLogtools();
  initCompare();

  // 侧边栏导航
  $$(".nav-item").forEach(n => {
    n.addEventListener("click", () => {
      if (n.classList.contains("locked")) {
        toast("请先完成前置步骤", "warning");
        return;
      }
      showView(n.dataset.view);
    });
  });

  // 步骤间导航按钮
  $$(".nav-next, .nav-prev").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.next || btn.dataset.prev;
      if (btn.classList.contains("nav-next") && isViewLocked(target)) {
        toast("请先完成前置步骤", "warning");
        return;
      }
      showView(target);
    });
  });

  // v2.0.2: 先渲染首屏, 再等后端状态 —— 界面出现绝不依赖 /api/state 返回。
  // 后端/SSH 慢(如正在 docker load)时页面依然立即可用, 状态稍后异步补齐。
  showView("step1");

  await refreshState();

  // 恢复上次选择的容器(多用户: 每个浏览器记住自己的容器)
  window.restoreContainerSelection?.();

  // 任务运行时高频轮询
  setInterval(() => {
    const t = G.state?.tasks;
    if (t?.deploy?.running || t?.execute?.running || t?.cleanup?.running) {
      refreshState({ silent: true });
      window.pollRunningTask?.();
    }
  }, 1200);

  // 常规状态轮询(低频)
  setInterval(() => refreshState({ silent: true }), 10000);

}

init();
