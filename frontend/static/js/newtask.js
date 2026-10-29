/** 新建测试任务 —— 选镜像 + 配置 + 用例设计 + 提交(容器由平台自动管理) */

import { $, $$, el, fmtLen, setMsg, toast, modal } from "./util.js";
import { api } from "./api.js";
import { G, saveDesigner, showView } from "./state.js";


let imagesCache = [];
let selectedImage = null;
let imagesLoaded = false;     // 首次进入"新建任务"时自动加载一次
let presets = { input_lengths: [], output_lengths: [] };
let presetsPromise = null;    // 长度预设加载 promise(失败时置 null, 允许重试)
let checkedInputs = new Set();
let checkedOutputs = new Set();


export function initNewTask() {
  initImageSelect();
  initConfigForm();
  initDesigner();
  $("#submitTaskBtn").addEventListener("click", submitTask);

  // 长度预设先加载: 回填历史设计时才能渲染输入/输出长度选择栏
  const presetsReady = loadPresets();
  // 回填上次设计的用例(浏览器本地持久化, 刷新不丢失)
  if (G.designer.params && G.designer.cases.length) {
    restoreDesigner(presetsReady);
  }
  // 每次进入"新建任务"视图: 首次自动拉取镜像; 长度预设失败时自动重试
  window.enterNewTaskView = () => {
    loadImagesOnce();
    if (!presetsPromise) loadPresets();
  };
  // 供任务列表/详情页"复制为新任务"调用(基于历史任务回填表单)
  window.prefillNewTask = fillFromTask;
}

function loadImagesOnce() {
  if (imagesLoaded) return;
  imagesLoaded = true;
  refreshImages();
}


/* ============ 1 · 镜像选择 ============ */

function initImageSelect() {
  $("#refreshImagesBtn").addEventListener("click", refreshImages);
  $("#imageFilterInput").addEventListener("input", renderImages);
  $("#imageFilterAll").addEventListener("change", renderImages);
}

async function refreshImages() {
  setMsg("#imageSelectMsg", "加载镜像列表...", "warn");
  try {
    const data = await api.get("/api/platform/images");
    imagesCache = data.images || [];
    // 默认选中名称含 aisbench 的第一个(业务优先)
    if (!selectedImage) {
      selectedImage = imagesCache.find(i =>
        /aisbench/i.test(i.name)) || null;
    }
    renderImages();
    setMsg("#imageSelectMsg",
      imagesCache.length ? `共 ${imagesCache.length} 个镜像` : "执行机上没有镜像",
      "muted");
  } catch (err) {
    setMsg("#imageSelectMsg", err.message, "error");
  }
}

function renderImages() {
  const tbody = $("#imagesTableBody");
  tbody.innerHTML = "";
  const showAll = $("#imageFilterAll").checked;
  const keyword = $("#imageFilterInput").value.trim().toLowerCase();
  let items = showAll || !keyword ? imagesCache
    : imagesCache.filter(img => img.name.toLowerCase().includes(keyword));
  if (!showAll && !keyword) {
    // 默认视图聚焦业务镜像
    items = imagesCache.filter(i => /aisbench|benchmark/i.test(i.name));
    if (!items.length) items = imagesCache;
  }
  if (!items.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "5" }, "未找到镜像, 请刷新或联系管理员")));
    return;
  }
  for (const img of items) {
    const chosen = selectedImage?.name === img.name;
    const tr = el("tr", { class: chosen ? "row-selected" : "" });
    tr.append(
      el("td", { class: "mono" }, img.name),
      el("td", { class: "mono" }, img.id),
      el("td", {}, img.size),
      el("td", {}, img.created),
      el("td", {}, el("button", {
        class: `btn btn-sm ${chosen ? "btn-outline" : "btn-primary"}`,
        onclick: () => {
          selectedImage = img;
          setMsg("#imageSelectMsg", `✓ 已选择镜像: ${img.name}`, "ok");
          renderImages();
        },
      }, chosen ? "✓ 已选择" : "选择")),
    );
    tbody.append(tr);
  }
}


/* ============ 2 · 测试配置 ============ */

