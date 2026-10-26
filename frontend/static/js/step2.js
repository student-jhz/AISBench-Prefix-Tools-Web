/** 步骤 2: 选择文件与模型 */

import { $, el, fmtBytes, setMsg, toast } from "./util.js";
import { api } from "./api.js";
import { G, saveForm } from "./main.js";
import { browseRemoteDir } from "./components.js";


const FIELDS = ["modelPath", "modelName", "vllmIp", "vllmPort", "apiKey", "workDir"];


export function initStep2() {
  initImageUpload();
  initCodeUpload();
  initModelForm();
}


/* ============ 镜像上传 ============ */

function initImageUpload() {
  const dropArea = $("#imageDropArea");
  const fileInput = $("#imageFileInput");

  $$radio("imageSource", value => {
    $("#imageUploadBox").hidden = value !== "upload";
    $("#imageRemoteBox").hidden = value !== "remote";
  });

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
          : el("span", { class: "meta" }, "未从 manifest 识别到镜像名，部署时由 docker load 识别"));
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

  // 远程 tar 检查
  $("#imageRemoteCheckBtn").addEventListener("click", async () => {
    const path = $("#imageRemotePath").value.trim();
    const msg = $("#imageRemoteMsg");
    if (!path) {
      setMsg(msg, "请输入远程 tar 包路径", "error");
      return;
    }
    setMsg(msg, "检查中...", "warn");
    try {
      const data = await api.post("/api/deploy/check-remote-tar", { remote_path: path });
      if (data.exists) {
        G.form.imageRemote = { path, image_name: data.image_name };
        saveForm();
        setMsg(msg, data.image_name
          ? `✓ 文件存在，识别镜像: ${data.image_name}`
          : "✓ 文件存在（无法预读镜像名，部署时识别）", "ok");
      } else {
        G.form.imageRemote = null;
        setMsg(msg, "✗ 文件不存在", "error");
      }
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
  });
}


/* ============ 代码上传 ============ */

function initCodeUpload() {
  const dropArea = $("#codeDropArea");
  const fileInput = $("#codeFileInput");

  $$radio("codeSource", value => {
    $("#codeBuiltinBox").hidden = value !== "builtin";
    $("#codeZipBox").hidden = value !== "zip";
  });

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
}


/* ============ 模型与服务信息 ============ */

function initModelForm() {
  // 字段持久化
  for (const field of FIELDS) {
    const input = $(`#${field}`);
    if (!input) continue;
    input.addEventListener("change", () => {
      G.form[field] = input.value;
      saveForm();
    });
  }

  // 远程目录浏览
  $("#modelBrowseBtn").addEventListener("click", () => {
    browseRemoteDir({
      title: "浏览远程目录 - 选择模型路径",
      onPick: path => {
        $("#modelPath").value = path;
        G.form.modelPath = path;
        saveForm();
      },
    });
  });

  // 暴露恢复函数(main.js 初始化后调用)
  window.restoreStep2Form = () => {
    for (const field of FIELDS) {
      const input = $(`#${field}`);
      if (input && G.form[field] !== undefined && G.form[field] !== "") {
        input.value = G.form[field];
      }
    }
    // vLLM IP 默认为执行机 IP
    if (!G.form.vllmIp && G.state?.ssh?.connected) {
      $("#vllmIp").value = G.state.ssh.host;
      G.form.vllmIp = G.state.ssh.host;
    }
    // 显示已上传的镜像信息
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
    if (G.form.imageRemote) {
      $("#imageRemotePath").value = G.form.imageRemote.path;
    }
  };
}


/** 当前步骤2的选择结果(供步骤3使用) */
export function getImageSelection() {
  const mode = getRadio("imageSource");
  if (mode === "remote") {
    return { type: "remote", remote_path: $("#imageRemotePath").value.trim() };
  }
  const upload = G.form.imageUpload;
  return { type: "upload", upload_id: upload?.id || "" };
}

export function getCodeSelection() {
  const source = getRadio("codeSource");
  if (source === "zip") {
    return { source: "zip", zip_upload_id: G.form.codeUpload?.id || "" };
  }
  return { source: "builtin" };
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
