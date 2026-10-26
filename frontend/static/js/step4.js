/** 步骤 4: 配置 config.py */

import { $, setMsg, toast, debounce } from "./util.js";
import { api } from "./api.js";
import { G } from "./main.js";


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


export function initStep4() {
  loadDefaults();

  const preview = debounce(updatePreview, 400);
  for (const sel of Object.values(FIELD_IDS)) {
    $(sel).addEventListener("input", preview);
    $(sel).addEventListener("change", preview);
  }

  $("#configSaveBtn").addEventListener("click", saveConfig);
  $("#configVerifyBtn").addEventListener("click", verifyConfig);
  $("#configReadBtn").addEventListener("click", readConfig);
}


function collectConfig() {
  const config = {};
  for (const [field, sel] of Object.entries(FIELD_IDS)) {
    config[field] = $(sel).value.trim();
  }
  return config;
}


async function loadDefaults() {
  try {
    const data = await api.get("/api/config/defaults");
    const defaults = data.defaults || {};
    for (const [field, sel] of Object.entries(FIELD_IDS)) {
      $(sel).value = defaults[field] ?? "";
    }
    // 部署后的建议值: DATASET_PATH = 代码挂载路径
    if (data.dataset_path_default) {
      $("#cfgDatasetPath").value = data.dataset_path_default;
      $("#cfgDatasetPath").placeholder = data.dataset_path_default;
    }
    // 从步骤 2 表单同步
    const f = G.form;
    if (f.modelName) $("#cfgModelName").value = f.modelName;
    if (f.modelPath) $("#cfgModelPath").value = f.modelPath;
    if (f.vllmIp) $("#cfgHostIp").value = f.vllmIp;
    if (f.vllmPort) $("#cfgHostPort").value = f.vllmPort;
    if (f.apiKey) $("#cfgApiKey").value = f.apiKey;
    updatePreview();
  } catch (err) {
    setMsg("#configStatusMsg", `加载默认配置失败: ${err.message}`, "error");
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
    const data = await api.post("/api/config/save", { config: collectConfig() });
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
    const data = await api.post("/api/config/verify", { config: collectConfig() });
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
    const data = await api.get("/api/config/read");
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
