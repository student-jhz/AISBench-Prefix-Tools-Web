/** 步骤 3: 配置 config.py —— 模型服务信息与 MODEL_PATH 均在此配置 */

import { $, setMsg, toast, debounce } from "./util.js";
import { api } from "./api.js";
import { G } from "./main.js";
import { browseContainerDir } from "./components.js";


// 界面字段 -> config.py 键
const FIELD_IDS = {
  dataset_path: "#cfgDatasetPath",
  work_path: "#cfgWorkPath",
  model_name: "#cfgModelName",
  model_path: "#cfgModelPath",
  host_ip: "#cfgHostIp",
  host_port: "#cfgHostPort",
  api_key: "#cfgApiKey",
  default_perf: "#cfgDefaultPerf",
  output_dir: "#cfgOutputDir",
  pod_info: "#cfgPodInfo",
};

// 回读字段(config.py 键 -> 界面字段)
const READ_KEY_MAP = {
  DATASET_PATH: "dataset_path",
  WORK_PATH: "work_path",
  MODEL_NAME: "model_name",
  MODEL_PATH: "model_path",
  HOST_IP: "host_ip",
  HOST_PORT: "host_port",
  API_KEY: "api_key",
  DEFAULT_PERFORMANCE_TEST: "default_perf",
  OUTPUT_DIR: "output_dir",
  POD_INFO: "pod_info",
};

// 用户手动编辑过的字段, 不再被容器默认值覆盖
const dirtyFields = new Set();


export function initStep3() {
  loadDefaults();

  const preview = debounce(updatePreview, 400);
  for (const [field, sel] of Object.entries(FIELD_IDS)) {
    const input = $(sel);
    // 用户手动编辑 => 标记 dirty, 后续从步骤2同步时跳过该字段
    input.addEventListener("input", () => { dirtyFields.add(field); preview(); });
    input.addEventListener("change", () => { dirtyFields.add(field); preview(); });
  }

  $("#configSaveBtn").addEventListener("click", saveConfig);
  $("#configVerifyBtn").addEventListener("click", verifyConfig);
  $("#configReadBtn").addEventListener("click", readConfig);
  $("#cfgModelPathBrowseBtn").addEventListener("click", browseModelPath);

  // 供 main.js 在切换到步骤3视图时调用(选择容器后刷新默认值)
  window.syncConfigFromContainer = syncFromContainer;
}


function collectConfig() {
  const config = {};
  for (const [field, sel] of Object.entries(FIELD_IDS)) {
    config[field] = $(sel).value.trim();
  }
  return config;
}


/** 从容器内目录浏览选择 MODEL_PATH(路径即容器内路径, 直接生效) */
function browseModelPath() {
  if (!G.containerInfo?.name) {
    toast("请先在步骤 2 选择或创建容器", "warning");
    return;
  }
  const input = $("#cfgModelPath");
  const initial = input.value.trim()
    || G.containerInfo.model_path_hint || "/";
  browseContainerDir({
    container: G.containerInfo.name,
    initialPath: initial,
    onPick: path => {
      input.value = path;
      dirtyFields.add("model_path");   // 用户显式选择, 不再被自动填充覆盖
      updatePreview();
      toast(`已选择 MODEL_PATH: ${path}`, "success", 2500);
    },
  });
}


async function loadDefaults() {
  try {
    const data = await api.get("/api/config/defaults");
    const defaults = data.defaults || {};
    for (const [field, sel] of Object.entries(FIELD_IDS)) {
      $(sel).value = defaults[field] ?? "";
    }
    // 建议值: DATASET_PATH = 代码挂载路径
    if (data.dataset_path_default) {
      $("#cfgDatasetPath").value = data.dataset_path_default;
      $("#cfgDatasetPath").placeholder = data.dataset_path_default;
    }
    // 按当前选择的容器同步默认值(未手动编辑过的字段)
    syncFromContainer();
    updatePreview();
  } catch (err) {
    setMsg("#configStatusMsg", `加载默认配置失败: ${err.message}`, "error");
  }
}


