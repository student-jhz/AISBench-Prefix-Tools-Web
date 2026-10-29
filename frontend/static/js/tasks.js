/** 任务列表 / 任务详情 —— 用户只看到任务与结果, 不出现容器概念 */

import { $, el, setMsg, toast, confirmDialog } from "./util.js";
import { api } from "./api.js";
import { G, showView, subscribeTaskLog } from "./main.js";

const ACTIVE_STATUSES = new Set(["queued", "preparing", "running", "collecting"]);

let pollingTimer = null;       // 有活动任务时高频轮询
let currentDetailId = null;    // 当前详情页任务


export function initTasks() {
  $("#tasksRefreshBtn").addEventListener("click", () => refreshTasks(true));
  $("#tasksNewBtn").addEventListener("click", () => showView("newtask"));
  window.refreshTasks = refreshTasks;
  window.showTaskDetail = showTaskDetail;
}


/* ============ 任务列表 ============ */

export async function refreshTasks(force = false) {
  let data;
  try {
    data = await api.get("/api/platform/tasks", { silent: true });
  } catch (err) {
    setMsg("#tasksMsg", `任务列表加载失败: ${err.message}`, "error");
    return;
  }
  const tasks = data.tasks || [];
  renderTasks(tasks);
  setMsg("#tasksMsg",
    `共 ${tasks.length} 个任务` +
    (tasks.some(t => ACTIVE_STATUSES.has(t.status)) ? " · 有任务进行中" : ""),
    "muted");

  // 活动任务期间 2s 轮询, 全部结束后降频/停止
  schedulePolling(tasks.some(t => ACTIVE_STATUSES.has(t.status)));
  // 详情页打开时同步刷新
  if (currentDetailId) refreshDetailQuiet();
  if (force) toast("任务列表已刷新", "info", 1500);
}

function schedulePolling(hasActive) {
  if (hasActive) {
    if (!pollingTimer) {
      pollingTimer = setInterval(() => refreshTasks(), 2000);
    }
  } else if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

function renderTasks(tasks) {
  const tbody = $("#tasksTableBody");
  tbody.innerHTML = "";
  if (!tasks.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "7" }, "暂无任务, 点击「新建任务」提交第一个测试")));
    return;
  }
  for (const t of tasks) {
    const tr = el("tr", { class: ACTIVE_STATUSES.has(t.status) ? "row-busy" : "" });
    tr.append(
      el("td", { class: "mono" }, t.name || t.id),
      el("td", {}, t.owner || "-"),
      el("td", {}, statusBadge(t)),
      el("td", { class: "mono" }, shortImage(t.image)),
      el("td", {}, resultCell(t)),
      el("td", {}, t.created_at || "-"),
      el("td", {}, rowActions(t)),
    );
    tbody.append(tr);
  }
}

function statusBadge(t) {
  const cls = {
    done: "tag-ok", error: "tag-err", cancelled: "tag-muted",
    interrupted: "tag-muted",
  }[t.status] || "tag-warn";
  let label = t.status_label || t.status;
  if (t.status === "queued" && t.queue_position > 0) label += ` (#${t.queue_position})`;
  return el("span", {
    class: `tag ${cls}`,
    title: t.phase || t.error || "",
  }, label);
}

function resultCell(t) {
  const rs = t.result_summary || {};
  if (t.status === "done") return `${rs.row_count ?? 0} 条结果`;
  if (t.status === "error") return el("span", { class: "tag tag-err" }, "失败");
  return "-";
}

function shortImage(img) {
  if (!img) return "-";
  const repo = img.split(":")[0];
  return repo.length > 30 ? repo.slice(0, 28) + "…" : repo;
}

function rowActions(t) {
  const box = el("div", { class: "cases-actions" });
  box.append(el("button", {
    class: "btn btn-ghost btn-sm", title: "查看详情",
    onclick: () => showTaskDetail(t.id),
  }, "👁"));
  box.append(el("button", {
    class: "btn btn-ghost btn-sm", title: "复制此任务的配置新建任务",
    onclick: () => copyTask(t.id),
  }, "⧉ 复制"));
  if (ACTIVE_STATUSES.has(t.status)) {
    box.append(el("button", {
      class: "btn btn-outline btn-sm", title: "取消任务",
      onclick: () => cancelTask(t.id),
    }, "✹ 取消"));
  } else {
    box.append(el("button", {
      class: "btn btn-ghost btn-sm", title: "删除任务",
      onclick: () => deleteTask(t),
    }, "🗑"));
  }
  return box;
}

async function cancelTask(id) {
  const ok = await confirmDialog(`取消任务 ${id}?`, {
    title: "取消任务", okText: "取消任务" });
  if (!ok) return;
  try {
    await api.post(`/api/platform/tasks/${id}/cancel`);
    toast("已发送取消请求", "info");
    refreshTasks();
  } catch (err) { toast(err.message, "error"); }
}