function initConfigForm() {
  $("#cfgModelPathBrowse").addEventListener("click", () =>
    browseRemoteDir("/mnt", p => $("#cfgModelPath").value = p));
}

/** 收集表单配置(空的高级项不提交, 由服务端默认值兜底) */
function collectConfig() {
  const config = {
    model_name: $("#cfgModelName").value.trim(),
    model_path: $("#cfgModelPath").value.trim(),
    host_ip: $("#cfgHostIp").value.trim(),
    host_port: $("#cfgHostPort").value.trim(),
  };
  const optional = {
    api_key: $("#cfgApiKey").value.trim(),
    default_perf: $("#cfgDefaultPerf").value,
    output_dir: $("#cfgOutputDir").value.trim(),
    pod_info: $("#cfgPodInfo").value.trim(),
    work_path: $("#cfgWorkPath").value.trim(),
    dataset_path: $("#cfgDatasetPath").value.trim(),
  };
  for (const [k, v] of Object.entries(optional)) {
    if (v) config[k] = v;
  }
  return config;
}

/** 将历史任务的测试配置回填到表单 */
function applyConfig(config) {
  const fields = {
    model_name: "#cfgModelName", model_path: "#cfgModelPath",
    host_ip: "#cfgHostIp", host_port: "#cfgHostPort",
    api_key: "#cfgApiKey", default_perf: "#cfgDefaultPerf",
    output_dir: "#cfgOutputDir", pod_info: "#cfgPodInfo",
    work_path: "#cfgWorkPath", dataset_path: "#cfgDatasetPath",
  };
  for (const [key, sel] of Object.entries(fields)) {
    $(sel).value = config[key] ?? "";
  }
}

function validateConfigLocal(config) {
  if (!config.model_name) return "请填写模型名称";
  if (!config.model_path) return "请选择模型权重目录";
  if (!config.host_ip) return "请填写服务 IP";
  if (!config.host_port) return "请填写服务端口";
  if (config.host_port && !/^\d+$/.test(config.host_port)) {
    return "服务端口必须是数字";
  }
  return "";
}

/** 浏览执行机目录(选择模型权重目录) */
function browseRemoteDir(initialPath, onPick) {
  let currentPath = initialPath || "/";
  const m = modal({ title: "浏览执行机目录 - 选择模型权重目录", large: true });
  const pathInput = el("input", { type: "text", value: currentPath,
    style: "flex:1" });
  const listBox = el("div", { class: "browser-list" });
  const statusEl = el("div", { class: "inline-msg muted", style: "margin-top:8px" });

  async function load(path) {
    currentPath = path;
    pathInput.value = path;
    listBox.innerHTML = `<div class="file-list-empty">加载中...</div>`;
    try {
      const data = await api.get(
        `/api/platform/fs/list?path=${encodeURIComponent(path)}`);
      renderList(data.entries || []);
    } catch (err) {
      listBox.innerHTML = "";
      statusEl.textContent = `加载失败: ${err.message}`;
    }
  }

  function renderList(entries) {
    listBox.innerHTML = "";
    statusEl.textContent = "";
    if (!entries.length) {
      listBox.innerHTML = `<div class="file-list-empty">(空目录)</div>`;
      return;
    }
    for (const entry of entries) {
      if (entry.type !== "dir") continue;
      const row = el("div", { class: "file-item", title: "双击进入" },
        el("span", {}, `📁 ${entry.name}`));
      row.addEventListener("dblclick", () =>
        load(joinPath(currentPath, entry.name)));
      listBox.append(row);
    }
  }

  m.body.append(
    el("div", { class: "browser-path-row" },
      pathInput,
      el("button", {
        class: "btn btn-primary btn-sm",
        onclick: () => load(pathInput.value.trim() || "/"),
      }, "前往"),
      el("button", {
        class: "btn btn-outline btn-sm",
        onclick: () => load(parentPath(currentPath)),
      }, "↑ 上级")),
    listBox, statusEl);

  m.footer.append(
    el("button", { class: "btn btn-outline", onclick: m.close }, "取消"),
    el("button", {
      class: "btn btn-primary",
      onclick: () => { const p = currentPath; m.close(); onPick(p); },
    }, "选择当前目录"));

  load(initialPath || "/");
  return m;
}

