/** 通用工具函数 */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (value !== null && value !== undefined) {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function fmtLen(n) {
  n = Number(n);
  if (!Number.isFinite(n)) return "-";
  n = Math.round(n);
  if (n >= 1048576 && n % 1048576 === 0) return `${n / 1048576}M`;
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}K`;
  return String(n);
}

export function fmtNum(v, decimals = 2) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("zh-CN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtBytes(size) {
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)}GB`;
  if (size >= 1024 ** 2) return `${(size / 1024 ** 2).toFixed(1)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${size}B`;
}

export function fmtTime(iso) {
  if (!iso) return "-";
  return iso.replace("T", " ").slice(0, 19);
}

export function debounce(fn, ms = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** Toast 通知 */
export function toast(message, type = "info", duration = 3200) {
  const icons = { success: "✓", error: "✗", warning: "⚠", info: "ℹ" };
  const node = el("div", { class: `toast ${type}` },
    el("span", {}, icons[type] || ""), el("span", {}, message));
  $("#toastContainer").append(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity 0.3s";
    setTimeout(() => node.remove(), 300);
  }, duration);
}

/** 模态框: 返回 {root, close, body, footer} */
export function modal({ title, large = false, onClose } = {}) {
  const body = el("div", { class: "modal-body" });
  const footer = el("div", { class: "modal-footer" });
  const root = el("div", { class: "modal-overlay" },
    el("div", { class: `modal${large ? " modal-lg" : ""}` },
      el("div", { class: "modal-header" },
        el("div", { class: "modal-title" }, title || ""),
        el("button", { class: "modal-close", onclick: close }, "×")),
      body, footer));

  function close() {
    root.remove();
    if (onClose) onClose();
  }
  root.addEventListener("click", e => { if (e.target === root) close(); });
  $("#modalRoot").append(root);
  return { root, body, footer, close };
}

/** 确认对话框(替代 window.confirm) */
export function confirmDialog(message, { title = "确认", danger = false, okText = "确认" } = {}) {
  return new Promise(resolve => {
    const m = modal({
      title,
      onClose: () => resolve(false),
    });
    m.body.append(el("div", { style: "font-size:13.5px;line-height:1.7;white-space:pre-wrap" }, message));
    m.footer.append(
      el("button", { class: "btn btn-outline", onclick: () => { m.close(); resolve(false); } }, "取消"),
      el("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, onclick: () => resolve(true) }, okText));
  });
}

/** 复制文本到剪贴板 */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制到剪贴板", "success", 1500);
  } catch {
    const ta = el("textarea", { style: "position:fixed;opacity:0" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制到剪贴板", "success", 1500);
  }
}

/** 设置内联消息 */
export function setMsg(selector, text, type = "muted") {
  const node = typeof selector === "string" ? $(selector) : selector;
  if (!node) return;
  node.textContent = text || "";
  node.className = `inline-msg ${type}`;
}
