/** 结果对比分析 */

import { $, $$, el, fmtBytes, setMsg, toast } from "./util.js";
import { api } from "./api.js";
import { drawBarChart } from "./components.js";


const SERIES_COLORS = { a: "#2563eb", b: "#f59e0b" };

let compareData = null;   // /api/compare/load 响应
let computeBody = null;   // 当前 compute/export 请求体


export function initCompare() {
  // 来源类型切换
  for (const side of ["A", "B"]) {
    $(`#cmpType${side}`).addEventListener("change", () => updatePickVisibility(side));
    $(`.cmp-pick[data-side="${side}"]`).addEventListener("click",
      () => $(`#cmpUpload${side}`).click());
    $(`#cmpUpload${side}`).addEventListener("change", async e => {
      const file = e.target.files[0];
      if (file) await uploadCompareCsv(side, file);
      e.target.value = "";
    });
    updatePickVisibility(side);
  }

  $("#cmpLoadBtn").addEventListener("click", loadComparison);
  $("#cmpAllMatches").addEventListener("change", e => {
    $$("#cmpMatchTableBody input[type=checkbox]").forEach(c => {
      c.checked = e.target.checked;
    });
  });
  $("#cmpComputeBtn").addEventListener("click", computeComparison);
  $("#cmpExportBtn").addEventListener("click", exportComparison);

  window.refreshCompareSources = refreshSources;
  refreshSources();
}


function updatePickVisibility(side) {
  const isUpload = $(`#cmpType${side}`).value === "upload";
  $(`.cmp-pick[data-side="${side}"]`).hidden = !isUpload;
  $(`#cmpFile${side}`).hidden = isUpload;
}


/* ============ 数据源列表 ============ */

async function refreshSources() {
  let runs = [];
  let uploads = [];
  try {
    runs = (await api.get("/api/results", { silent: true })).runs || [];
  } catch { /* 未部署时无结果历史 */ }
  try {
    const items = (await api.get("/api/uploads", { silent: true })).items || [];
    uploads = items.filter(i => i.kind === "compare-csv" && !i.error);
  } catch { /* ignore */ }

  const options = [];
  for (const run of runs) {
    for (const file of run.csv_files) {
      options.push({
        value: JSON.stringify({ type: "result", run_id: run.run_id, file }),
        label: `${run.run_id} / ${file}`,
      });
    }
  }
  for (const u of uploads) {
    options.push({
      value: JSON.stringify({ type: "upload", upload_id: u.id }),
      label: `上传: ${u.filename} (${u.rows}行)`,
    });
  }

  for (const side of ["A", "B"]) {
    const select = $(`#cmpFile${side}`);
    const previous = select.value;
    select.innerHTML = "";
    if (!options.length) {
      select.append(el("option", { value: "" }, "(暂无可选数据源)"));
      continue;
    }
    for (const opt of options) {
      select.append(el("option", { value: opt.value }, opt.label));
    }
    // 恢复之前的选择
    if (previous && options.some(o => o.value === previous)) {
      select.value = previous;
    }
  }
}


function sourceOf(side) {
  const type = $(`#cmpType${side}`).value;
  if (type === "upload") {
    const id = selectValue(`#cmpFile${side}`, "upload_id");
    if (!id) {
      toast(`请先上传或选择对比${side === "A" ? "基准 A" : "对比 B"}的CSV文件`, "warning");
      return null;
    }
    return { type: "upload", upload_id: id };
  }
  const value = selectValue(`#cmpFile${side}`, "result");
  if (!value) {
    toast(`请先选择对比${side === "A" ? "基准 A" : "对比 B"}的结果文件`, "warning");
    return null;
  }
  return value;
}


function selectValue(sel, expectType) {
  const raw = $(sel).value;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed.type === expectType ? parsed : null;
  } catch {
    return null;
  }
}


