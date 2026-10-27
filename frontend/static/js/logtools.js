/** 远程日志工具 - KV cache 筛选 / 日志查看 */

import { $, $$, el, fmtBytes, setMsg, toast, modal, copyText } from "./util.js";
import { api } from "./api.js";


let currentPath = "/";
let selectedFile = null;
let lastParsed = null;


export function initLogtools() {
  // 服务器切换
  $$radio("ltServer", value => {
    $("#ltOtherServerBox").hidden = value !== "other";
    refreshStatus();
  });

  // 其他服务器连接
  $("#ltConnectBtn").addEventListener("click", connectOther);
  $("#ltDisconnectBtn").addEventListener("click", disconnectOther);

  // 日志来源切换
  $$radio("ltMode", value => {
    $("#ltContainerBox").hidden = value === "host";
    $("#ltFileBrowserBox").hidden = value === "docker_logs";
    selectedFile = null;
  });

  // 容器列表
  $("#ltRefreshContainersBtn").addEventListener("click", refreshContainers);

  // 目录浏览
  $("#ltGotoBtn").addEventListener("click",
    () => loadDir($("#ltPath").value.trim() || "/"));

  // 筛选 / 查看 / 填入
  $("#ltGrepBtn").addEventListener("click", grepKv);
  $("#ltViewBtn").addEventListener("click", viewLog);
  $("#ltFillBtn").addEventListener("click", fillStep4);

  window.refreshLogtoolsStatus = refreshStatus;
  refreshStatus();
}


function getRadio(name) {
  const checked = document.querySelector(`input[name="${name}"]:checked`);
  return checked ? checked.value : null;
}


function $$radio(name, onChange) {
  document.querySelectorAll(`input[name="${name}"]`).forEach(radio => {
    radio.addEventListener("change", () => onChange(radio.value));
  });
}


/* ============ 服务器状态 ============ */

async function refreshStatus() {
  let s;
  try {
    s = await api.get("/api/logtools/status", { silent: true });
  } catch {
    return;
  }
  const sess = s.session.connected
    ? `● 当前执行机已连接 (${s.session.host})`
    : "○ 当前执行机未连接";
  const other = s.other.connected
    ? `● 其他服务器已连接 (${s.other.host})`
    : "○ 其他服务器未连接";
  $("#ltSessionStatus").textContent = `${sess} · ${other}`;
}


async function connectOther() {
  const host = $("#ltHost").value.trim();
  const username = $("#ltUser").value.trim();
  const password = $("#ltPassword").value || null;
  if (!host || !username) {
    setMsg("#ltConnMsg", "请填写 IP 和用户名", "error");
    return;
  }
  if (!password) {
    setMsg("#ltConnMsg", "请填写密码", "error");
    return;
  }
  const btn = $("#ltConnectBtn");
  btn.disabled = true;
  setMsg("#ltConnMsg", "连接中...", "warn");
  try {
    const data = await api.post("/api/logtools/other/connect", {
      host,
      port: parseInt($("#ltPort").value, 10) || 22,
      username,
      password,
      timeout: 10,
    });
    setMsg("#ltConnMsg", data.message, "ok");
    toast("其他服务器连接成功", "success");
    refreshStatus();
  } catch (err) {
    setMsg("#ltConnMsg", err.message, "error");
  } finally {
    btn.disabled = false;
  }
}


async function disconnectOther() {
  try {
    await api.post("/api/logtools/other/disconnect");
    setMsg("#ltConnMsg", "已断开", "muted");
    refreshStatus();
  } catch (err) {
    setMsg("#ltConnMsg", err.message, "error");
  }
}


/* ============ 容器列表 / 目录浏览 ============ */

async function refreshContainers() {
  const server = getRadio("ltServer");
  const select = $("#ltContainerSelect");
  select.innerHTML = "";
  select.append(el("option", { value: "" }, "加载中..."));
  try {
    const data = await api.get(
      `/api/logtools/containers?server=${server}`);
    select.innerHTML = "";
    if (!data.containers.length) {
      select.append(el("option", { value: "" }, "(无运行中的容器)"));
      return;
    }
    for (const c of data.containers) {
      select.append(el("option", { value: c.name },
        `${c.name} (${c.image})`));
    }
  } catch (err) {
    select.innerHTML = "";
    select.append(el("option", { value: "" }, "(加载失败)"));
    toast(err.message, "error");
  }
}


function containerName() {
  return $("#ltContainerSelect").value || "";
}


async function loadDir(path) {
  const mode = getRadio("ltMode");
  const server = getRadio("ltServer");
  if (mode === "docker_logs") return;

  setMsg("#ltFileMsg", "加载中...", "warn");
  let url = `/api/logtools/list?server=${server}&mode=${mode}` +
    `&path=${encodeURIComponent(path)}`;
  if (mode === "container") {
    if (!containerName()) {
      setMsg("#ltFileMsg", "请先选择容器", "error");
      return;
    }
    url += `&container=${encodeURIComponent(containerName())}`;
  }
  try {
    const data = await api.get(url);
    currentPath = data.path;
    $("#ltPath").value = data.path;
    selectedFile = null;
    renderFileList(data.entries);
    setMsg("#ltFileMsg", `共 ${data.entries.length} 项`, "muted");
  } catch (err) {
    setMsg("#ltFileMsg", err.message, "error");
  }
}


