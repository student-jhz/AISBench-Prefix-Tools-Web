/** 应用入口: 全局状态 / 导航 / WebSocket 日志 */

import { $, $$, toast } from "./util.js";
import { api, LogSocket } from "./api.js";
import { LogConsole } from "./components.js";
import { initStep1 } from "./step1.js";
import { initStep2 } from "./step2.js";
import { initStep3 } from "./step3.js";
import { initStep4 } from "./step4.js";
import { initStep5 } from "./step5.js";
import { initStep6 } from "./step6.js";
import { initLogtools } from "./logtools.js";
import { initCompare } from "./compare.js";


/** 全局应用状态 */
export const G = {
  state: null,            // /api/state 聚合状态
  currentView: "step1",
  consoles: {},           // channel -> LogConsole
  // 步骤2 表单数据(跨步骤共享 + localStorage 持久化)
  form: loadForm(),
  // 步骤5 设计器状态(前端持有, 执行时提交)
  designer: {
    params: null,         // {single_kv_cache, dp, max_request_length, repeat_rate, request_rate}
    cases: [],            // 当前用例列表
    summary: "",
  },
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


/* ============ 导航 ============ */

const VIEW_ORDER = ["step1", "step2", "step3", "step4", "step5", "step6",
  "logtools", "compare"];

export function showView(view) {
  if (!VIEW_ORDER.includes(view)) return;
  G.currentView = view;
  $$(".view").forEach(v => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === view));
  if (view === "step6") window.refreshRuns?.();
  if (view === "compare") window.refreshCompareSources?.();
  if (view === "logtools") window.refreshLogtoolsStatus?.();
}


function isViewLocked(view) {
  const steps = G.state?.steps;
  if (!steps) return view !== "step1";
  if (["step2", "step3", "step4", "step5", "step6", "logtools"].includes(view)
      && !steps.ssh_connected) return true;
  if (view === "step6" && !steps.deployed) return true;
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

  // 部署信息卡片
  window.updateDeployInfo?.(s.deploy);
  // 任务状态
  window.updateTaskStatus?.(s.tasks);
}


/* ============ WebSocket 日志 ============ */

function initLogConsoles() {
  for (const pre of $$(".log-console")) {
    const channel = pre.getAttribute("channel");
    G.consoles[channel] = new LogConsole(pre);
  }

  new LogSocket(
    data => {
      const target = G.consoles[data.channel];
      if (target) target.append(data.text);
    },
    () => { /* 重连后服务端按 seq 补发历史 */ });

  $("#deployLogClearBtn").addEventListener("click", () => G.consoles.deploy?.clear());
  $("#execLogClearBtn").addEventListener("click", () => G.consoles.exec?.clear());
}


/* ============ 初始化 ============ */

async function init() {
  initLogConsoles();

  initStep1();
  initStep2();
  initStep3();
  initStep4();
  initStep5();
  initStep6();
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

  await refreshState();

  // 恢复持久化表单(步骤2字段)
  window.restoreStep2Form?.();

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

  showView("step1");
}

init();
