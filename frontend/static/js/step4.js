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

// 步骤2 表单字段 -> 步骤4 配置字段(进入步骤4时自动继承)
const STEP2_MAP = {
  model_name: "modelName",
  model_path: "modelPath",
  host_ip: "vllmIp",
  host_port: "vllmPort",
  api_key: "apiKey",
};

// 用户在步骤4手动编辑过的字段, 不再被步骤2的值覆盖
const dirtyFields = new Set();


export function initStep4() {
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

  // 供 main.js 在切换到步骤4视图时调用, 保证步骤2后填的值能继承过来
  window.syncStep4FromStep2 = syncFromStep2;
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
    // 从步骤 2 表单同步(未手动编辑过的字段)
    syncFromStep2();
    updatePreview();
  } catch (err) {
    setMsg("#configStatusMsg", `加载默认配置失败: ${err.message}`, "error");
  }
}


/** 从步骤2表单继承模型/服务信息(仅覆盖未手动编辑过的字段) */
export function syncFromStep2() {
  const f = G.form;
  let changed = false;
  for (const [field, source] of Object.entries(STEP2_MAP)) {
    const value = f[source];
    if (!value) continue;                   // 步骤2未填写则保留现有值
    if (dirtyFields.has(field)) continue;   // 尊重用户在步骤4的手动修改
    const input = $(FIELD_IDS[field]);
    const text = String(value);
    if (input.value !== text) {
      input.value = text;
      changed = true;
    }
  }
  if (changed) updatePreview();
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