function renderFileList(entries) {
  const listBox = $("#ltFileList");
  listBox.innerHTML = "";
  if (!entries.length) {
    listBox.append(el("div", { class: "file-list-empty" }, "(空目录)"));
    return;
  }
  for (const entry of entries) {
    const isDir = entry.type === "dir";
    const fullPath = joinPath(currentPath, entry.name);
    const row = el("div", {
      class: "file-item",
      title: isDir ? "双击进入" : "单击选择",
    },
      el("span", { class: "file-icon" }, isDir ? "📁" : "📄"),
      el("span", {}, entry.name),
      !isDir && entry.size != null
        ? el("span", { class: "file-size" }, fmtBytes(entry.size))
        : null);

    if (isDir) {
      row.addEventListener("dblclick", () => loadDir(fullPath));
    } else {
      row.addEventListener("click", () => {
        $$("#ltFileList .file-item").forEach(r =>
          r.classList.remove("selected"));
        row.classList.add("selected");
        selectedFile = fullPath;
      });
    }
    listBox.append(row);
  }
}


function joinPath(base, name) {
  const b = (base || "/").replace(/\/+$/, "");
  return `${b}/${name}`;
}


/* ============ KV 筛选 / 日志查看 ============ */

function buildRequestBody() {
  const mode = getRadio("ltMode");
  const body = { server: getRadio("ltServer"), mode };
  if (mode !== "docker_logs") {
    const path = selectedFile || $("#ltPath").value.trim();
    if (!path) {
      toast("请先浏览并选择日志文件", "warning");
      return null;
    }
    body.path = path;
  }
  if (mode !== "host") {
    if (!containerName()) {
      toast("请先选择容器", "warning");
      return null;
    }
    body.container = containerName();
  }
  return body;
}


async function grepKv() {
  const body = buildRequestBody();
  if (!body) return;

  const btn = $("#ltGrepBtn");
  btn.disabled = true;
  btn.textContent = "筛选中...";
  try {
    const data = await api.post("/api/logtools/grep", body);
    const cmdBox = $("#ltCommandDisplay");
    cmdBox.hidden = false;
    cmdBox.textContent = data.command;
    $("#ltGrepResult").textContent = data.output || data.error || "(无输出)";
    renderParsedValues(data);
  } catch (err) {
    toast(err.message, "error", 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔍 筛选 KV cache";
  }
}


function renderParsedValues(data) {
  const box = $("#ltParsedValues");
  box.innerHTML = "";
  lastParsed = data.parsed ? { kv: data.kv, max_len: data.max_len } : null;
  $("#ltFillBtn").disabled = !data.parsed;

  if (!data.parsed) {
    box.append(el("span", {
      class: "parsed-pill",
      style: "background:#fef3c7;color:#b45309",
    }, "未解析到 KV cache 信息"));
    return;
  }
  if (data.kv != null) {
    box.append(el("span", { class: "parsed-pill" },
      `GPU KV cache size: ${data.kv.toLocaleString()} tokens`));
  }
  if (data.max_len != null) {
    box.append(el("span", { class: "parsed-pill" },
      `Maximum concurrency: ${data.max_len.toLocaleString()} tokens`));
  }
}


async function viewLog() {
  const body = buildRequestBody();
  if (!body) return;

  const btn = $("#ltViewBtn");
  btn.disabled = true;
  try {
    const data = await api.post("/api/logtools/view", body);
    const cmdBox = $("#ltCommandDisplay");
    cmdBox.hidden = false;
    cmdBox.textContent = data.command;

    const m = modal({
      title: `日志内容${data.size ? ` (尾部 ${fmtBytes(data.size)})` : ""}`,
      large: true,
    });
    m.body.append(el("pre", { class: "log-viewer" }, data.content));
    m.footer.append(
      el("button", {
        class: "btn btn-outline",
        onclick: () => copyText(data.content),
      }, "复制内容"),
      el("button", { class: "btn btn-primary", onclick: m.close }, "关闭"),
    );
  } catch (err) {
    toast(err.message, "error", 5000);
  } finally {
    btn.disabled = false;
  }
}


/* ============ 填入步骤 4 ============ */

function fillStep4() {
  if (!lastParsed) return;
  if (lastParsed.kv != null) {
    $("#kvCache").value = lastParsed.kv;
  }
  if (lastParsed.max_len != null) {
    $("#kvMaxLen").value = lastParsed.max_len;
    // 触发步骤4的输入长度chips刷新
    $("#kvMaxLen").dispatchEvent(new Event("input", { bubbles: true }));
  }
  toast("已填入步骤 4 的 KV cache 参数", "success");
  import("./main.js").then(m => m.showView("step4"));
}
