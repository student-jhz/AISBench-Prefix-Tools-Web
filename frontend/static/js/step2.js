/** 步骤 2: 容器准备 —— 选择已有容器 / 新建容器(容器资源池, 多用户) */

import { $, el, fmtBytes, setMsg, toast, confirmDialog, modal } from "./util.js";
import { api } from "./api.js";
import { G, saveForm, saveContainer, refreshState, switchExecChannel } from "./main.js";
import { browseRemoteDir } from "./components.js";


const NAME_SUFFIX_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

let containersCache = [];
let imagesCache = [];
let selectedImage = null;      // {name, ...}
let lastDeployRunning = false;
let busyPollTimer = null;      // busy 期间 5s 轮询, 任务结束后自动消除提示


export function initStep2() {
  initModeSwitch();
  initExistingContainers();
  initImageSelect();
  initImageUpload();
  initCodeUpload();
  initCreateForm();
  initWorkDir();
  initCurrentCard();

  $("#openContainerManageBtn").addEventListener("click", openContainerManage);
  $("#deployStartBtn").addEventListener("click", startCreateContainer);
  $("#deployCancelBtn").addEventListener("click", cancelDeploy);
  $("#deployLogClearBtn").addEventListener("click", () => G.consoles.deploy?.clear());

  // main.js 状态刷新钩子
  window.updateContainerInfo = updateCurrentCard;
  window.restoreContainerSelection = restoreSelection;
}


/* ============ 模式切换: 已有容器 / 新建容器 ============ */

function initModeSwitch() {
  $$radio("containerMode", value => {
    $("#containerExistingBox").hidden = value !== "existing";
    $("#containerCreateBox").hidden = value !== "create";
    if (value === "existing") refreshContainers();
    if (value === "create") refreshImages();
  });
}


/* ============ 已有容器列表 / 选择 ============ */

function initExistingContainers() {
  $("#refreshContainersBtn").addEventListener("click", refreshContainers);
  $("#showStoppedContainers").addEventListener("change", renderContainers);
}

async function refreshContainers() {
  setMsg("#containersMsg", "加载容器列表...", "warn");
  try {
    const data = await api.get("/api/containers/list", { silent: true });
    containersCache = data.containers || [];
    renderContainers();
    setMsg("#containersMsg", `共 ${containersCache.length} 个 aisbench 容器`, "muted");
  } catch (err) {
    setMsg("#containersMsg", err.message, "error");
  }
}

function renderContainers() {
  const tbody = $("#containersTableBody");
  tbody.innerHTML = "";
  const showStopped = $("#showStoppedContainers").checked;
  const items = containersCache.filter(c => showStopped || c.running);
  if (!items.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "5" }, "暂无容器, 可切换到「新建容器」创建")));
    return;
  }
  for (const c of items) {
    const tr = el("tr", { class: c.busy ? "row-busy" : "" });
    tr.append(
      el("td", { class: "mono" }, c.name),
      el("td", {}, c.image || "-"),
      el("td", {}, statusBadge(c)),
      el("td", {}, busyBadge(c)),
      el("td", {}, rowActions(c)),
    );
    tbody.append(tr);
  }
}

function statusBadge(c) {
  return el("span", { class: `tag ${c.running ? "tag-ok" : "tag-muted"}` },
    c.running ? "● 运行中" : "○ 已停止");
}

function busyBadge(c) {
  if (!c.running) return el("span", { class: "tag tag-muted" }, "-");
  if (!c.busy) return el("span", { class: "tag tag-ok" }, "空闲");
  // v2.0.5: 区分"本服务任务执行中"与"检测到他人测试进程"
  const label = c.executing_here
    ? (c.busy_source === "both" ? "⏳ 测试执行中(含外部进程)" : "⏳ 测试执行中")
    : "⏳ 检测到测试进程";
  return el("span", {
    class: "tag tag-warn",
    title: c.busy_detail || "后台任务或容器内测试进程占用中",
  }, label);
}