function joinPath(base, name) {
  const b = (base || "/").replace(/\/+$/, "");
  return `${b}/${name}`;
}

function parentPath(p) {
  const parts = (p || "/").split("/").filter(Boolean);
  parts.pop();
  return "/" + parts.join("/");
}


/* ============ 3 · 测试用例设计 ============ */

function initDesigner() {
  $("#kvMaxLen").addEventListener("input", renderLengthChips);
  $("#generateCasesBtn").addEventListener("click", generateCases);
  $("#addCaseBtn").addEventListener("click", showAddCaseModal);
  $("#dupCaseBtn").addEventListener("click", duplicateSelectedCase);
  $("#delCaseBtn").addEventListener("click", deleteSelectedCase);
  $$(".adj-data").forEach(btn => btn.addEventListener("click",
    () => adjustCases("data", Number(btn.dataset.factor))));
  $$(".adj-conc").forEach(btn => btn.addEventListener("click",
    () => adjustCases("conc", Number(btn.dataset.factor))));
}

function loadPresets() {
  const p = (async () => {
    const data = await api.get("/api/designer/presets", { silent: true });
    presets = {
      input_lengths: Array.isArray(data?.input_lengths) ? [...data.input_lengths] : [],
      output_lengths: Array.isArray(data?.output_lengths) ? [...data.output_lengths] : [],
    };
    setMsg("#designerMsg", "");   // 清除上次失败提示
    // 全新表单才应用默认勾选; 回填历史设计时保留历史勾选
    if (!G.designer.params || !G.designer.cases.length) applyDefaultChecks();
    // 关键修复: 预设到达后必须重渲染长度选择栏。
    // 旧逻辑在存在历史设计时提前 return, 而回填渲染发生在预设到达之前,
    // 导致选择栏以空 presets 渲染后永远不再刷新(偶发"长度选择栏消失")。
    renderLengthChips();
    return presets;
  })();
  presetsPromise = p;
  p.catch(err => {
    presetsPromise = null;   // 置空以允许下次进入视图时重试
    setMsg("#designerMsg",
      `输入/输出长度预设加载失败: ${err.message} — 再次进入本页将自动重试`, "error");
  });
  return p;
}

function applyDefaultChecks() {
  // 默认勾选: 输入前两个有效项, 输出 512/1024
  const maxLen = Number($("#kvMaxLen").value) || 0;
  const validInputs = presets.input_lengths.filter(l => !maxLen || l < maxLen);
  checkedInputs = new Set(validInputs.slice(0, 2));
  checkedOutputs = new Set(
    presets.output_lengths.filter(l => [128, 256, 512, 1024].includes(l)));
}

async function restoreDesigner(presetsReady, message = "已恢复上次设计的测试用例") {
  const d = G.designer;
  $("#kvCache").value = d.params.single_kv_cache ?? "";
  $("#kvDp").value = d.params.dp ?? 1;
  $("#kvMaxLen").value = d.params.max_request_length ?? "";
  $("#kvRepeatRate").value = d.params.repeat_rate ?? 0.9;
  $("#kvRequestRate").value = d.params.request_rate ?? 0;
  checkedInputs = new Set(d.input_lengths || []);
  checkedOutputs = new Set(d.output_lengths || []);
  // 等待长度预设到达后再渲染选择栏(否则渲染瞬间 presets 为空 → 选择栏不可见)
  try { await presetsReady; } catch { /* 失败已提示, 以占位文案渲染 */ }
  renderLengthChips();
  renderCases();
  toast(message, "info");
  try {
    const data = await api.post("/api/designer/cases",
      { ...d.params, cases: d.cases }, { silent: true });
    G.designer.cases = data.cases;
    saveDesigner();
    renderCases();
    $("#calcDetails").textContent = data.calc_details;
  } catch { /* ignore */ }
}

