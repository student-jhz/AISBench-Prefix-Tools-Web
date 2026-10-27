/** 步骤 5: 执行测试 —— 在当前选择的容器内执行 */

import { $, $$, el, fmtLen, fmtNum, fmtTime, toast, confirmDialog } from "./util.js";
import { api } from "./api.js";
import { G, refreshState } from "./main.js";
import { pollDeployStatus, updateDeployTaskUI, openContainerManage } from "./step2.js";


let lastExecuteRunning = false;
let lastRunId = null;


export function initStep5() {
  $("#refreshCommandsBtn").addEventListener("click", refreshCommands);
  $("#executeStartBtn").addEventListener("click", startExecute);
  $("#executeCancelBtn").addEventListener("click", cancelExecute);
  $("#cleanupBtn").addEventListener("click", () => openContainerManage());
  $("#execRefreshBusyBtn").addEventListener("click", () => refreshExecContainer(true));
  $("#refreshRunsBtn").addEventListener("click", refreshRuns);

  // main.js 状态刷新钩子
  window.updateTaskStatus = tasks => {
    updateDeployTaskUI(tasks);
    updateExecuteTaskUI(tasks);
  };
  window.pollRunningTask = async () => {
    await pollDeployStatus();
    await pollExecuteStatus();
  };
  window.refreshRuns = refreshRuns;
  window.refreshExecContainer = refreshExecContainer;
  // 页面刷新后恢复显示最近一次测试结果(与上游桌面版"结果摘要"面板一致)
  restoreLastResult();
  refreshExecContainer();
}


/* ============ 命令预览 ============ */

async function refreshCommands() {
  if (!G.designer.params || !G.designer.cases.length) {
    toast("请先在步骤 4 生成测试用例", "warning");
    return;
  }
  try {
    const data = await api.post("/api/designer/script", {
      ...G.designer.params,
      cases: G.designer.cases,
    });
    $("#execSummary").textContent = data.summary;
    $("#commandsPreview").textContent =
      data.commands.join("\n\n") + "\n\n# ===== run_tests.sh =====\n" + data.script;
  } catch (err) {
    toast(err.message, "error", 6000);
  }
}


/* ============ 执行 / 取消 ============ */

async function startExecute() {
  if (!G.containerInfo?.name) {
    toast("请先在步骤 2 选择或创建测试容器", "warning");
    return;
  }
  if (G.containerInfo.busy) {
    toast("该容器正在执行测试, 请等待完成或在步骤 2 新建容器", "warning");
    return;
  }
  if (!G.designer.params || !G.designer.cases.length) {
    toast("请先在步骤 4 生成测试用例", "warning");
    return;
  }
  const btn = $("#executeStartBtn");
  btn.disabled = true;
  btn.textContent = "执行中...";
  $("#executeCancelBtn").disabled = false;
  $("#executePhase").textContent = "⏳ 任务启动中...";
  $("#resultsCard").hidden = true;
  lastExecuteRunning = true;

  try {
    const data = await api.post("/api/execute/start", {
      designer: {
        ...G.designer.params,
        cases: G.designer.cases,
      },
      run_name: $("#runName").value.trim() || null,
      container: G.container,
    });
    lastRunId = data.run_id;
    toast(`测试任务已启动 (run_id: ${data.run_id})`, "success");
    await refreshState({ silent: true });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "▶️ 执行测试 → 生成CSV";
    $("#executeCancelBtn").disabled = true;
    $("#executePhase").textContent = "";
    lastExecuteRunning = false;
    toast(err.message, "error", 6000);
  }
}


async function cancelExecute() {
  try {
    const data = await api.post(
      `/api/execute/cancel?container=${encodeURIComponent(G.container || "")}`);
    toast(data.ok ? "已发送取消请求" : "任务未在运行", data.ok ? "info" : "warning");
  } catch (err) {
    toast(err.message, "error");
  }
}


/* ============ 执行状态轮询 ============ */

async function pollExecuteStatus() {
  let st;
  try {
    st = await api.get(
      `/api/execute/status?container=${encodeURIComponent(G.container || "")}`,
      { silent: true });
  } catch {
    return;
  }
  $("#executePhase").textContent = st.running
    ? `⏳ [${st.status}] ${st.phase || "执行中"}...`
    : "";

  if (lastExecuteRunning && !st.running) {
    if (st.status === "done") {
      showResult(st.result);
      toast("测试执行完成!", "success");
    } else if (st.status === "error") {
      toast(`执行失败: ${st.error}`, "error", 8000);
      if (st.result?.run_id) showResult(st.result);
    } else if (st.status === "cancelled") {
      toast("执行已取消", "info");
    }
    await refreshState({ silent: true });
    await refreshRuns();
    refreshExecContainer();
  }
  lastExecuteRunning = st.running;
}