async function uploadCompareCsv(side, file) {
  toast(`正在上传 ${file.name}...`, "info", 1500);
  try {
    const data = await api.upload("/api/upload/compare-csv", file);
    if (!data.ok) {
      toast(`上传失败: ${data.error}`, "error", 6000);
      return;
    }
    toast(`CSV 上传成功 (${data.rows} 行)`, "success");
    await refreshSources();
    const select = $(`#cmpFile${side}`);
    const target = JSON.stringify({ type: "upload", upload_id: data.id });
    if ([...select.options].some(o => o.value === target)) {
      select.value = target;
    }
  } catch (err) {
    toast(`上传失败: ${err.message}`, "error", 6000);
  }
}


/* ============ 加载与匹配 ============ */

async function loadComparison() {
  const a = sourceOf("A");
  const b = sourceOf("B");
  if (!a || !b) return;

  const btn = $("#cmpLoadBtn");
  btn.disabled = true;
  setMsg("#cmpLoadMsg", "加载中...", "warn");
  try {
    const data = await api.post("/api/compare/load", { a, b });
    compareData = data;
    computeBody = null;
    $("#cmpExportBtn").disabled = true;
    $("#cmpResults").innerHTML = "";
    renderMatchCard(data);
    setMsg("#cmpLoadMsg",
      `A: ${data.a.label} (${data.a.rows}行) · B: ${data.b.label} (${data.b.rows}行) · 匹配 ${data.matches.length} 对`,
      "ok");
  } catch (err) {
    setMsg("#cmpLoadMsg", err.message, "error");
  } finally {
    btn.disabled = false;
  }
}


function renderMatchCard(data) {
  const card = $("#cmpMatchCard");
  card.hidden = false;
  $("#cmpAllMatches").checked = true;

  // 匹配用例表
  const tbody = $("#cmpMatchTableBody");
  tbody.innerHTML = "";
  if (!data.matches.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "6" }, "没有可匹配的用例(输入长度不一致)")));
  }
  for (const match of data.matches) {
    tbody.append(el("tr", {},
      el("td", {}, el("input", {
        type: "checkbox",
        value: String(match.index),
        checked: true,
      })),
      el("td", {}, match.label),
      el("td", {}, `${match.input_a} → ${match.output_a}`),
      el("td", {}, `${match.input_b} → ${match.output_b}`),
      el("td", {}, `${match.total_req_a} / ${match.total_req_b}`),
      el("td", {}, match.exact ? "精确匹配" : "输入匹配"),
    ));
  }

  // 指标分组
  const groups = $("#cmpMetricsGroups");
  groups.innerHTML = "";
  const byCategory = new Map();
  for (const metric of data.metric_fields) {
    if (!byCategory.has(metric.category)) byCategory.set(metric.category, []);
    byCategory.get(metric.category).push(metric);
  }
  for (const [category, metrics] of byCategory) {
    const row = el("div", { class: "checkbox-row" });
    for (const metric of metrics) {
      const checkbox = el("input", {
        type: "checkbox",
        value: metric.field,
      });
      checkbox.checked = data.default_metrics.includes(metric.field);
      row.append(el("label", { class: "check-chip" },
        checkbox,
        el("span", {}, `${metric.name}${metric.unit ? ` (${metric.unit})` : ""}`)));
    }
    groups.append(
      el("div", { class: "metrics-group-title" }, category),
      row,
    );
  }
}


/* ============ 计算与展示 ============ */

async function computeComparison() {
  if (!compareData) {
    toast("请先加载数据源", "warning");
    return;
  }
  const metrics = $$("#cmpMetricsGroups input:checked").map(i => i.value);
  const matchIndices = $$("#cmpMatchTableBody input:checked")
    .map(i => Number(i.value));
  if (!matchIndices.length) {
    toast("请至少勾选一个匹配用例", "warning");
    return;
  }
  if (!metrics.length) {
    toast("请至少勾选一个对比指标", "warning");
    return;
  }

  computeBody = { a: sourceOf("A"), b: sourceOf("B"), metrics, match_indices: matchIndices };
  if (!computeBody.a || !computeBody.b) return;

  const btn = $("#cmpComputeBtn");
  btn.disabled = true;
  btn.textContent = "计算中...";
  try {
    const data = await api.post("/api/compare/compute", computeBody);
    renderResults(data, metrics);
    $("#cmpExportBtn").disabled = false;
    toast(`对比完成: ${data.match_count} 个用例 × ${metrics.length} 个指标`, "success");
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "📊 开始对比";
  }
}