/** 基于历史任务回填"新建任务"表单(任务列表/详情页的"复制"入口) */
export async function fillFromTask(src) {
  if (!src || typeof src !== "object") return;
  // 任务名: 历史名 + 副本后缀, 用户可自行修改
  $("#taskNameInput").value = src.name ? `${src.name} 副本` : "";
  // 镜像: 优先匹配当前镜像列表; 不在列表中时保留历史镜像名(提交时仍使用它)
  if (src.image) {
    const found = imagesCache.find(i => i.name === src.image);
    selectedImage = found || { name: src.image };
    renderImages();
    if (imagesLoaded && !found) {
      setMsg("#imageSelectMsg",
        `⚠ 历史任务镜像 ${src.image} 不在当前镜像列表中, 提交时仍将使用该镜像`,
        "warn");
    }
  }
  // 测试配置回填
  applyConfig(src.config || {});
  // 用例设计回填(等长度预设就绪后渲染选择栏)
  const d = src.designer || {};
  G.designer = {
    params: {
      single_kv_cache: Number(d.single_kv_cache) || 0,
      dp: Number(d.dp) || 1,
      max_request_length: Number(d.max_request_length) || 0,
      repeat_rate: Number(d.repeat_rate) || 0.9,
      request_rate: Number(d.request_rate) || 0,
    },
    cases: Array.isArray(d.cases) ? d.cases : [],
    summary: d.summary || "",
    input_lengths: Array.isArray(d.input_lengths) ? d.input_lengths : [],
    output_lengths: Array.isArray(d.output_lengths) ? d.output_lengths : [],
  };
  saveDesigner();
  const presetsReady = presetsPromise || loadPresets();
  await restoreDesigner(presetsReady, "已载入历史任务的测试用例");
  setMsg("#submitMsg",
    `已从任务 ${src.name || src.id} 复制配置, 请确认后提交新任务`, "ok");
}

function renderLengthChips() {
  const maxLen = Number($("#kvMaxLen").value) || 0;
  renderChips($("#inputLengthChecks"), presets.input_lengths,
    checkedInputs, v => !!maxLen && v >= maxLen);
  renderChips($("#outputLengthChecks"), presets.output_lengths,
    checkedOutputs, () => false);
}

function renderChips(container, values, checkedSet, isDisabled) {
  container.innerHTML = "";
  if (!values.length) {
    // 预设缺失时给出可见占位, 而不是空白区域让用户以为界面坏了
    container.append(el("span", { class: "hint-text" },
      "暂无可选长度(预设未加载, 再次进入本页将自动重试)"));
    return;
  }
  for (const value of values) {
    const disabled = isDisabled(value);
    if (disabled) checkedSet.delete(value);
    const checkbox = el("input", { type: "checkbox", value: String(value) });
    checkbox.checked = checkedSet.has(value);
    checkbox.disabled = disabled;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) checkedSet.add(value);
      else checkedSet.delete(value);
      persistChecked();
    });
    container.append(el("label",
      { class: `check-chip${disabled ? " disabled" : ""}` },
      checkbox, el("span", {}, fmtLen(value))));
  }
}

function persistChecked() {
  G.designer.input_lengths = [...checkedInputs];
  G.designer.output_lengths = [...checkedOutputs];
  saveDesigner();
}

function checkedValues(containerSel, checkedSet) {
  return [...checkedSet].filter(v =>
    !$(containerSel).querySelector(`input[value="${v}"]`)?.disabled)
    .sort((a, b) => a - b);
}

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
    G.designer.params = params;
    G.designer.cases = data.cases;
    G.designer.summary = data.summary;
    G.designer.input_lengths = checkedValues("#inputLengthChecks", checkedInputs);
    G.designer.output_lengths = checkedValues("#outputLengthChecks", checkedOutputs);
    saveDesigner();
    renderCases();
    $("#calcDetails").textContent = data.calc_details;
    setMsg("#designerMsg", data.warning ? `⚠ ${data.warning}` : "", "warn");
    toast(`已生成 ${data.cases.length} 个测试用例`, "success");
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "⚙ 生成测试用例";
  }
}

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
      el("td", { colspan: "7" }, "点击「生成测试用例」后显示")));
    return;
  }
  cases.forEach((c, i) => {
    const tr = el("tr", { class: c.below_min ? "row-below-min" : "" });
    const radio = el("input", { type: "radio", name: "caseRadio", value: String(i) });
    radio.addEventListener("change", () => {
      $$("#casesTableBody tr").forEach(r => r.classList.remove("row-selected"));
      tr.classList.add("row-selected");
    });
    tr.append(
      el("td", {}, radio),
      editableCell(c, "input_len"),
      editableCell(c, "output_len"),
      editableCell(c, "data_num_recommended"),
      el("td", {}, c.data_num_min != null ? c.data_num_min.toLocaleString() : "-"),
      editableCell(c, "concurrency_recommended"),
      el("td", {}, c.concurrency_max != null ? c.concurrency_max.toLocaleString() : "-"),
    );
    tbody.append(tr);
  });
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