async function restoreLastResult() {
  try {
    const st = await api.get(
      `/api/execute/status?container=${encodeURIComponent(G.container || "")}`,
      { silent: true });
    if (!st.running && st.result?.run_id && st.result.rows?.length) {
      showResult(st.result);
    }
  } catch { /* ignore */ }
}


/* ============ 执行容器状态(含测试执行感知) ============ */

async function refreshExecContainer(force = false) {
  const grid = $("#execContainerGrid");
  const banner = $("#execBusyBanner");
  const info = G.containerInfo;
  if (!info?.name) {
    grid.innerHTML = "";
    grid.append(el("div", { class: "info-item" },
      el("div", { class: "info-label" }, "提示"),
      el("div", { class: "info-value" }, "请先在步骤 2 选择或创建测试容器")));
    banner.hidden = true;
    return;
  }

  grid.innerHTML = "";
  grid.append(
    el("div", { class: "info-item" },
      el("div", { class: "info-label" }, "容器名称"),
      el("div", { class: "info-value" }, info.name)),
    el("div", { class: "info-item" },
      el("div", { class: "info-label" }, "镜像"),
      el("div", { class: "info-value" }, info.image || "-")),
    el("div", { class: "info-item" },
      el("div", { class: "info-label" }, "状态"),
      el("div", { class: "info-value" },
        info.running ? "运行中" : `已停止 (${info.state || "-"})`)),
    el("div", { class: "info-item" },
      el("div", { class: "info-label" }, "测试执行"),
      el("div", { class: "info-value" }, info.busy ? "测试执行中" : "空闲")));

  // 实时查询 busy 状态: 让用户在执行前感知容器是否被占用
  try {
    const st = await api.get(
      `/api/containers/busy?name=${encodeURIComponent(info.name)}`, { silent: true });
    G.containerInfo = { ...G.containerInfo, ...st, name: info.name };
    // 状态回填后重渲染信息网格(否则网格仍是查询前的旧状态)
    grid.innerHTML = "";
    grid.append(
      el("div", { class: "info-item" },
        el("div", { class: "info-label" }, "容器名称"),
        el("div", { class: "info-value" }, info.name)),
      el("div", { class: "info-item" },
        el("div", { class: "info-label" }, "镜像"),
        el("div", { class: "info-value" }, info.image || "-")),
      el("div", { class: "info-item" },
        el("div", { class: "info-label" }, "状态"),
        el("div", { class: "info-value" },
          st.running ? "运行中" : `已停止 (${st.state || "-"})`)),
      el("div", { class: "info-item" },
        el("div", { class: "info-label" }, "测试执行"),
        el("div", { class: "info-value" }, st.busy ? "测试执行中" : "空闲")));
    const btn = $("#executeStartBtn");
    if (st.busy) {
      banner.hidden = false;
      banner.className = "busy-banner warn";
      banner.textContent =
        `⏳ 容器 ${info.name} 正在执行测试` +
        `${st.busy_detail ? `: ${st.busy_detail}` : ""}` +
        " —— 请等待完成, 或在步骤 2 新建容器执行新任务。";
      btn.disabled = true;
    } else if (!st.running) {
      banner.hidden = false;
      banner.className = "busy-banner error";
      banner.textContent = `⚠ 容器 ${info.name} 未运行, 请先在步骤 2 启动容器。`;
      btn.disabled = true;
    } else {
      banner.hidden = true;
      if (!G.state?.tasks?.execute?.running) btn.disabled = false;
    }
  } catch {
    banner.hidden = true;
  }
  if (force) toast("容器状态已刷新", "info", 1500);
}


function updateExecuteTaskUI(tasks) {
  const t = tasks?.execute;
  const running = !!t?.running;
  const btn = $("#executeStartBtn");
  btn.disabled = running;
  btn.textContent = running ? "执行中..." : "▶️ 执行测试 → 生成CSV";
  $("#executeCancelBtn").disabled = !running;
  if (!running) $("#executePhase").textContent = "";
}


/* ============ 结果展示 ============ */

