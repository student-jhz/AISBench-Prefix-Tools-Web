/** 步骤 1: SSH 连接执行机 */

import { $, setMsg, toast } from "./util.js";
import { api } from "./api.js";
import { G, refreshState } from "./main.js";


export function initStep1() {
  const hostEl = $("#sshHost");
  const portEl = $("#sshPort");
  const userEl = $("#sshUser");
  const pwEl = $("#sshPassword");
  const connectBtn = $("#sshConnectBtn");
  const disconnectBtn = $("#sshDisconnectBtn");
  const msgEl = $("#sshConnMsg");

  let keyUploadId = null;

  // 密码显示切换
  $("#sshPwToggle").addEventListener("click", () => {
    pwEl.type = pwEl.type === "password" ? "text" : "password";
  });

  // 回填上次连接信息(凭据保存在服务端, 服务重启后自动重连)
  restoreSshForm();

  async function restoreSshForm() {
    try {
      const st = await api.get("/api/ssh/status", { silent: true });
      const r = st.remembered;
      if (r?.host) {
        hostEl.value = r.host;
        if (r.port) portEl.value = r.port;
        if (r.username) userEl.value = r.username;
        if (r.has_credential && !st.connected) {
          setMsg(msgEl, `已保存 ${r.host} 的连接信息, 服务重启后会自动重连`, "muted");
        }
      }
    } catch { /* ignore */ }
  }

  // 密钥选择与上传
  $("#sshKeyPick").addEventListener("click", () => $("#sshKeyFile").click());
  $("#sshKeyFile").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    $("#sshKeyName").value = `${file.name} (上传中...)`;
    try {
      const data = await api.upload("/api/upload/ssh-key", file);
      keyUploadId = data.id;
      $("#sshKeyName").value = file.name;
      setMsg(msgEl, `密钥已上传: ${file.name}`, "ok");
      toast("密钥文件已上传", "success");
    } catch (err) {
      keyUploadId = null;
      $("#sshKeyName").value = "";
      e.target.value = "";
      setMsg(msgEl, `密钥上传失败: ${err.message}`, "error");
    }
  });

  // 连接
  connectBtn.addEventListener("click", async () => {
    const host = hostEl.value.trim();
    const username = userEl.value.trim();
    const password = pwEl.value || null;
    const port = parseInt(portEl.value, 10) || 22;

    if (!host || !username) {
      setMsg(msgEl, "请填写 IP 和用户名", "error");
      return;
    }
    if (!password && !keyUploadId) {
      setMsg(msgEl, "请填写密码或上传密钥文件", "error");
      return;
    }

    connectBtn.disabled = true;
    connectBtn.textContent = "连接中...";
    setMsg(msgEl, "正在连接...", "warn");

    try {
      const data = await api.post("/api/ssh/connect", {
        host, port, username, password,
        key_upload_id: keyUploadId,
      });
      setMsg(msgEl, data.message, "ok");
      toast("SSH 连接成功", "success");
      // 记录主机IP供步骤3配置默认HOST_IP使用
      G.form.host = host;
      await refreshState();
      // 自动进入下一步
      import("./main.js").then(m => m.showView("step2"));
    } catch (err) {
      setMsg(msgEl, err.message, "error");
      toast(`连接失败: ${err.message}`, "error", 5000);
    } finally {
      connectBtn.disabled = false;
      connectBtn.textContent = "测试连接";
    }
  });

  // 断开
  disconnectBtn.addEventListener("click", async () => {
    try {
      await api.post("/api/ssh/disconnect");
      toast("已断开连接", "info");
      await refreshState();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
