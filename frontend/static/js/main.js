/** 应用入口: 登录门禁 / 平台视图路由 / 任务日志 WebSocket */

import { $, $$, toast } from "./util.js";
import { api, auth, LogSocket } from "./api.js";
import { LogConsole } from "./components.js";
import { initLogin } from "./login.js";
import { initTasks } from "./tasks.js";
import { initNewTask } from "./newtask.js";
import { initCompare } from "./compare.js";

// 模块图启动标记: index.html 内联 watchdog 据此判断前端是否真正启动
window.__appBooted = true;

/** 全局应用状态 */
export const G = {
  session: null,          // {authenticated, user, host, ...}
  currentView: "tasks",
  consoles: {},           // channel -> LogConsole
  designer: loadDesigner(),
};


function loadDesigner() {
  try {
    const d = JSON.parse(localStorage.getItem("aisbench_designer") || "null");
    if (d && d.params && Array.isArray(d.cases)) {
      return {
        params: d.params, cases: d.cases, summary: d.summary || "",
        input_lengths: d.input_lengths || [], output_lengths: d.output_lengths || [],
      };
    }
  } catch { /* ignore */ }
  return { params: null, cases: [], summary: "", input_lengths: [], output_lengths: [] };
}

export function saveDesigner() {
  try {
    localStorage.setItem("aisbench_designer", JSON.stringify({
      params: G.designer.params, cases: G.designer.cases,
      summary: G.designer.summary,
      input_lengths: G.designer.input_lengths,
      output_lengths: G.designer.output_lengths,
    }));
  } catch { /* ignore */ }
}


/* ============ 登录门禁: 登录前只有登录界面 ============ */

export function showLogin() {
  G.session = null;
  $("#loginWrap").hidden = false;
  $("#appShell").hidden = true;
}

export async function enterApp() {
  $("#loginWrap").hidden = true;
  $("#appShell").hidden = false;
  await refreshSession();
  window.refreshTasks?.();
  showView("tasks");
}

export async function refreshSession() {
  try {
    G.session = await api.get("/api/auth/status");
  } catch {
    G.session = { authenticated: false };
  }
  const s = G.session;
  const pill = $("#connStatus");
  if (s.authenticated) {
    pill.textContent = `● ${s.user}@${s.host}`;
    pill.className = "status-pill connected";
  } else {
    pill.textContent = "● 未登录";
    pill.className = "status-pill disconnected";
  }
}

// 会话失效(401): 全局退回登录界面, 不再留在半可用状态
window.addEventListener("aisbench:unauthorized", () => {
  auth.setToken("");
  showLogin();
  toast("登录已失效, 请重新登录", "warning", 6000);
});


/* ============ 导航 ============ */

const VIEW_ORDER = ["tasks", "taskdetail", "newtask", "compare"];

export function showView(view) {
  if (!VIEW_ORDER.includes(view)) return;
  G.currentView = view;
  $$(".view").forEach(v => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $$(".nav-item").forEach(n =>
    n.classList.toggle("active", n.dataset.view === view));
  if (view === "newtask") window.enterNewTaskView?.();
  if (view === "compare") window.refreshCompareSources?.();
}


/* ============ 任务日志 WebSocket ============ */

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
  $("#taskLogClearBtn")?.addEventListener("click", () => G.consoles.task?.clear());
}

/** 订阅某个任务的日志通道 */
export function subscribeTaskLog(taskId) {
  const pre = $("#taskLogConsole");
  if (!pre || !G.logSocket) return;
  const oldChannel = pre.getAttribute("channel");
  const newChannel = taskId ? `task@${taskId}` : "task";
  if (oldChannel === newChannel) return;
  if (oldChannel && G.consoles[oldChannel]) delete G.consoles[oldChannel];
  pre.setAttribute("channel", newChannel);
  pre.textContent = "";
  G.consoles[newChannel] = new LogConsole(pre);
  G.logSocket.setChannels(newChannel);
}


/* ============ 初始化 ============ */

async function init() {
  initLogConsoles();
  initLogin();
  initTasks();
  initNewTask();
  initCompare();

  // 侧边栏导航
  $$(".nav-item").forEach(n => {
    n.addEventListener("click", () => showView(n.dataset.view));
  });

  // 登录态检查: 有 token 验证, 无 token 直接登录界面
  if (auth.getToken()) {
    const s = await api.get("/api/auth/status").catch(() => null);
    if (s?.authenticated) {
      await enterApp();
      return;
    }
    auth.setToken("");
  }
  showLogin();
}

init();
