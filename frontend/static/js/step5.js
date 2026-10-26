/** 步骤 5: 测试用例设计 */

import { $, $$, el, fmtLen, toast, modal, copyText } from "./util.js";
import { api } from "./api.js";
import { G } from "./main.js";


let presets = { input_lengths: [], output_lengths: [], grep_command: "" };
let checkedInputs = new Set();
let checkedOutputs = new Set();


export function initStep5() {
  loadPresets();

  // 最大上下文变化 -> 刷新输入长度chips可用状态
  $("#kvMaxLen").addEventListener("input", renderLengthChips);

  $("#kvGrepCopyBtn").addEventListener("click",
    () => copyText($("#kvGrepCmd").value));
  $("#openLogToolsBtn").addEventListener("click", () => {
    import("./main.js").then(m => m.showView("logtools"));
  });

  $("#generateCasesBtn").addEventListener("click", generateCases);
  $("#addCaseBtn").addEventListener("click", showAddCaseModal);
  $("#dupCaseBtn").addEventListener("click", duplicateSelectedCase);
  $("#delCaseBtn").addEventListener("click", deleteSelectedCase);

  $$(".adj-data").forEach(btn => btn.addEventListener("click",
    () => adjustCases("data", Number(btn.dataset.factor))));
  $$(".adj-conc").forEach(btn => btn.addEventListener("click",
    () => adjustCases("conc", Number(btn.dataset.factor))));
}


async function loadPresets() {
  try {
    presets = await api.get("/api/designer/presets");
    $("#kvGrepCmd").value = presets.grep_command || "";
    // 默认勾选: 输入取前两个有效项, 输出取 512/1024
    const maxLen = Number($("#kvMaxLen").value) || 0;
    const validInputs = presets.input_lengths.filter(l => !maxLen || l < maxLen);
    checkedInputs = new Set(validInputs.slice(0, 2));
    checkedOutputs = new Set(
      presets.output_lengths.filter(l => [128, 256, 512, 1024].includes(l)));
    renderLengthChips();
  } catch (err) {
    toast(`加载预设失败: ${err.message}`, "error");
  }
}


/* ============ 长度选择 chips ============ */

function renderLengthChips() {
  const maxLen = Number($("#kvMaxLen").value) || 0;
  renderChips($("#inputLengthChecks"), presets.input_lengths,
    checkedInputs, v => !!maxLen && v >= maxLen);
  renderChips($("#outputLengthChecks"), presets.output_lengths,
    checkedOutputs, () => false);
}


function renderChips(container, values, checkedSet, isDisabled) {
  container.innerHTML = "";
  for (const value of values) {
    const disabled = isDisabled(value);
    if (disabled) checkedSet.delete(value);
    const checkbox = el("input", { type: "checkbox", value: String(value) });
    checkbox.checked = checkedSet.has(value);
    checkbox.disabled = disabled;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) checkedSet.add(value);
      else checkedSet.delete(value);
    });
    container.append(el("label",
      { class: `check-chip${disabled ? " disabled" : ""}` },
      checkbox, el("span", {}, fmtLen(value))));
  }
}


function checkedValues(containerSel, checkedSet) {
  const values = [...checkedSet].filter(v =>
    !$(containerSel).querySelector(`input[value="${v}"]`)?.disabled);
  return values.sort((a, b) => a - b);
}


/* ============ KV 参数 / 生成用例 ============ */

function designerParams() {
  return {
    single_kv_cache: parseInt($("#kvCache").value, 10) || 0,
    dp: parseInt($("#kvDp").value, 10) || 1,
    max_request_length: parseInt($("#kvMaxLen").value, 10) || 0,
    repeat_rate: parseFloat($("#kvRepeatRate").value) || 0.9,
    request_rate: parseFloat($("#kvRequestRate").value) || 0,
  };
}