async function submitCases() {
  const params = G.designer.params;
  if (!params || !G.designer.cases.length) return;
  try {
    const data = await api.post("/api/designer/cases", {
      ...params, cases: G.designer.cases,
    });
    G.designer.cases = data.cases;
    G.designer.summary = data.summary;
    saveDesigner();
    renderCases();
    $("#calcDetails").textContent = data.calc_details;
  } catch (err) {
    toast(err.message, "error", 6000);
  }
}

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
    el("div", { class: "form-item" }, el("label", {}, "输入长度 (tokens) *"), inputEl),
    el("div", { class: "form-item" }, el("label", {}, "输出长度 (tokens) *"), outputEl),
    el("div", { class: "form-item" }, el("label", {}, "请求数"), reqEl),
    el("div", { class: "form-item" }, el("label", {}, "并发数"), concEl),
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
        G.designer.cases.push({
          input_len: inputLen, output_len: outputLen,
          data_num_recommended: parseInt(reqEl.value, 10) || minReq * 2,
          data_num_min: minReq,
          concurrency_recommended: parseInt(concEl.value, 10) || maxCc,
          concurrency_max: maxCc,
          repeat_rate: params.repeat_rate, dp: params.dp,
          is_custom: true,
        });
        m.close();
        await submitCases();
        toast("用例已添加", "success");
      },
    }, "添加"));
}

async function duplicateSelectedCase() {
  const idx = selectedCaseIndex();
  if (idx < 0) { toast("请先选择一个用例", "warning"); return; }
  const source = G.designer.cases[idx];
  G.designer.cases.splice(idx + 1, 0, { ...source, is_custom: true });
  await submitCases();
  toast("用例已复制", "success");
}

async function deleteSelectedCase() {
  const idx = selectedCaseIndex();
  if (idx < 0) { toast("请先选择一个用例", "warning"); return; }
  G.designer.cases.splice(idx, 1);
  if (!G.designer.cases.length) {
    G.designer.params = null;
    renderCases();
    saveDesigner();
    $("#calcDetails").textContent = "(点击「生成测试用例」后显示计算明细)";
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


/* ============ 4 · 提交任务 ============ */

async function submitTask() {
  if (!selectedImage) {
    toast("请先选择测试镜像", "warning");
    showView("newtask");
    return;
  }
  const config = collectConfig();
  const err = validateConfigLocal(config);
  if (err) {
    toast(err, "warning");
    setMsg("#configMsg", err, "error");
    return;
  }
  if (!G.designer.params || !G.designer.cases.length) {
    toast("请先生成测试用例", "warning");
    return;
  }

  const btn = $("#submitTaskBtn");
  btn.disabled = true;
  btn.textContent = "提交中...";
  try {
    const data = await api.post("/api/platform/tasks", {
      name: $("#taskNameInput").value.trim() || null,
      image: selectedImage.name,
      config,
      designer: {
        ...G.designer.params,
        // 勾选的长度一并持久化: 复制任务时可精确回显选择栏
        input_lengths: checkedValues("#inputLengthChecks", checkedInputs),
        output_lengths: checkedValues("#outputLengthChecks", checkedOutputs),
        cases: G.designer.cases,
      },
    });
    toast(data.message, "success");
    setMsg("#submitMsg", `✓ ${data.message}, 正在排队执行...`, "ok");
    // 跳转任务列表查看执行进度
    await window.refreshTasks?.();
    showView("tasks");
    window.showTaskDetail?.(data.id);
  } catch (e) {
    setMsg("#submitMsg", `提交失败: ${e.message}`, "error");
    toast(e.message, "error", 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "🚀 提交任务";
  }
}
