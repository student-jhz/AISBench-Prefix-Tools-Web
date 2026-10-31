/** 新建测试任务 —— 选镜像 + 配置 + 用例设计 + 提交(容器由平台自动管理) */

import { $, $$, el, fmtLen, setMsg, toast, modal, copyText } from "./util.js";
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
  $("#kvAutoFetchBtn").addEventListener("click", showEngineKvModal);
  $("#generateCasesBtn").addEventListener("click", generateCases);
  $("#addCaseBtn").addEventListener("click", showAddCaseModal);
  $("#dupCaseBtn").addEventListener("click", duplicateSelectedCase);
  $("#delCaseBtn").addEventListener("click", deleteSelectedCase);
  $$(".adj-data").forEach(btn => btn.addEventListener("click",
    () => adjustCases("data", Number(btn.dataset.factor))));
  $$(".adj-conc").forEach(btn => btn.addEventListener("click",
    () => adjustCases("conc", Number(btn.dataset.factor))));
}


/* ============ 3 · 引擎参数自动获取(从 vLLM 日志) ============ */

/**
 * 从 vLLM 引擎日志获取 GPU KV cache size / Maximum concurrency,
 * 填入「单个DP组KV cache」与「最大请求长度」。
 * 三种日志来源(与桌面版 AISBench-Prefix-Tools 的日志工具一致):
 *   host        宿主机日志文件(如 /root/vllm_serve.log)
 *   container   容器内日志文件(docker exec grep)
 *   docker_logs 容器运行日志(docker logs | grep)
 */