function rowActions(c) {
  const box = el("div", { class: "cases-actions" });
  if (!c.running) {
    box.append(el("button", {
      class: "btn btn-ghost btn-sm", title: "启动容器",
      onclick: async () => {
        try {
          await api.post("/api/containers/start", { name: c.name });
          toast(`容器 ${c.name} 已启动`, "success");
          refreshContainers();
        } catch (err) { toast(err.message, "error"); }
      },
    }, "▶ 启动"));
  }
  box.append(el("button", {
    class: `btn btn-sm ${c.busy ? "btn-outline" : "btn-primary"}`,
    onclick: () => selectContainer(c.name),
  }, c.busy ? "仍要选择" : "选择"));
  return box;
}

async function selectContainer(name, force = false) {
  try {
    const data = await api.post("/api/containers/select", { name, force });
    if (!data.selected && data.busy) {
      // 正在执行测试: 让用户明确知晓风险, 建议新建容器
      const ok = await confirmDialog(
        `容器 ${name} 正在执行测试:\n  ${data.busy_detail || "(检测到测试进程)"}\n\n` +
        "不建议选择该容器 —— 并发测试会互相干扰。\n" +
        "建议「新建容器」执行新测试任务。\n\n仍要选择该容器吗?",
        { title: "容器正在执行测试", danger: true, okText: "仍要选择" });
      if (!ok) return;
      return selectContainer(name, true);
    }
    applyContainer({
      ...data.info,
      busy: data.busy,
      busy_detail: data.busy_detail,
      executing_here: data.executing_here,
      process_detected: data.process_detected,
      busy_source: data.busy_source,
      task: data.task,
    });
  } catch (err) {
    toast(err.message, "error", 6000);
  }
}

/** 应用选中的容器: 记住选择(本浏览器), 切换执行日志通道, 刷新卡片 */
export function applyContainer(info) {
  G.containerInfo = { ...info };
  saveContainer(info.name);
  switchExecChannel(info.name);
  updateCurrentCard();
  window.syncConfigFromContainer?.();
  updateNavLocksSafe();
  toast(`已选择容器: ${info.name}`, "success");
}

function updateNavLocksSafe() {
  import("./main.js").then(m => m.refreshState({ silent: true }));
}


/* ============ 镜像选择(已有镜像优先) ============ */

function initImageSelect() {
  $("#refreshImagesBtn").addEventListener("click", refreshImages);
  $("#imageFilterInput").addEventListener("input", renderImages);
  $("#imageFilterAll").addEventListener("change", renderImages);
}

async function refreshImages() {
  setMsg("#imageSelectMsg", "加载镜像列表...", "warn");
  try {
    const data = await api.get("/api/containers/images", { silent: true });
    imagesCache = data.images || [];
    renderImages();
    setMsg("#imageSelectMsg",
      imagesCache.length ? `共 ${imagesCache.length} 个镜像` : "执行机上没有镜像", "muted");
  } catch (err) {
    setMsg("#imageSelectMsg", err.message, "error");
  }
}