const RESULT_COLUMNS = [
  ["input_len", v => fmtLen(v)],
  ["output_len", v => fmtLen(v)],
  ["total_req", v => fmtNum(v, 0)],
  ["max_cc", v => fmtNum(v, 0)],
  ["cc", v => fmtNum(v, 0)],
  ["input_token_throughput", v => fmtNum(v)],
  ["output_throughput", v => fmtNum(v)],
  ["TTFT_avg", v => fmtNum(v)],
  ["TPOT_avg", v => fmtNum(v)],
  ["qps", v => fmtNum(v)],
  ["hbm_hit_rate", v => `${fmtNum(v, 1)}%`],
  ["external_hit_rate", v => `${fmtNum(v, 1)}%`],
];


function showResult(result) {
  if (!result || !result.run_id) return;
  lastRunId = result.run_id;

  const card = $("#resultsCard");
  card.hidden = false;
  $("#resultsInfo").textContent =
    `Run: ${result.run_id}  |  日志目录: ${result.log_dir_name || "-"}  |  ` +
    `CSV: ${result.csv_file || "(无)"}  |  结果行数: ${result.row_count ?? 0}`;

  // 下载按钮
  const actions = $("#resultActions");
  actions.innerHTML = "";
  if (result.csv_file) {
    actions.append(el("button", {
      class: "btn btn-outline btn-sm",
      onclick: () => api.download(
        `/api/results/${encodeURIComponent(result.run_id)}/download` +
        `?file=${encodeURIComponent(result.csv_file)}`),
    }, "⬇ 下载CSV"));
  }
  actions.append(el("button", {
    class: "btn btn-outline btn-sm",
    onclick: () => api.download(
      `/api/results/${encodeURIComponent(result.run_id)}/download-zip`),
  }, "🗜 打包下载"));

  // 结果表
  const tbody = $("#resultsTableBody");
  tbody.innerHTML = "";
  const rows = result.rows || [];
  if (!rows.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "12" }, "未提取到结果数据, 请检查执行日志")));
    return;
  }
  for (const row of rows) {
    const tr = el("tr");
    for (const [field, fmt] of RESULT_COLUMNS) {
      tr.append(el("td", {}, fmt(row[field])));
    }
    tbody.append(tr);
  }
}


/** 从结果历史查看某次run的详细结果表 */
async function viewRun(run) {
  try {
    const data = await api.get(
      `/api/results/${encodeURIComponent(run.run_id)}`, { silent: true });
    showResult({
      run_id: data.run_id,
      csv_file: data.csv_file,
      rows: data.rows || [],
      row_count: (data.rows || []).length,
    });
    $("#resultsCard").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    toast(err.message, "error", 6000);
  }
}


/* ============ 结果历史 ============ */

async function refreshRuns() {
  const tbody = $("#runsTableBody");
  let runs;
  try {
    runs = (await api.get("/api/results", { silent: true })).runs;
  } catch {
    return;
  }
  tbody.innerHTML = "";
  if (!runs.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "5" }, "暂无测试结果")));
    return;
  }
  for (const run of runs) {
    const tr = el("tr");
    tr.append(
      el("td", {}, run.run_id),
      el("td", {}, fmtTime(run.created_at)),
      el("td", {}, String(run.file_count)),
      el("td", {}, run.csv_files.length ? run.csv_files.join(", ") : "-"),
      el("td", {}, runActions(run)),
    );
    tbody.append(tr);
  }
}


function runActions(run) {
  const box = el("div", { class: "cases-actions" });
  if (run.csv_files.length) {
    box.append(el("button", {
      class: "btn btn-ghost btn-sm",
      title: "查看结果表",
      onclick: () => viewRun(run),
    }, "👁"));
  }
  if (run.csv_files.length) {
    box.append(el("button", {
      class: "btn btn-ghost btn-sm",
      title: "下载CSV",
      onclick: () => api.download(
        `/api/results/${encodeURIComponent(run.run_id)}/download` +
        `?file=${encodeURIComponent(run.csv_files[0])}`),
    }, "CSV"));
  }
  box.append(
    el("button", {
      class: "btn btn-ghost btn-sm",
      title: "打包下载",
      onclick: () => api.download(
        `/api/results/${encodeURIComponent(run.run_id)}/download-zip`),
    }, "ZIP"),
    el("button", {
      class: "btn btn-ghost btn-sm",
      title: "删除",
      onclick: async () => {
        const ok = await confirmDialog(`删除结果 ${run.run_id}? 此操作不可恢复。`,
          { title: "删除结果", danger: true, okText: "删除" });
        if (!ok) return;
        try {
          await api.del(`/api/results/${encodeURIComponent(run.run_id)}`);
          toast("结果已删除", "success");
          refreshRuns();
        } catch (err) {
          toast(err.message, "error");
        }
      },
    }, "🗑"),
  );
  return box;
}