/**
 * 从当前选择的容器继承默认值(仅覆盖未手动编辑过的字段):
 * - MODEL_PATH  ← 容器挂载的模型目录
 * - DATASET_PATH ← 容器内代码挂载路径
 * - HOST_IP     ← 执行机 IP(vLLM 与执行机同机时)
 */
export function syncFromContainer() {
  const info = G.containerInfo;
  let changed = false;

  const defaults = {};
  if (info) {
    if (info.model_path_hint) defaults.model_path = info.model_path_hint;
    if (info.code_mount_path) defaults.dataset_path = info.code_mount_path;
  }
  if (G.state?.ssh?.connected) defaults.host_ip = G.state.ssh.host;

  for (const [field, value] of Object.entries(defaults)) {
    const input = $(FIELD_IDS[field]);
    if (!input || input.value.trim() || dirtyFields.has(field)) continue;
    input.value = value;
    changed = true;
  }
  if (changed) {
    updatePreview();
    toast("已按当前容器填充默认配置(可手动修改)", "info", 2000);
  }
}


async function updatePreview() {
  try {
    const data = await api.post("/api/config/preview",
      { config: collectConfig() }, { silent: true });
    $("#configPreview").textContent = data.content || "(空)";
    if (data.errors?.length) {
      setMsg("#configStatusMsg", `配置有误: ${data.errors.join("; ")}`, "error");
    } else {
      setMsg("#configStatusMsg", "", "muted");
    }
  } catch (err) {
    setMsg("#configStatusMsg", err.message, "error");
  }
}


async function saveConfig() {
  const btn = $("#configSaveBtn");
  btn.disabled = true;
  setMsg("#configStatusMsg", "正在写入容器...", "warn");
  try {
    const data = await api.post("/api/config/save",
      { config: collectConfig(), container: G.container });
    if (data.warning) {
      setMsg("#configStatusMsg", data.warning, "warn");
    } else if (data.mismatches?.length) {
      setMsg("#configStatusMsg",
        `已写入但存在不一致: ${data.mismatches.join("; ")}`, "warn");
    } else {
      setMsg("#configStatusMsg", "✓ 配置已保存到容器, 回读校验一致", "ok");
    }
    if (data.host_path) {
      $("#configPathLabel").textContent =
        `宿主机路径: ${data.host_path}  |  容器内路径: ${data.container_path}`;
    }
    if (data.content) $("#configPreview").textContent = data.content;
    toast("config.py 已保存", "success");
  } catch (err) {
    setMsg("#configStatusMsg", err.message, "error");
    toast(err.message, "error", 5000);
  } finally {
    btn.disabled = false;
  }
}


async function verifyConfig() {
  setMsg("#configStatusMsg", "正在回读验证...", "warn");
  try {
    const data = await api.post("/api/config/verify",
      { config: collectConfig(), container: G.container });
    if (data.ok) {
      setMsg("#configStatusMsg", "✓ 容器内配置与界面值一致", "ok");
    } else {
      setMsg("#configStatusMsg", `配置不一致: ${data.mismatches.join("; ")}`, "warn");
    }
  } catch (err) {
    setMsg("#configStatusMsg", err.message, "error");
  }
}


async function readConfig() {
  setMsg("#configStatusMsg", "正在读取容器配置...", "warn");
  try {
    const data = await api.get(
      `/api/config/read?container=${encodeURIComponent(G.container || "")}`);
    const fields = data.fields || {};
    for (const [key, field] of Object.entries(READ_KEY_MAP)) {
      if (fields[key] !== undefined) $(FIELD_IDS[field]).value = fields[key];
    }
    $("#configPreview").textContent = data.content || "(空)";
    setMsg("#configStatusMsg", "✓ 已读取容器内当前 config.py 并填充表单", "ok");
  } catch (err) {
    setMsg("#configStatusMsg", err.message, "error");
  }
}
