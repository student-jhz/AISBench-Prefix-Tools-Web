/** 步骤 3: Docker 部署 */

import { $, el, setMsg, toast, confirmDialog, fmtTime } from "./util.js";
import { api } from "./api.js";
import { G, saveForm, refreshState } from "./main.js";
import { getImageSelection, getCodeSelection } from "./step2.js";


let lastDeployRunning = false;


export function initStep3() {
  const workDirEl = $("#workDir");

  // 工作目录持久化
  workDirEl.addEventListener("input", () => {
    G.form.workDir = workDirEl.value;
    saveForm();
  });

  $("#workDirCheckBtn").addEventListener("click", checkWorkDir);
  $("#workDirCleanBtn").addEventListener("click", cleanWorkDir);
  $("#deployStartBtn").addEventListener("click", startDeploy);
  $("#deployCancelBtn").addEventListener("click", cancelDeploy);

  window.updateDeployInfo = renderDeployInfo;
}


/* ============ 工作目录检查 / 清理 ============ */

async function checkWorkDir() {
  const workDir = $("#workDir").value.trim() || "/tmp/AISBench_Prefix_Tools";
  setMsg("#workDirMsg", "检查中...", "warn");
  try {
    const data = await api.get(
      `/api/deploy/preflight?work_dir=${encodeURIComponent(workDir)}`);
    if (!data.exists) {
      setMsg("#workDirMsg", "目录不存在(部署时将自动创建)", "muted");
    } else if (!data.has_content) {
      setMsg("#workDirMsg", "目录存在且为空", "ok");
    } else {
      setMsg("#workDirMsg",
        `目录已存在, 含 ${data.entries.length} 项内容(部署前建议先清理)`, "warn");
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
    `将删除远程执行机上的工作目录:\n${workDir}\n\n其中的镜像tar包/代码等文件将被清除, 是否继续?`,
    { title: "清理工作目录", danger: true, okText: "清理" });
  if (!ok) return;
  try {
    await api.post("/api/cleanup",
      { remove_container: false, clean_work_dir: true, work_dir: workDir });
    toast("清理任务已启动, 详情见部署日志", "info");
  } catch (err) {
    setMsg("#workDirMsg", err.message, "error");
  }
}


/* ============ 部署启动 / 取消 ============ */

async function startDeploy() {
  const image = getImageSelection();
  const code = getCodeSelection();
  const modelPath = $("#modelPath").value.trim();
  const workDir = $("#workDir").value.trim() || "/tmp/AISBench_Prefix_Tools";

  // 前置校验
  if (image.type === "upload" && !image.upload_id) {
    toast("请先在步骤 2 上传镜像 tar 包", "warning");
    return;
  }
  if (image.type === "remote" && !image.remote_path) {
    toast("请先在步骤 2 填写远程镜像 tar 路径", "warning");
    return;
  }
  if (code.source === "zip" && !code.zip_upload_id) {
    toast("请先在步骤 2 上传代码 zip 包", "warning");
    return;
  }
  if (!modelPath) {
    toast("请先在步骤 2 填写模型权重路径", "warning");
    return;
  }

  G.form.workDir = workDir;
  saveForm();

  const btn = $("#deployStartBtn");
  btn.disabled = true;
  btn.textContent = "部署中...";
  $("#deployCancelBtn").disabled = false;
  $("#deployPhase").textContent = "⏳ 任务启动中...";
  lastDeployRunning = true;

  try {
    await api.post("/api/deploy/start", {
      image,
      model_path: modelPath,
      work_dir: workDir,
      code,
      clean_work_dir: false,
    });
    toast("部署任务已启动", "success");
    await refreshState({ silent: true });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "🚀 开始部署";
    $("#deployCancelBtn").disabled = true;
    $("#deployPhase").textContent = "";
    lastDeployRunning = false;
    toast(err.message, "error", 5000);
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


/* ============ 部署状态轮询(main.js 高频调用) ============ */

export async function pollDeployStatus() {
  let st;
  try {
    st = await api.get("/api/deploy/status", { silent: true });
  } catch {
    return;
  }
  $("#deployPhase").textContent = st.running
    ? `⏳ [${st.status}] ${st.phase || "部署中"}...`
    : "";

  if (lastDeployRunning && !st.running) {
    if (st.status === "done") {
      toast("Docker 部署完成!", "success");
      renderDeployInfo(st.result);
    } else if (st.status === "error") {
      toast(`部署失败: ${st.error}`, "error", 8000);
    } else if (st.status === "cancelled") {
      toast("部署已取消", "info");
    }
    await refreshState({ silent: true });
  }
  lastDeployRunning = st.running;
}


/* ============ 部署信息卡片 ============ */

const INFO_FIELDS = [
  ["容器名称", "container_name"],
  ["镜像", "image_name"],
  ["代码路径(宿主机)", "code_host_path"],
  ["代码挂载(容器内)", "code_mount_path"],
  ["模型路径", "model_path"],
  ["工作目录", "work_dir"],
  ["部署时间", "deployed_at"],
];


function renderDeployInfo(deploy) {
  const card = $("#deployInfoCard");
  const grid = $("#deployInfoGrid");
  if (!deploy || !deploy.container_name) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  grid.innerHTML = "";
  for (const [label, key] of INFO_FIELDS) {
    let value = deploy[key] || "-";
    if (key === "deployed_at") value = fmtTime(value);
    grid.append(el("div", { class: "info-item" },
      el("div", { class: "info-label" }, label),
      el("div", { class: "info-value" }, value)));
  }
  // 已部署时同步工作目录显示
  const workDirEl = $("#workDir");
  if (deploy.work_dir && (!workDirEl.value.trim() ||
      workDirEl.value.trim() === "/tmp/AISBench_Prefix_Tools")) {
    workDirEl.value = deploy.work_dir;
  }
}


/* ============ 任务状态按钮刷新(由 step6 汇总挂载) ============ */

export function updateDeployTaskUI(tasks) {
  const t = tasks?.deploy;
  const running = !!t?.running;
  const startBtn = $("#deployStartBtn");
  startBtn.disabled = running;
  startBtn.textContent = running ? "部署中..." : "🚀 开始部署";
  $("#deployCancelBtn").disabled = !running;
  $("#workDirCheckBtn").disabled = running;
  $("#workDirCleanBtn").disabled = running;
  if (!running) $("#deployPhase").textContent = "";
}