async function generateCases() {
  const params = designerParams();
  if (!params.single_kv_cache || !params.max_request_length) {
    toast("请先填写单个DP组KV cache与模型最大上下文", "warning");
    return;
  }
  if (!(params.repeat_rate > 0 && params.repeat_rate <= 1)) {
    toast("前缀命中率应在 (0, 1] 之间", "warning");
    return;
  }

  const btn = $("#generateCasesBtn");
  btn.disabled = true;
  btn.textContent = "生成中...";
  try {
    const data = await api.post("/api/designer/generate", {
      ...params,
      input_lengths: checkedValues("#inputLengthChecks", checkedInputs),
      output_lengths: checkedValues("#outputLengthChecks", checkedOutputs),
    });
    applyDesignerResponse(data, params);

    const banner = $("#skippedWarning");
    if (data.warning) {
      banner.hidden = false;
      banner.textContent = `⚠ ${data.warning}`;
    } else {
      banner.hidden = true;
    }
    toast(`已生成 ${data.cases.length} 个测试用例`, "success");
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "⚙️ 生成测试用例";
  }
}


function applyDesignerResponse(data, params) {
  G.designer.params = params;
  G.designer.cases = data.cases;
  G.designer.summary = data.summary;
  renderCases();
  $("#calcDetails").textContent = data.calc_details;
  $("#testSummary").textContent = data.summary;
}


/* ============ 用例表格 ============ */

function selectedCaseIndex() {
  const radio = $('input[name="caseRadio"]:checked');
  return radio ? Number(radio.value) : -1;
}


function renderCases() {
  const tbody = $("#casesTableBody");
  tbody.innerHTML = "";
  const cases = G.designer.cases;
  if (!cases.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "8" }, "点击「生成测试用例」后显示")));
    $("#belowMinBanner").hidden = true;
    return;
  }

  cases.forEach((c, i) => {
    const tr = el("tr");
    if (c.below_min) tr.classList.add("row-below-min");

    const radio = el("input",
      { type: "radio", name: "caseRadio", value: String(i) });
    radio.addEventListener("change", () => {
      $$("#casesTableBody tr").forEach(r => r.classList.remove("row-selected"));
      tr.classList.add("row-selected");
    });

    tr.append(
      el("td", {}, radio),
      el("td", {}, String(i + 1)),
      editableCell(c, "input_len"),
      editableCell(c, "output_len"),
      editableCell(c, "data_num_recommended"),
      el("td", {}, c.data_num_min != null ? c.data_num_min.toLocaleString() : "-"),
      editableCell(c, "concurrency_recommended"),
      el("td", {}, c.kv_usage != null ? `${c.kv_usage}%` : "-"),
    );
    if (c.is_custom) {
      tr.children[2].append(el("span", { class: "tag-custom" }, "自定义"));
    }
    tbody.append(tr);
  });

  // 低于最小请求数提示
  const below = cases.filter(c => c.below_min);
  const banner = $("#belowMinBanner");
  if (below.length) {
    const list = below.map(c =>
      `#${cases.indexOf(c) + 1}(${fmtLen(c.input_len)}/${fmtLen(c.output_len)})`).join(", ");
    banner.hidden = false;
    banner.textContent =
      `⚠ 以下用例的请求数低于最小值, 不会在HBM之外的缓存介质命中: ${list}`;
  } else {
    banner.hidden = true;
  }
}


function editableCell(caseData, field) {
  const input = el("input",
    { type: "number", class: "cell-input", value: String(caseData[field]) });
  input.addEventListener("change", async () => {
    const value = parseInt(input.value, 10);
    if (!Number.isFinite(value) || value < 1) {
      input.value = String(caseData[field]);
      return;
    }
    caseData[field] = value;
    await submitCases();
  });
  return el("td", { class: "cell-editable" }, input);
}


/** 提交完整用例列表到服务端校验并重算派生值 */
async function submitCases() {
  const params = G.designer.params;
  if (!params || !G.designer.cases.length) return;
  try {
    const data = await api.post("/api/designer/cases", {
      ...params,
      cases: G.designer.cases,
    });
    G.designer.cases = data.cases;
    G.designer.summary = data.summary;
    renderCases();
    $("#calcDetails").textContent = data.calc_details;
    $("#testSummary").textContent = data.summary;
  } catch (err) {
    toast(err.message, "error", 6000);
  }
}