function renderImages() {
  const tbody = $("#imagesTableBody");
  tbody.innerHTML = "";
  const showAll = $("#imageFilterAll").checked;
  const keyword = $("#imageFilterInput").value.trim().toLowerCase();
  const items = showAll || !keyword
    ? imagesCache
    : imagesCache.filter(img => img.name.toLowerCase().includes(keyword));
  if (!items.length) {
    tbody.append(el("tr", { class: "empty-row" },
      el("td", { colspan: "5" },
        "未找到可用镜像 —— 请切换为「上传 tar 包」或「执行机上已有 tar 包」")));
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


/* ============ 镜像 tar 上传(仅当执行机缺镜像时需要) ============ */

function initImageUpload() {
  $$radio("imageSource", value => {
    $("#imageExistingBox").hidden = value !== "existing";
    $("#imageUploadBox").hidden = value !== "upload";
    $("#imageRemoteBox").hidden = value !== "remote";
    if (value === "existing" && !imagesCache.length) refreshImages();
  });

  const dropArea = $("#imageDropArea");
  const fileInput = $("#imageFileInput");
  dropArea.addEventListener("click", () => fileInput.click());
  dropArea.addEventListener("dragover", e => { e.preventDefault(); dropArea.classList.add("dragover"); });
  dropArea.addEventListener("dragleave", () => dropArea.classList.remove("dragover"));
  dropArea.addEventListener("drop", e => {
    e.preventDefault();
    dropArea.classList.remove("dragover");
    if (e.dataTransfer.files.length) uploadImage(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", e => {
    if (e.target.files.length) uploadImage(e.target.files[0]);
  });

  $("#imageRemoteCheckBtn").addEventListener("click", checkRemoteTar);
}

async function uploadImage(file) {
  const progressBox = $("#imageUploadProgress");
  const bar = $("#imageProgressBar");
  const text = $("#imageProgressText");
  const resultBox = $("#imageUploadResult");
  progressBox.hidden = false;
  resultBox.hidden = true;
  try {
    const data = await api.upload("/api/upload/image", file, (loaded, total) => {
      const pct = Math.round(loaded * 100 / total);
      bar.style.width = `${pct}%`;
      text.textContent = `${fmtBytes(loaded)} / ${fmtBytes(total)} (${pct}%)`;
    });
    G.form.imageUpload = data;
    saveForm();
    resultBox.hidden = false;
    resultBox.className = "upload-result";
    resultBox.innerHTML = "";
    resultBox.append(
      el("span", {}, "✓ 上传完成"),
      el("span", { class: "meta" }, `${file.name} · ${fmtBytes(data.size)}`),
      data.image_name
        ? el("span", { class: "meta" }, `识别镜像: ${data.image_name}`)
        : el("span", { class: "meta" }, "未识别到镜像名, 创建时由 docker load 识别"));
    toast("镜像包上传完成", "success");
  } catch (err) {
    resultBox.hidden = false;
    resultBox.className = "upload-result error";
    resultBox.textContent = `✗ 上传失败: ${err.message}`;
  } finally {
    progressBox.hidden = true;
    fileInput.value = "";
  }
}

async function checkRemoteTar() {
  const path = $("#imageRemotePath").value.trim();
  if (!path) {
    setMsg("#imageRemoteMsg", "请输入远程 tar 包路径", "error");
    return;
  }
  setMsg("#imageRemoteMsg", "检查中...", "warn");
  try {
    const data = await api.post("/api/deploy/check-remote-tar", { remote_path: path });
    if (data.exists) {
      G.form.imageRemote = { path, image_name: data.image_name };
      saveForm();
      setMsg("#imageRemoteMsg", data.image_name
        ? `✓ 文件存在，识别镜像: ${data.image_name}`
        : "✓ 文件存在（无法预读镜像名，创建时识别）", "ok");
    } else {
      G.form.imageRemote = null;
      setMsg("#imageRemoteMsg", "✗ 文件不存在", "error");
    }
  } catch (err) {
    setMsg("#imageRemoteMsg", err.message, "error");
  }
}


/* ============ 代码 zip 上传 ============ */

function initCodeUpload() {
  $$radio("codeSource", value => {
    $("#codeBuiltinBox").hidden = value !== "builtin";
    $("#codeZipBox").hidden = value !== "zip";
  });

  const dropArea = $("#codeDropArea");
  const fileInput = $("#codeFileInput");
  dropArea.addEventListener("click", () => fileInput.click());
  dropArea.addEventListener("dragover", e => { e.preventDefault(); dropArea.classList.add("dragover"); });
  dropArea.addEventListener("dragleave", () => dropArea.classList.remove("dragover"));
  dropArea.addEventListener("drop", e => {
    e.preventDefault();
    dropArea.classList.remove("dragover");
    if (e.dataTransfer.files.length) uploadCode(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", e => {
    if (e.target.files.length) uploadCode(e.target.files[0]);
  });
}

async function uploadCode(file) {
  const resultBox = $("#codeUploadResult");
  try {
    const data = await api.upload("/api/upload/code-zip", file);
    G.form.codeUpload = data;
    saveForm();
    resultBox.hidden = false;
    resultBox.className = "upload-result";
    resultBox.innerHTML = "";
    resultBox.append(
      el("span", {}, "✓ 上传完成"),
      el("span", { class: "meta" }, `${file.name} · ${fmtBytes(data.size)}`));
    toast("代码包上传完成", "success");
  } catch (err) {
    resultBox.hidden = false;
    resultBox.className = "upload-result error";
    resultBox.textContent = `✗ 上传失败: ${err.message}`;
  } finally {
    fileInput.value = "";
  }
}


/* ============ 新建容器表单 ============ */

function initCreateForm() {
  // 模型目录: 远程浏览 + 持久化
  const mountInput = $("#modelMountPath");
  mountInput.value = G.form.modelMount || "";
  mountInput.addEventListener("change", () => {
    G.form.modelMount = mountInput.value.trim();
    saveForm();
  });
  $("#modelMountBrowseBtn").addEventListener("click", () => {
    browseRemoteDir({
      title: "浏览远程目录 - 选择模型(父)目录",
      onPick: path => {
        mountInput.value = path;
        G.form.modelMount = path;
        saveForm();
      },
    });
  });

  // 容器名称: 前缀 aisbench_ 由 UI 呈现, 输入为后缀
  const nameInput = $("#containerNameInput");
  nameInput.value = G.form.containerName || "";
  nameInput.addEventListener("input", () => {
    G.form.containerName = nameInput.value.trim();
    saveForm();
    validateNameInput();
  });
  validateNameInput();

  // 恢复远程tar路径与上传记录
  if (G.form.imageRemote?.path) $("#imageRemotePath").value = G.form.imageRemote.path;
  if (G.form.imageUpload) {
    const resultBox = $("#imageUploadResult");
    const d = G.form.imageUpload;
    resultBox.hidden = false;
    resultBox.className = "upload-result";
    resultBox.innerHTML = "";
    resultBox.append(
      el("span", {}, `✓ 已上传: ${d.filename}`),
      el("span", { class: "meta" }, fmtBytes(d.size)),
      d.image_name ? el("span", { class: "meta" }, `镜像: ${d.image_name}`) : null);
  }
}

function validateNameInput() {
  const value = $("#containerNameInput").value.trim();
  if (!value) {
    setMsg("#containerNameMsg", "完整名称将为 aisbench_" + value, "muted");
    return false;
  }
  if (!NAME_SUFFIX_RE.test(value)) {
    setMsg("#containerNameMsg",
      "✗ 仅允许字母/数字/下划线/点/连字符, 且以字母或数字开头", "error");
    return false;
  }
  setMsg("#containerNameMsg", `✓ 完整名称: aisbench_${value}`, "ok");
  return true;
}

function getImageSelection() {
  const mode = getRadio("imageSource");
  if (mode === "upload") {
    return { type: "upload", upload_id: G.form.imageUpload?.id || "" };
  }
  if (mode === "remote") {
    return { type: "remote", remote_path: $("#imageRemotePath").value.trim() };
  }
  return { type: "existing", name: selectedImage?.name || "" };
}

function getCodeSelection() {
  const source = getRadio("codeSource");
  if (source === "zip") {
    return { source: "zip", zip_upload_id: G.form.codeUpload?.id || "" };
  }
  return { source: "builtin" };
}


/* ============ 工作目录(高级选项) ============ */

function initWorkDir() {
  const workDirEl = $("#workDir");
  workDirEl.value = G.form.workDir || "/tmp/AISBench_Prefix_Tools";
  workDirEl.addEventListener("input", () => {
    G.form.workDir = workDirEl.value;
    saveForm();
  });
  $("#workDirCheckBtn").addEventListener("click", checkWorkDir);
  $("#workDirCleanBtn").addEventListener("click", cleanWorkDir);
}

async function checkWorkDir() {
  const workDir = $("#workDir").value.trim() || "/tmp/AISBench_Prefix_Tools";
  setMsg("#workDirMsg", "检查中...", "warn");
  try {
    const data = await api.get(
      `/api/deploy/preflight?work_dir=${encodeURIComponent(workDir)}`);
    if (!data.exists) {
      setMsg("#workDirMsg", "目录不存在(创建容器时将自动创建)", "muted");
    } else if (!data.has_content) {
      setMsg("#workDirMsg", "目录存在且为空", "ok");
    } else {
      setMsg("#workDirMsg",
        `目录已存在, 含 ${data.entries.length} 项内容(可先清理避免残留旧代码)`, "warn");
    }
  } catch (err) {
    setMsg("#workDirMsg", err.message, "error");
  }
}

async function cleanWorkDir() {
  const workDir = $("#workDir").value.trim();
  if (!workDir) {
    setMsg("#workDirMsg", "请先填写远程工作目录", "error");
    return;
  }
  const ok = await confirmDialog(
    `将删除远程执行机上的工作目录:\n${workDir}\n\n` +
    "其中的镜像tar包/代码等文件将被清除(不影响已有容器), 是否继续?",
    { title: "清理工作目录", danger: true, okText: "清理" });
  if (!ok) return;
  try {
    await api.post("/api/cleanup",
      { containers: [], clean_work_dir: true, work_dir: workDir });
    toast("清理任务已启动, 详情见容器操作日志", "info");
  } catch (err) {
    setMsg("#workDirMsg", err.message, "error");
  }
}


/* ============ 创建容器(后台任务) ============ */

async function startCreateContainer() {
  const image = getImageSelection();
  const modelMount = $("#modelMountPath").value.trim();
  const nameSuffix = $("#containerNameInput").value.trim();
  const workDir = $("#workDir").value.trim() || "/tmp/AISBench_Prefix_Tools";

  // 前置校验
  if (image.type === "existing" && !image.name) {
    toast("请先在镜像列表中选择镜像", "warning");
    return;
  }
  if (image.type === "upload" && !image.upload_id) {
    toast("请先上传镜像 tar 包", "warning");
    return;
  }
  if (image.type === "remote" && !image.remote_path) {
    toast("请填写远程镜像 tar 路径", "warning");
    return;
  }
  if (!modelMount) {
    toast("请填写要映射进容器的模型(父)目录", "warning");
    return;
  }
  if (!nameSuffix || !NAME_SUFFIX_RE.test(nameSuffix)) {
    toast("容器名称不合法: aisbench_ + 字母/数字/下划线/点/连字符", "warning");
    validateNameInput();
    return;
  }
  const code = getCodeSelection();
  if (code.source === "zip" && !code.zip_upload_id) {
    toast("请先上传代码 zip 包", "warning");
    return;
  }

  G.form.workDir = workDir;
  saveForm();

  const containerName = `aisbench_${nameSuffix}`;
  const btn = $("#deployStartBtn");
  btn.disabled = true;
  btn.textContent = "创建中...";
  $("#deployCancelBtn").disabled = false;
  $("#deployPhase").textContent = "⏳ 任务启动中...";
  lastDeployRunning = true;

  try {
    await api.post("/api/deploy/start", {
      image,
      model_mount: modelMount,
      container_name: containerName,
      work_dir: workDir,
      code,
    });
    toast("容器创建任务已启动", "success");
    await refreshState({ silent: true });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "🐳 创建容器";
    $("#deployCancelBtn").disabled = true;
    $("#deployPhase").textContent = "";
    lastDeployRunning = false;
    toast(err.message, "error", 6000);
  }
}

async function cancelDeploy() {
  try {
    const data = await api.post("/api/deploy/cancel");
    toast(data.ok ? "已发送取消请求" : "任务未在运行", data.ok ? "info" : "warning");
  } catch (err) {
    toast(err.message, "error");
  }
}


/* ============ 创建任务状态轮询(main.js 高频调用) ============ */

export async function pollDeployStatus() {
  let st;
  try {
    st = await api.get("/api/deploy/status", { silent: true });
  } catch {
    return;
  }
  $("#deployPhase").textContent = st.running
    ? `⏳ [${st.status}] ${st.phase || "创建容器中"}...`
    : "";

  if (lastDeployRunning && !st.running) {
    if (st.status === "done") {
      toast("容器创建完成!", "success");
      // 自动选择刚创建的容器, 进入后续步骤
      const name = st.result?.container_name;
      if (name) await selectContainer(name, true);
      refreshContainers();
    } else if (st.status === "error") {
      toast(`容器创建失败: ${st.error}`, "error", 8000);
    } else if (st.status === "cancelled") {
      toast("容器创建已取消", "info");
    }
    await refreshState({ silent: true });
  }
  lastDeployRunning = st.running;
}

export function updateDeployTaskUI(tasks) {
  const t = tasks?.deploy;
  const running = !!t?.running;
  const startBtn = $("#deployStartBtn");
  startBtn.disabled = running;
  startBtn.textContent = running ? "创建中..." : "🐳 创建容器";
  $("#deployCancelBtn").disabled = !running;
  $("#workDirCheckBtn").disabled = running;
  $("#workDirCleanBtn").disabled = running;
  if (!running) $("#deployPhase").textContent = "";
}


/* ============ 当前容器卡片 ============ */

function initCurrentCard() {
  $("#refreshCurrentBtn").addEventListener("click", () => updateCurrentCard(true));
  updateCurrentCard();
}

async function updateCurrentCard(force = false) {
  const card = $("#currentContainerCard");
  if (!G.containerInfo?.name) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  renderCurrentGrid();
  await refreshCurrentStatus();
  if (force) toast("容器状态已刷新", "info", 1500);
}

/** 渲染当前容器信息网格(从 G.containerInfo) */
function renderCurrentGrid() {
  const grid = $("#currentContainerGrid");
  const info = G.containerInfo;
  if (!info?.name) return;
  grid.innerHTML = "";

  const mounts = (info.model_mounts || [])
    .map(m => `${m.host} → ${m.container}`).join("\n") || "-";
  grid.append(
    infoItem("容器名称", info.name),
    infoItem("镜像", info.image || "-"),
    infoItem("状态", info.running ? "运行中" : `已停止 (${info.state || "-"})`),
    infoItem("测试执行", execStateText(info)),
    infoItem("模型目录挂载", mounts),
    infoItem("代码路径(宿主机)", info.code_host_path || "-"),
  );
}

/** 测试执行状态文案: 注册表(本服务任务)权威, docker top 仅补充他人占用 */
function execStateText(info) {
  if (info.executing_here) {
    const task = info.task || {};
    if (task.script_running) return "测试执行中";
    if (task.status === "running") return `收尾中(${task.phase || "日志处理"})`;
    return "空闲";
  }
  if (info.process_detected) return "检测到测试进程(可能他人使用)";
  return "空闲";
}

/** 查询实时状态(busy/running), 回填后重渲染信息网格与横幅 */
async function refreshCurrentStatus() {
  const name = G.containerInfo?.name;
  if (!name) return;
  let st = null;
  try {
    st = await api.get(
      `/api/containers/busy?name=${encodeURIComponent(name)}`, { silent: true });
    G.containerInfo = { ...G.containerInfo, ...st, name };
  } catch { /* 忽略实时状态错误 */ }
  renderCurrentGrid();
  renderCurrentBanner();
  // 同步步骤5的执行容器卡片(直接复用本次查询结果, 不重复请求)
  if (st) window.renderExecContainerCard?.(st);
  // busy 期间保持轮询: 任务结束后界面自动消除"测试执行中"(v2.0.5)
  ensureBusyPolling();
}

/** busy 状态轮询: 每 5s 刷新一次 /busy, 不再依赖用户手动刷新 */
function ensureBusyPolling() {
  const info = G.containerInfo;
  if (info?.busy) {
    if (!busyPollTimer) {
      busyPollTimer = setInterval(async () => {
        const name = G.containerInfo?.name;
        if (!name) { stopBusyPolling(); return; }
        try {
          const st = await api.get(
            `/api/containers/busy?name=${encodeURIComponent(name)}`,
            { silent: true });
          G.containerInfo = { ...G.containerInfo, ...st, name };
          renderCurrentGrid();
          renderCurrentBanner();
          window.renderExecContainerCard?.(st);
          if (!st.busy) {
            stopBusyPolling();
            window.pollRunningTask?.();
          }
        } catch { /* SSH 短暂失败时下轮再试 */ }
      }, 5000);
    }
  } else {
    stopBusyPolling();
  }
}

function stopBusyPolling() {
  if (busyPollTimer) {
    clearInterval(busyPollTimer);
    busyPollTimer = null;
  }
}

/** 渲染 busy / 未运行横幅(含启动按钮) */
function renderCurrentBanner() {
  const banner = $("#currentBusyBanner");
  const info = G.containerInfo;
  banner.hidden = true;
  banner.innerHTML = "";
  if (!info?.name) return;
  if (info.busy) {
    banner.hidden = false;
    banner.className = "busy-banner warn";
    const task = info.task || {};
    let text;
    if (info.executing_here) {
      text = task.script_running
        ? `⏳ 该容器正在执行测试${task.run_id ? ` (run: ${task.run_id})` : ""} —— `
        : `⏳ 测试脚本已结束, 正在${task.phase || "处理日志/结果"} —— 容器即将空闲。`;
    } else {
      text = "⏳ 检测到容器内测试进程(可能由其他用户/手工启动)";
    }
    if (info.busy_detail) text += `\n进程: ${info.busy_detail}`;
    text += info.executing_here
      ? "\n如需执行新测试任务, 建议新建一个容器。"
      : "\n不建议选择该容器, 建议新建容器执行新测试任务。";
    banner.textContent = text;
  } else if (!info.running) {
    banner.hidden = false;
    banner.className = "busy-banner error";
    banner.append(
      el("span", {}, "⚠ 容器未运行, 无法执行测试。"),
      el("button", {
        class: "btn btn-outline btn-sm",
        onclick: async () => {
          try {
            await api.post("/api/containers/start", { name: info.name });
            toast(`容器 ${info.name} 已启动`, "success");
            await reinspectCurrentContainer();
          } catch (err) { toast(err.message, "error"); }
        },
      }, "▶ 启动容器"));
  }
}

/** 重新 inspect 当前容器(force select), 获取最新镜像/状态/挂载后刷新卡片 */
async function reinspectCurrentContainer() {
  const name = G.containerInfo?.name;
  if (!name) return;
  try {
    const data = await api.post("/api/containers/select",
      { name, force: true }, { silent: true });
    if (data.selected) {
      G.containerInfo = { ...G.containerInfo, ...data.info,
        busy: data.busy, busy_detail: data.busy_detail,
        executing_here: data.executing_here,
        process_detected: data.process_detected,
        busy_source: data.busy_source, task: data.task };
    }
  } catch { /* 容器可能已被删除 */ }
  updateCurrentCard();
  window.syncConfigFromContainer?.();
}

function infoItem(label, value) {
  return el("div", { class: "info-item" },
    el("div", { class: "info-label" }, label),
    el("div", { class: "info-value" }, String(value ?? "-")));
}


/* ============ 页面刷新后恢复容器选择 ============ */

async function restoreSelection() {
  updateCurrentCard();
  if (!G.container || !G.state?.ssh?.connected) return;
  try {
    const data = await api.post(
      "/api/containers/select", { name: G.container, force: true }, { silent: true });
    if (data.selected) {
      G.containerInfo = { ...data.info,
        busy: data.busy, busy_detail: data.busy_detail,
        executing_here: data.executing_here,
        process_detected: data.process_detected,
        busy_source: data.busy_source, task: data.task };
      switchExecChannel(data.info.name);
      updateCurrentCard();
      updateNavLocksSafe();
    }
  } catch {
    // 容器可能已被清理: 清除本浏览器的选择
    saveContainer("");
    G.containerInfo = null;
    updateCurrentCard();
  }
}


/* ============ 容器管理(按名称勾选清理) ============ */

export async function openContainerManage() {
  const m = modal({ title: "容器管理 - 按名称清理", large: true });
  const listBody = el("tbody");
  const statusEl = el("div", { class: "inline-msg" });
  const allCheck = el("input", { type: "checkbox" });
  const cleanDirCheck = el("input", { type: "checkbox" });
  const workDirInput = el("input", {
    type: "text", value: G.form.workDir || "/tmp/AISBench_Prefix_Tools",
    style: "flex:1",
  });

  async function load() {
    listBody.innerHTML = "";
    statusEl.textContent = "加载容器列表...";
    try {
      const data = await api.get("/api/containers/list", { silent: true });
      const items = data.containers || [];
      statusEl.textContent = `共 ${items.length} 个 aisbench 容器`;
      if (!items.length) {
        listBody.append(el("tr", { class: "empty-row" },
          el("td", { colspan: "5" }, "暂无容器")));
        return;
      }
      for (const c of items) {
        const cb = el("input", { type: "checkbox", "data-name": c.name });
        const tr = el("tr", { class: c.busy ? "row-busy" : "" });
        tr.append(
          el("td", {}, cb),
          el("td", { class: "mono" }, c.name),
          el("td", {}, c.image || "-"),
          el("td", {}, statusBadge(c)),
          el("td", {}, busyBadge(c)));
        listBody.append(tr);
      }
    } catch (err) {
      statusEl.textContent = `加载失败: ${err.message}`;
    }
  }

  allCheck.addEventListener("change", () => {
    listBody.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.checked = allCheck.checked;
    });
  });

  m.body.append(
    el("div", { class: "hint-text", style: "margin-bottom:8px" },
      "仅显示 aisbench 前缀容器；请勾选要删除的容器名 —— ",
      "不会删除未勾选的任何容器；正在执行测试的容器会被自动跳过。"),
    el("div", { class: "table-wrap" },
      el("table", { class: "data-table" },
        el("thead", {}, el("tr", {},
          el("th", {}, allCheck), el("th", {}, "容器名称"),
          el("th", {}, "镜像"), el("th", {}, "状态"), el("th", {}, "测试执行"))),
        listBody)),
    el("div", { class: "form-actions", style: "margin-top:12px" },
      el("label", { class: "checkbox-inline" }, cleanDirCheck, " 同时清理远程工作目录 "),
      el("div", { class: "input-group", style: "flex:1" }, workDirInput)),
    statusEl);

  m.footer.append(
    el("button", { class: "btn btn-outline", onclick: () => m.close() }, "关闭"),
    el("button", {
      class: "btn btn-outline",
      onclick: load,
    }, "↻ 刷新"),
    el("button", {
      class: "btn btn-danger",
      onclick: async () => {
        const names = [...listBody.querySelectorAll('input[type="checkbox"]:checked')]
          .map(cb => cb.dataset.name);
        if (!names.length && !cleanDirCheck.checked) {
          statusEl.textContent = "请先勾选要清理的容器或勾选清理工作目录";
          return;
        }
        const ok = await confirmDialog(
          (names.length
            ? `将删除以下容器:\n  ${names.join("\n  ")}\n\n`
            : "") +
          (cleanDirCheck.checked
            ? `将清理远程工作目录:\n  ${workDirInput.value.trim()}\n\n` : "") +
          "测试结果历史不受影响。此操作不可恢复, 是否继续?",
          { title: "确认清理", danger: true, okText: "清理" });
        if (!ok) return;
        try {
          await api.post("/api/cleanup", {
            containers: names,
            clean_work_dir: cleanDirCheck.checked,
            work_dir: cleanDirCheck.checked ? workDirInput.value.trim() : null,
          });
          statusEl.textContent = "清理任务已启动, 请在「容器操作日志」查看进度...";
          // 轮询清理任务状态
          const timer = setInterval(async () => {
            try {
              const st = (await api.get("/api/state", { silent: true })).tasks.cleanup;
              if (!st.running) {
                clearInterval(timer);
                const removed = st.result?.removed || [];
                const skipped = st.result?.skipped || [];
                statusEl.textContent =
                  `清理完成: 删除 ${removed.length} 个` +
                  (skipped.length ? `, 跳过 ${skipped.length} 个(执行中)` : "");
                toast("环境清理完成", "success");
                if (G.container && removed.includes(G.container)) {
                  saveContainer("");
                  G.containerInfo = null;
                  switchExecChannel("");
                  updateCurrentCard();
                }
                await refreshState({ silent: true });
                load();
              }
            } catch { /* ignore */ }
          }, 1000);
        } catch (err) {
          statusEl.textContent = `清理失败: ${err.message}`;
        }
      },
    }, "🧹 开始清理"));

  load();
  return m;
}


/* ============ 工具 ============ */

function getRadio(name) {
  const checked = document.querySelector(`input[name="${name}"]:checked`);
  return checked ? checked.value : null;
}

function $$radio(name, onChange) {
  document.querySelectorAll(`input[name="${name}"]`).forEach(radio => {
    radio.addEventListener("change", () => onChange(radio.value));
  });
}