function renderResults(data, metrics) {
  const root = $("#cmpResults");
  root.innerHTML = "";

  // 未匹配提示
  if (data.unmatched_a || data.unmatched_b) {
    root.append(el("div", { class: "warning-banner" },
      `⚠ 未匹配用例: A 侧 ${data.unmatched_a} 行, B 侧 ${data.unmatched_b} 行(已忽略)`));
  }

  for (const field of metrics) {
    const table = data.tables[field];
    if (!table) continue;
    root.append(renderMetricCard(table, data));
  }
}


function renderMetricCard(table, data) {
  const tbody = el("tbody");
  for (const row of table.rows) {
    const diffCell = el("td", {},
      `${row.diff_str} (${row.pct_str === "-" ? "-" : row.pct_str + "%"})`);
    if (row.diff !== null && row.diff !== undefined) {
      diffCell.classList.add(row.diff >= 0 ? "diff-pos" : "diff-neg");
    }
    tbody.append(el("tr", {},
      el("td", {}, row.label + (row.exact ? "" : " (输入匹配)")),
      el("td", {}, row.a_str),
      el("td", {}, row.b_str),
      diffCell,
    ));
  }

  const card = el("div", { class: "card cmp-result-card" },
    el("div", { class: "card-title-row" },
      el("div", { class: "card-title" },
        `${table.name} (${table.field}${table.unit ? `, ${table.unit}` : ""})`),
      el("div", { class: "chart-legend" },
        legendItem(SERIES_COLORS.a, `A: ${data.a.label}`),
        legendItem(SERIES_COLORS.b, `B: ${data.b.label}`))),
    el("div", { class: "table-wrap" },
      el("table", { class: "data-table" },
        el("thead", {}, el("tr", {},
          el("th", {}, "用例"), el("th", {}, "A (基准)"),
          el("th", {}, "B (对比)"), el("th", {}, "Δ (B-A, Δ%)"))),
        tbody)),
    el("div", { class: "cmp-chart-row" },
      chartBox("基准 A", table, "a"),
      chartBox("对比 B", table, "b")));

  // 图表在插入DOM后再绘制(需要布局尺寸)
  requestAnimationFrame(() => {
    const canvases = card.querySelectorAll("canvas");
    const labels = table.rows.map(r => r.label);
    drawBarChart(canvases[0], labels,
      [{ name: "A", color: SERIES_COLORS.a, values: table.rows.map(r => r.a) }],
      { unit: table.unit, decimals: table.decimals });
    drawBarChart(canvases[1], labels,
      [{ name: "B", color: SERIES_COLORS.b, values: table.rows.map(r => r.b) }],
      { unit: table.unit, decimals: table.decimals });
  });
  return card;
}


function chartBox(title, table, side) {
  return el("div", { class: "chart-box" },
    el("div", { class: "metrics-group-title" },
      `${title}${table.unit ? ` (${table.unit})` : ""}`),
    el("canvas", {}));
}


function legendItem(color, label) {
  return el("span", { class: "legend-item" },
    el("span", { class: "legend-swatch", style: `background:${color}` }),
    label);
}


/* ============ 导出 ============ */

async function exportComparison() {
  if (!computeBody) {
    toast("请先完成对比计算", "warning");
    return;
  }
  try {
    await api.postDownload("/api/compare/export", computeBody);
    toast("对比结果CSV已导出", "success");
  } catch (err) {
    toast(`导出失败: ${err.message}`, "error");
  }
}