/** 复制历史任务 → 基于它回填"新建任务"表单 */
async function copyTask(id) {
  try {
    const src = await api.get(
      `/api/platform/tasks/${encodeURIComponent(id)}/copy-source`);
    // 同步前缀立即回填名称/镜像/配置, 再切换视图避免用户看到空表单
    const filling = window.prefillNewTask?.(src);
    showView("newtask");
    await filling;
  } catch (err) {
    toast(`复制任务失败: ${err.message}`, "error", 6000);
  }
}

async function deleteTask(t) {
  const ok = await confirmDialog(
    `删除任务 ${t.name || t.id}?\n其结果数据将一并删除, 此操作不可恢复。`,
    { title: "删除任务", danger: true, okText: "删除" });
  if (!ok) return;
  try {
    await api.del(`/api/platform/tasks/${t.id}`);
    toast("任务已删除", "success");
    refreshTasks();
  } catch (err) { toast(err.message, "error"); }
}


/* ============ 任务详情 ============ */

export async function showTaskDetail(id) {
  currentDetailId = id;
  $("#taskDetailTitle").textContent = "任务详情";
  subscribeTaskLog(id);
  showView("taskdetail");
  await refreshDetail();
}

async function refreshDetailQuiet() {
  if (currentDetailId) await refreshDetail(true).catch(() => {});
}

async function refreshDetail(quiet = false) {
  const id = currentDetailId;
  if (!id) return;
  let t;
  try {
    t = await api.get(`/api/platform/tasks/${encodeURIComponent(id)}`,
      { silent: true });
  } catch (err) {
    if (!quiet) toast(`任务加载失败: ${err.message}`, "error");
    return;
  }

  $("#taskDetailTitle").textContent = `任务: ${t.name || t.id}`;
  $("#taskDetailDesc").textContent =
    `提交人 ${t.owner} · 镜像 ${t.image} · 创建于 ${t.created_at}`;

  renderDetailStatus(t);
  renderDetailActions(t);
  renderDetailError(t);
  renderDetailResult(t);

  // 活动任务: 详情页自身也周期刷新(用户停留在详情页时)
  if (ACTIVE_STATUSES.has(t.status) && !detailTimer) {
    detailTimer = setInterval(() => refreshDetailQuiet(), 2000);
  } else if (!ACTIVE_STATUSES.has(t.status) && detailTimer) {
    clearInterval(detailTimer);
    detailTimer = null;
    window.refreshTasks?.();   // 结束时刷新列表
  }
}

let detailTimer = null;

function renderDetailStatus(t) {
  const box = $("#taskDetailStatus");
  box.innerHTML = "";
  box.append(
    statusBadge(t),
    el("span", { class: "inline-msg muted", style: "margin-left:10px" },
      ACTIVE_STATUSES.has(t.status)
        ? (t.phase ? `当前阶段: ${t.phase}` : "处理中...")
        : (t.ended_at ? `结束于 ${t.ended_at}` : "")),
  );
}

function renderDetailActions(t) {
  const box = $("#taskDetailActions");
  box.innerHTML = "";
  box.append(el("button", {
    class: "btn btn-outline", onclick: () => copyTask(t.id),
  }, "⧉ 复制为新任务"));
  if (ACTIVE_STATUSES.has(t.status)) {
    box.append(el("button", {
      class: "btn btn-outline", onclick: () => cancelTask(t.id),
    }, "✹ 取消任务"));
  }
  const rs = t.result_summary || {};
  if (t.status === "done" && rs.csv_file) {
    box.append(el("button", {
      class: "btn btn-outline",
      onclick: () => api.download(
        `/api/results/${encodeURIComponent(t.id)}/download` +
        `?file=${encodeURIComponent(rs.csv_file)}`),
    }, "⬇ 下载 CSV"));
    box.append(el("button", {
      class: "btn btn-outline",
      onclick: () => api.download(
        `/api/results/${encodeURIComponent(t.id)}/download-zip`),
    }, "🗜 打包下载"));
  }
}

function renderDetailError(t) {
  const box = $("#taskDetailError");
  if (t.error && t.status !== "done") {
    box.textContent = `⚠ ${t.error}`;
    box.className = "inline-msg error";
  } else {
    box.textContent = "";
  }
}

function renderDetailResult(t) {
  const card = $("#taskResultCard");
  const rows = t.result?.rows || [];
  if (t.status !== "done" || !rows.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const tbody = $("#taskResultBody");
  tbody.innerHTML = "";
  for (const row of rows) {
    const tr = el("tr");
    for (const key of ["input_len", "output_len", "qps", "ttft_avg",
      "tpot_avg", "e2e_avg", "concurrency", "data_num", "total_throughput",
      "hbm_hit_rate", "external_hit_rate", "repeat_rate"]) {
      tr.append(el("td", {}, formatCell(row[key])));
    }
    tbody.append(tr);
  }
}

function formatCell(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (Number.isFinite(n) && String(v) !== "" &&
      (String(v).includes(".") || Math.abs(n) >= 1000)) {
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }
  return String(v);
}
