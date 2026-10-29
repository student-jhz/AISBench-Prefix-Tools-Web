/** 登录界面 —— 登录执行机后才进入任务平台 */

import { $, bindPasswordToggle, setMsg, toast } from "./util.js";
import { api, auth } from "./api.js";
import { enterApp } from "./state.js";


export function initLogin() {
  const hostEl = $("#loginHost");
  const portEl = $("#loginPort");
  const userEl = $("#loginUser");
  const pwEl = $("#loginPassword");
  const btn = $("#loginBtn");
  const msgEl = $("#loginMsg");

  // 密码显示/隐藏(图标与状态同步反馈)
  bindPasswordToggle(pwEl, $("#loginPwToggle"));

  let keyUploadId = null;

  // 回填上次登录的用户名(凭据不回填)
  try {
    const last = localStorage.getItem("aisbench_login_hint");
    if (last) {
      const hint = JSON.parse(last);
      if (hint.host) hostEl.value = hint.host;
      if (hint.port) portEl.value = hint.port;
      if (hint.username) userEl.value = hint.username;
    }
  } catch { /* ignore */ }

  // 密钥选择与上传
  $("#loginKeyPick").addEventListener("click", () => $("#loginKeyFile").click());
  $("#loginKeyFile").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    $("#loginKeyName").value = `${file.name} (上传中...)`;
    try {
      const data = await api.upload("/api/upload/ssh-key", file);
      keyUploadId = data.id;
      $("#loginKeyName").value = file.name;
      setMsg(msgEl, `密钥已上传: ${file.name}`, "ok");
    } catch (err) {
      keyUploadId = null;
      $("#loginKeyName").value = "";
      e.target.value = "";
      setMsg(msgEl, `密钥上传失败: ${err.message}`, "error");
    }
  });

  // 登录
  btn.addEventListener("click", async () => {
    const host = hostEl.value.trim();
    const username = userEl.value.trim();
    const password = pwEl.value || null;
    const port = parseInt(portEl.value, 10) || 22;

    if (!host || !username) {
      setMsg(msgEl, "请填写执行机 IP 和用户名", "error");
      return;
    }
    if (!password && !keyUploadId) {
      setMsg(msgEl, "请填写密码或上传密钥文件", "error");
      return;
    }

    btn.disabled = true;
    btn.textContent = "登录中...";
    setMsg(msgEl, "正在登录执行机...", "warn");
    try {
      const data = await api.post("/api/auth/login", {
        host, port, username, password, key_upload_id: keyUploadId,
      });
      auth.setToken(data.token);
      try {
        localStorage.setItem("aisbench_login_hint", JSON.stringify(
          { host, port, username }));
      } catch { /* ignore */ }
      setMsg(msgEl, data.message, "ok");
      toast(data.message, "success");
      pwEl.value = "";
      await enterApp();
    } catch (err) {
      setMsg(msgEl, `登录失败: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "🔐 登录执行机";
    }
  });

  // 回车登录
  [hostEl, portEl, userEl, pwEl].forEach(el => {
    el.addEventListener("keydown", e => {
      if (e.key === "Enter") btn.click();
    });
  });

  // 退出登录
  $("#logoutBtn").addEventListener("click", async () => {
    try {
      await api.post("/api/auth/logout");
    } catch { /* 会话可能已失效 */ }
    auth.setToken("");
    location.reload();
  });
}