function showEngineKvModal() {
  let mode = "host";
  let server = "session";   // "session"=当前登录执行机 | "other"=用户指定的其他服务器
  let currentPath = "/";
  let parsed = null;        // {kv, max_len}
  let otherConnected = null;   // 其他服务器连接状态 {connected, host, port, username}
  let keyUploadId = null;      // 其他服务器密钥上传 ID

  const m = modal({ title: "从引擎日志获取 KV cache / 最大请求长度", large: true });

  // ---- 日志所在服务器选择 ----
  // vLLM 引擎不一定部署在当前登录的执行机上, 允许临时连接其他服务器
  const serverOptions = [
    ["session", "当前登录的执行机"],
    ["other", "其他服务器"],
  ];
  const serverRadios = serverOptions.map(([value, label]) => {
    const input = el("input", { type: "radio", name: "engineLogServer", value });
    input.checked = value === server;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      server = value;
      updateServerUI();
    });
    return el("label", { class: "checkbox-inline" }, input, el("span", {}, label));
  });
  const serverRow = el("div", { class: "form-actions", style: "margin-bottom:10px" },
    el("span", { class: "hint-text" }, "日志所在服务器:"),
    ...serverRadios);

  // ---- 其他服务器连接(密码或密钥, 挂在当前用户会话上) ----
  const otherHost = el("input", { type: "text", placeholder: "如 10.0.0.2" });
  const otherPort = el("input", { type: "number", value: "22", min: "1", max: "65535" });
  const otherUser = el("input", { type: "text", placeholder: "root" });
  const otherPassword = el("input", { type: "password", placeholder: "密码(与密钥二选一)" });
  const keyFileInput = el("input", { type: "file", style: "display:none" });
  const keyPickBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: () => keyFileInput.click(),
  }, "🔑 密钥文件");
  const keyNameEl = el("span", { class: "hint-text" }, "未选择密钥");
  keyFileInput.addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    keyNameEl.textContent = `${file.name} (上传中...)`;
    try {
      const data = await api.upload("/api/upload/ssh-key", file);
      keyUploadId = data.id;
      keyNameEl.textContent = file.name;
    } catch (err) {
      keyUploadId = null;
      keyNameEl.textContent = `上传失败: ${err.message}`;
    }
    e.target.value = "";
  });
  const connectBtn = el("button", { class: "btn btn-primary btn-sm", type: "button" },
    "连接");
  const otherStatusEl = el("span", { class: "inline-msg muted" });
  const otherFormRow = el("div", { class: "form-grid" },
    el("div", { class: "form-item" }, el("label", {}, "主机"), otherHost),
    el("div", { class: "form-item" }, el("label", {}, "端口"), otherPort),
    el("div", { class: "form-item" }, el("label", {}, "用户名"), otherUser),
    el("div", { class: "form-item" }, el("label", {}, "密码"), otherPassword),
    el("div", { class: "form-item" }, el("label", {}, "密钥"),
      el("div", { class: "form-actions", style: "margin:0" }, keyPickBtn, keyNameEl)),
  );
  const disconnectBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button", onclick: disconnectOther,
  }, "断开");
  const otherActionsRow = el("div", { class: "form-actions", style: "margin-bottom:4px" },
    connectBtn, disconnectBtn, otherStatusEl, keyFileInput);
  const otherServerBox = el("div", {
    class: "advanced-box", style: "margin-bottom:10px;padding:10px",
  }, otherFormRow, otherActionsRow);

  async function refreshOtherStatus() {
    try {
      otherConnected = await api.get("/api/platform/engine/other/status",
        { silent: true });
    } catch {
      otherConnected = { connected: false };
    }
    renderOtherStatus();
  }

  function renderOtherStatus() {
    const on = !!(otherConnected && otherConnected.connected);
    const creds = [otherHost, otherPort, otherUser, otherPassword, keyPickBtn];
    creds.forEach(i => { i.disabled = on; });
    connectBtn.hidden = on;
    disconnectBtn.hidden = !on;
    otherStatusEl.textContent = on
      ? `✓ 已连接 ${otherConnected.username}@${otherConnected.host}:${otherConnected.port}`
      : "未连接";
    otherStatusEl.className = on ? "inline-msg ok" : "inline-msg muted";
  }

  async function connectOther() {
    const host = otherHost.value.trim();
    const username = otherUser.value.trim();
    if (!host || !username) {
      otherStatusEl.textContent = "请填写主机与用户名";
      otherStatusEl.className = "inline-msg warn";
      return;
    }
    const body = {
      host, port: Number(otherPort.value) || 22, username,
      password: otherPassword.value || undefined,
      key_upload_id: keyUploadId || undefined,
    };
    if (!body.password && !body.key_upload_id) {
      otherStatusEl.textContent = "请填写密码或上传密钥文件";
      otherStatusEl.className = "inline-msg warn";
      return;
    }
    connectBtn.disabled = true;
    connectBtn.textContent = "连接中...";
    try {
      const data = await api.post("/api/platform/engine/other/connect", body);
      otherConnected = data.status;
      renderOtherStatus();
      toast(data.message, "success");
      containerSelect.innerHTML = "";   // 换机器后重新加载容器列表
      loadContainers(true);
      if (mode !== "docker_logs") loadDir(currentPath);
    } catch (err) {
      otherStatusEl.textContent = `连接失败: ${err.message}`;
      otherStatusEl.className = "inline-msg error";
    } finally {
      connectBtn.disabled = false;
      connectBtn.textContent = "连接";
    }
  }
  connectBtn.addEventListener("click", connectOther);

  async function disconnectOther() {
    try {
      const data = await api.post("/api/platform/engine/other/disconnect");
      otherConnected = data.status;
    } catch {
      otherConnected = { connected: false };
    }
    renderOtherStatus();
    containerSelect.innerHTML = "";
    parsed = null;
    parsedEl.textContent = "";
    fillBtn.disabled = true;
  }

  // ---- 来源选择 ----
  const modeOptions = [
    ["host", "宿主机日志文件"],
    ["container", "容器内日志文件"],
    ["docker_logs", "容器运行日志 (docker logs)"],
  ];
  const modeRadios = modeOptions.map(([value, label]) => {
    const input = el("input", { type: "radio", name: "engineLogMode", value });
    input.checked = value === mode;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      mode = value;
      updateModeUI();
    });
    return el("label", { class: "checkbox-inline" }, input, el("span", {}, label));
  });
  const modeRow = el("div", { class: "form-actions", style: "margin-bottom:10px" },
    ...modeRadios);

  // ---- 容器选择(container/docker_logs 模式) ----
  const containerSelect = el("select", { style: "flex:1;min-width:220px" });
  containerSelect.addEventListener("change", updateCommandPreview);
  const refreshContainersBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: () => loadContainers(true),
  }, "↻ 刷新容器");
  const containerRow = el("div", { class: "form-actions", style: "margin-bottom:10px" },
    el("span", { class: "hint-text" }, "引擎容器:"),
    containerSelect, refreshContainersBtn,
    el("span", { class: "hint-text" }, "vLLM 服务所在容器(非平台测试容器)"));

  // ---- 日志路径(host/container 模式) ----
  const pathInput = el("input", { type: "text", value: currentPath, style: "flex:1" });
  pathInput.addEventListener("input", updateCommandPreview);
  const goBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: () => loadDir(pathInput.value.trim() || "/"),
  }, "浏览");
  const upBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: () => loadDir(parentPath(currentPath)),
  }, "↑ 上级");
  const pathRow = el("div", { class: "browser-path-row", style: "margin-bottom:8px" },
    pathInput, goBtn, upBtn);

  const listBox = el("div", {
    class: "browser-list",
    style: "height:220px;margin-bottom:10px",
  });

  // ---- 操作与结果 ----
  const grepBtn = el("button", { class: "btn btn-primary", type: "button" },
    "🔍 筛选 KV cache");
  const statusEl = el("span", { class: "inline-msg muted" });
  const actionRow = el("div", { class: "form-actions", style: "margin-bottom:10px" },
    grepBtn, statusEl);

  // ---- 命令预览与复制(日志不在服务器上时, 用户可复制命令自行执行) ----
  const GREP = "grep -e 'GPU KV cache size' -e 'Maximum concurrency'";
  const GENERIC_CMD = `cat vllm_serve.log | ${GREP}`;
  const cmdText = el("span", { style: "flex:1" });
  const copyCmdBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: () => copyText(cmdText.textContent),
  }, "⧉ 复制命令");
  const cmdRow = el("div", {
    class: "command-display",
    style: "margin-bottom:4px;display:flex;align-items:center;gap:8px",
  }, cmdText, copyCmdBtn);
  const cmdHint = el("div", { class: "hint-text", style: "margin-bottom:8px" },
    "日志不在服务器上? 复制命令到日志所在的机器执行, 把输出粘贴到下方解析");
  const resultPre = el("pre", {
    class: "log-console", style: "height:140px;margin-bottom:8px",
  }, "(点击「筛选 KV cache」后显示结果)");
  const parsedEl = el("div", { class: "inline-msg", style: "margin-bottom:4px" });
  const mapHint = el("div", { class: "hint-text", style: "margin-bottom:8px" },
    "GPU KV cache size → 单个DP组KV cache；Maximum concurrency → 最大请求长度");

  // ---- 粘贴 grep 输出解析(命令在别处执行后的回填通道) ----
  const pasteInput = el("textarea", {
    rows: "3", style: "width:100%;font-family:var(--mono);font-size:12px",
    placeholder: "把在别处执行的 grep 输出粘贴到这里, 例如:\n"
      + "GPU KV cache size: 198,469 tokens\n"
      + "Maximum concurrency for 131,072 tokens per request: 3.84x",
  });
  const parsePasteBtn = el("button", {
    class: "btn btn-outline btn-sm", type: "button",
    onclick: parsePastedOutput,
  }, "📋 解析粘贴的输出");
  const pasteBox = el("details", { class: "advanced-box", style: "margin-bottom:8px" },
    el("summary", {}, "日志在别处? 粘贴 grep 输出解析"),
    el("div", { style: "margin-top:8px" },
      pasteInput,
      el("div", { class: "form-actions", style: "margin-top:6px" }, parsePasteBtn)));

  function updateModeUI() {
    containerRow.hidden = mode === "host";
    pathRow.hidden = mode === "docker_logs";
    listBox.hidden = mode === "docker_logs";
    parsed = null;
    parsedEl.textContent = "";
    fillBtn.disabled = true;
    if (mode !== "host") loadContainers();
    else if (!listBox.children.length) loadDir("/");
    updateCommandPreview();
  }

  function updateServerUI() {
    otherServerBox.hidden = server === "session";
    // 换了机器: 容器列表与目录内容全部失效, 需重新加载
    containerSelect.innerHTML = "";
    listBox.innerHTML = "";
    parsed = null;
    parsedEl.textContent = "";
    fillBtn.disabled = true;
    if (server === "other") refreshOtherStatus();
    updateModeUI();
  }

  /** POSIX shell 引用(与后端 shlex.quote 行为一致) */
  function shellQuote(value) {
    const s = String(value ?? "");
    if (s === "") return "''";
    if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(s)) return s;
    return `'${s.replace(/'/g, "'\\''")}'`;
  }

  /** 按当前选择构造 grep 命令(与服务端 build_grep_command 一致) */
  function buildCommand() {
    if (mode === "docker_logs") {
      const c = containerSelect.value;
      return c ? `docker logs ${shellQuote(c)} 2>&1 | ${GREP}` : "";
    }
    const p = pathInput.value.trim();
    if (mode === "container") {
      const c = containerSelect.value;
      return (c && p) ? `docker exec ${shellQuote(c)} ${GREP} ${shellQuote(p)}` : "";
    }
    return p ? `${GREP} ${shellQuote(p)}` : "";
  }

  function updateCommandPreview() {
    cmdText.textContent = buildCommand() || GENERIC_CMD;
  }

  /** 解析用户粘贴的 grep 输出(与后端 parse_kv_results 相同的正则) */
  function parsePastedOutput() {
    const text = pasteInput.value || "";
    const kvMatch = text.match(/GPU KV cache size:?\s*([\d,]+)\s*tokens/);
    const maxMatch = text.match(/Maximum concurrency for ([\d,]+) tokens per request/);
    const kv = kvMatch ? Number(kvMatch[1].replace(/,/g, "")) : null;
    const maxLen = maxMatch ? Number(maxMatch[1].replace(/,/g, "")) : null;
    if (kv === null && maxLen === null) {
      parsed = null;
      parsedEl.textContent = "粘贴内容中未找到 KV cache / 最大上下文数值";
      parsedEl.className = "inline-msg warn";
      fillBtn.disabled = true;
      return;
    }
    parsed = { kv, max_len: maxLen };
    const parts = [];
    if (kv !== null) parts.push(`单个DP组KV cache = ${kv.toLocaleString()} tokens`);
    if (maxLen !== null) parts.push(`最大请求长度 = ${maxLen.toLocaleString()} tokens`);
    parsedEl.textContent = "✓ " + parts.join("  |  ");
    parsedEl.className = "inline-msg ok";
    fillBtn.disabled = false;
  }

  async function loadContainers(force = false) {
    if (containerSelect.options.length && !force) return;
    if (server === "other" && !(otherConnected && otherConnected.connected)) {
      statusEl.textContent = "请先连接其他服务器";
      statusEl.className = "inline-msg warn";
      return;
    }
    statusEl.textContent = "加载容器列表...";
    statusEl.className = "inline-msg warn";
    try {
      const data = await api.get(
        `/api/platform/engine/containers?server=${server}`);
      containerSelect.innerHTML = "";
      const items = data.containers || [];
      if (!items.length) {
        containerSelect.append(el("option", { value: "" },
          "(没有运行中的容器)"));
      }
      for (const item of items) {
        containerSelect.append(el("option", { value: item.name },
          `${item.name}  (${item.image || "?"})`));
      }
      statusEl.textContent = "";
      statusEl.className = "inline-msg muted";
    } catch (err) {
      statusEl.textContent = `容器列表加载失败: ${err.message}`;
      statusEl.className = "inline-msg error";
    }
  }

  async function loadDir(path) {
    currentPath = (path || "/").replace(/\/+$/, "") || "/";
    pathInput.value = currentPath;
    updateCommandPreview();
    if (server === "other" && !(otherConnected && otherConnected.connected)) {
      listBox.innerHTML = "";
      statusEl.textContent = "请先连接其他服务器";
      statusEl.className = "inline-msg warn";
      return;
    }
    listBox.innerHTML = `<div class="file-list-empty">加载中...</div>`;
    try {
      const params = new URLSearchParams({ server, mode, path: currentPath });
      if (mode === "container") params.set("container", containerSelect.value);
      const data = await api.get(`/api/platform/engine/list?${params}`);
      renderDir(data.entries || []);
    } catch (err) {
      listBox.innerHTML = "";
      statusEl.textContent = `目录加载失败: ${err.message}`;
      statusEl.className = "inline-msg error";
    }
  }

  function renderDir(entries) {
    listBox.innerHTML = "";
    statusEl.textContent = "";
    statusEl.className = "inline-msg muted";
    if (!entries.length) {
      listBox.innerHTML = `<div class="file-list-empty">(空目录)</div>`;
      return;
    }
    if (currentPath !== "/") {
      listBox.append(fileRow("📁 ..", () => loadDir(parentPath(currentPath)),
        "双击进入上级目录", null));
    }
    for (const entry of entries) {
      if (entry.type === "dir") {
        listBox.append(fileRow(`📁 ${entry.name}`,
          () => loadDir(joinPath(currentPath, entry.name)),
          "双击进入", null));
      } else {
        // 单击选择文件作为日志路径
        listBox.append(fileRow(`📄 ${entry.name}`, null, "点击选择", () => {
          pathInput.value = joinPath(currentPath, entry.name);
          updateCommandPreview();
          listBox.querySelectorAll(".file-item").forEach(r =>
            r.classList.remove("selected"));
        }));
      }
    }
  }

  function fileRow(label, onDouble, title, onSelect) {
    const row = el("div", { class: "file-item", title }, el("span", {}, label));
    if (onDouble) row.addEventListener("dblclick", onDouble);
    if (onSelect) {
      row.addEventListener("click", () => {
        onSelect();
        row.classList.add("selected");
      });
    }
    return row;
  }

  async function doGrep() {
    const body = { mode, server };
    if (mode !== "host") {
      body.container = containerSelect.value;
      if (!body.container) {
        statusEl.textContent = "请先选择引擎容器";
        statusEl.className = "inline-msg warn";
        return;
      }
    }
    if (mode !== "docker_logs") {
      body.path = pathInput.value.trim();
      if (!body.path) {
        statusEl.textContent = "请先填写/选择日志文件路径";
        statusEl.className = "inline-msg warn";
        return;
      }
    }

    grepBtn.disabled = true;
    grepBtn.textContent = "筛选中...";
    statusEl.textContent = "";
    try {
      const data = await api.post("/api/platform/engine/grep", body);
      if (data.command) cmdText.textContent = data.command;
      resultPre.textContent = data.output || data.error || "(无输出)";
      parsed = data.parsed ? { kv: data.kv, max_len: data.max_len } : null;
      if (data.parsed) {
        const parts = [];
        if (data.kv !== null && data.kv !== undefined) {
          parts.push(`单个DP组KV cache = ${data.kv.toLocaleString()} tokens`);
        }
        if (data.max_len !== null && data.max_len !== undefined) {
          parts.push(`最大请求长度 = ${data.max_len.toLocaleString()} tokens`);
        }
        parsedEl.textContent = "✓ " + parts.join("  |  ");
        parsedEl.className = "inline-msg ok";
        statusEl.textContent = "筛选完成, 可填入参数";
        statusEl.className = "inline-msg ok";
        fillBtn.disabled = false;
      } else {
        parsedEl.textContent = "未解析到 KV cache / 最大上下文数值";
        parsedEl.className = "inline-msg warn";
        statusEl.textContent = data.error
          ? `筛选失败: ${data.error}` : "请确认选择的是 vLLM 启动日志";
        statusEl.className = "inline-msg warn";
        fillBtn.disabled = true;
      }
    } catch (err) {
      statusEl.textContent = `筛选失败: ${err.message}`;
      statusEl.className = "inline-msg error";
      fillBtn.disabled = true;
    } finally {
      grepBtn.disabled = false;
      grepBtn.textContent = "🔍 筛选 KV cache";
    }
  }
  grepBtn.addEventListener("click", doGrep);

  function fillParams() {
    if (!parsed) return;
    const filled = [];
    if (parsed.kv !== null && parsed.kv !== undefined) {
      $("#kvCache").value = parsed.kv;
      filled.push(`单个DP组KV cache=${parsed.kv.toLocaleString()}`);
    }
    if (parsed.max_len !== null && parsed.max_len !== undefined) {
      $("#kvMaxLen").value = parsed.max_len;
      filled.push(`最大请求长度=${parsed.max_len.toLocaleString()}`);
    }
    // 最大上下文变化会影响长度选择栏的可用项, 立即重渲染
    renderLengthChips();
    m.close();
    toast(`已填入: ${filled.join("; ")}`, "success", 4000);
  }
  const fillBtn = el("button", { class: "btn btn-primary", type: "button" },
    "✓ 填入参数");
  fillBtn.disabled = true;
  fillBtn.addEventListener("click", fillParams);

  m.body.append(serverRow, otherServerBox, modeRow, containerRow, pathRow,
    listBox, actionRow, mapHint, cmdRow, cmdHint, resultPre, pasteBox, parsedEl);
  m.footer.append(
    el("button", { class: "btn btn-outline", onclick: m.close }, "取消"),
    fillBtn);

  otherServerBox.hidden = true;
  updateModeUI();
  return m;
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