/* ============ 添加 / 复制 / 删除 / 系数调整 ============ */

function showAddCaseModal() {
  if (!G.designer.params) {
    toast("请先生成测试用例", "warning");
    return;
  }
  const params = G.designer.params;
  const totalKv = params.single_kv_cache * params.dp;

  const inputEl = el("input", { type: "number", class: "cell-input", placeholder: "如 16384" });
  const outputEl = el("input", { type: "number", class: "cell-input", placeholder: "如 512" });
  const reqEl = el("input", { type: "number", class: "cell-input", placeholder: "留空=推荐值" });
  const concEl = el("input", { type: "number", class: "cell-input", placeholder: "留空=最大并发" });

  const m = modal({ title: "添加测试用例" });
  m.body.append(el("div", { class: "form-grid" },
    formItem("输入长度 (tokens) *", inputEl),
    formItem("输出长度 (tokens) *", outputEl),
    formItem("请求数", reqEl),
    formItem("并发数", concEl),
    el("div", { class: "form-item span-2 hint-text" },
      `按当前参数计算: 并发数 = floor(${totalKv.toLocaleString()} / (输入+输出)), ` +
      `推荐请求数 = (floor(${totalKv.toLocaleString()} / 输入 / ${params.repeat_rate}) + 1) × 2`),
  ));

  m.footer.append(
    el("button", { class: "btn btn-outline", onclick: m.close }, "取消"),
    el("button", {
      class: "btn btn-primary",
      onclick: async () => {
        const inputLen = parseInt(inputEl.value, 10);
        const outputLen = parseInt(outputEl.value, 10);
        if (!inputLen || !outputLen || inputLen < 1 || outputLen < 1) {
          toast("请填写有效的输入/输出长度", "warning");
          return;
        }
        const total = inputLen + outputLen;
        const maxCc = Math.max(1, Math.floor(totalKv / total));
        const minReq = Math.floor(totalKv / inputLen / params.repeat_rate) + 1;
        const recReq = minReq * 2;
        G.designer.cases.push({
          input_len: inputLen,
          output_len: outputLen,
          data_num_recommended: parseInt(reqEl.value, 10) || recReq,
          data_num_min: minReq,
          concurrency_recommended: parseInt(concEl.value, 10) || maxCc,
          concurrency_max: maxCc,
          repeat_rate: params.repeat_rate,
          dp: params.dp,
          is_custom: true,
        });
        m.close();
        await submitCases();
        toast("用例已添加", "success");
      },
    }, "添加"),
  );
}


function formItem(label, input) {
  return el("div", { class: "form-item" },
    el("label", {}, label), input);
}


async function duplicateSelectedCase() {
  const idx = selectedCaseIndex();
  if (idx < 0) {
    toast("请先选择一个用例", "warning");
    return;
  }
  const source = G.designer.cases[idx];
  G.designer.cases.splice(idx + 1, 0, { ...source, is_custom: true });
  await submitCases();
  toast("用例已复制", "success");
}


async function deleteSelectedCase() {
  const idx = selectedCaseIndex();
  if (idx < 0) {
    toast("请先选择一个用例", "warning");
    return;
  }
  G.designer.cases.splice(idx, 1);
  if (!G.designer.cases.length) {
    renderCases();
    $("#calcDetails").textContent = "(尚未生成测试用例)";
    $("#testSummary").textContent = "(尚未生成)";
    toast("已删除全部用例", "info");
    return;
  }
  await submitCases();
  toast("用例已删除", "success");
}


async function adjustCases(kind, factor) {
  if (!G.designer.cases.length) {
    toast("请先生成测试用例", "warning");
    return;
  }
  const field = kind === "data" ? "data_num_recommended" : "concurrency_recommended";
  for (const c of G.designer.cases) {
    c[field] = Math.max(1, Math.round(c[field] * factor));
    if (kind === "data") c.is_custom = true;
  }
  await submitCases();
  toast(`已将${kind === "data" ? "请求数" : "并发数"}整体 ×${factor}`, "success");
}
