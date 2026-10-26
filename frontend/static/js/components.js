/** 可复用组件: 日志控制台 / 远程目录浏览器 / 柱状图 */

import { el, fmtBytes, modal } from "./util.js";
import { api } from "./api.js";


/**
 * 日志控制台 - 处理 \r 单行刷新(进度条)
 * 后端日志以增量文本块推送, 控制台维护逻辑行缓冲
 */
export class LogConsole {
  constructor(preNode) {
    this.pre = preNode;
    this.lines = [];
    this.maxLines = 3000;
  }

  append(text) {
    // 按 \n 分段; 段内 \r 表示回到行首(保留最后一段)
    const segments = text.split("\n");
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (i > 0) this.lines.push("");
      if (seg.includes("\r")) {
        const last = seg.split("\r").pop();
        if (this.lines.length === 0) this.lines.push("");
        this.lines[this.lines.length - 1] = last;
      } else if (seg) {
        if (this.lines.length === 0) this.lines.push("");
        this.lines[this.lines.length - 1] += seg;
      }
    }
    if (this.lines.length > this.maxLines) {
      this.lines = this.lines.slice(-this.maxLines);
    }
    this.render();
  }

  render() {
    const atBottom = this.pre.scrollTop + this.pre.clientHeight >= this.pre.scrollHeight - 40;
    this.pre.textContent = this.lines.join("\n");
    if (atBottom) this.pre.scrollTop = this.pre.scrollHeight;
  }

  clear() {
    this.lines = [];
    this.render();
  }
}


export function joinPath(base, name) {
  const b = base.replace(/\/+$/, "");
  return b ? `${b}/${name}` : `/${name}`;
}

export function parentPath(path) {
  const stripped = path.replace(/\/+$/, "");
  if (!stripped || stripped === "/") return "/";
  return stripped.split("/").slice(0, -1).join("/") || "/";
}


/**
 * 远程目录浏览器弹窗(基于 /api/fs/list)
 * onPick(path) 用户点击"选择当前目录"时回调
 */
export function browseRemoteDir({ title = "浏览远程目录 - 选择模型路径", onPick }) {
  let currentPath = "/";

  const m = modal({ title, large: true });
  const pathInput = el("input", { type: "text", value: currentPath });
  const listBox = el("div", { class: "browser-list" });
  const statusEl = el("div", { class: "inline-msg muted", style: "margin-top:8px" });

  function load(path) {
    currentPath = path;
    pathInput.value = path;
    listBox.innerHTML = `<div class="file-list-empty">加载中...</div>`;
    api.get(`/api/fs/list?path=${encodeURIComponent(path)}`)
      .then(data => renderList(data.entries))
      .catch(err => {
        listBox.innerHTML = "";
        listBox.append(el("div", { class: "file-list-empty" }, `加载失败: ${err.message}`));
      });
  }

  function renderList(entries) {
    listBox.innerHTML = "";
    statusEl.textContent = `${entries.length} 项`;
    if (currentPath !== "/") {
      listBox.append(fileRow("📁 ..", () => load(parentPath(currentPath))));
    }
    for (const entry of entries) {
      if (entry.type === "dir") {
        listBox.append(fileRow(`📁 ${entry.name}`,
          () => load(joinPath(currentPath, entry.name))));
      } else {
        listBox.append(fileRow(`📄 ${entry.name}`, null, fmtBytes(entry.size)));
      }
    }
  }

  function fileRow(label, onDouble, sizeText) {
    return el("div", {
      class: "file-item",
      ondblclick: onDouble || null,
      title: onDouble ? "双击进入" : "",
    },
      el("span", {}, label),
      sizeText ? el("span", { class: "file-size" }, sizeText) : null);
  }

  m.body.append(
    el("div", { class: "browser-path-row" },
      pathInput,
      el("button", {
        class: "btn btn-primary btn-sm",
        onclick: () => load(pathInput.value.trim() || "/"),
      }, "前往"),
      el("button", {
        class: "btn btn-outline btn-sm",
        onclick: () => load(parentPath(currentPath)),
      }, "↑ 上级")),
    listBox, statusEl);

  m.footer.append(
    el("button", { class: "btn btn-outline", onclick: m.close }, "取消"),
    el("button", {
      class: "btn btn-primary",
      onclick: () => { const p = currentPath; m.close(); onPick(p); },
    }, "选择当前目录"));

  load("/");
  return m;
}


/**
 * 轻量分组柱状图(Canvas, 无外部依赖)
 * labels: [string]; series: [{name, color, values: [number|null]}]
 * options: {unit, decimals}
 */
export function drawBarChart(canvas, labels, series, options = {}) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const W = Math.max(rect.width, 260);
  const H = Math.max(rect.height, 220);
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const decimals = options.decimals ?? 2;
  const unit = options.unit || "";
  const padding = { top: 24, right: 16, bottom: 44, left: 64 };
  const plotW = W - padding.left - padding.right;
  const plotH = H - padding.top - padding.bottom;

  const allValues = series.flatMap(s => s.values)
    .filter(v => v !== null && Number.isFinite(v));
  let maxVal = allValues.length ? Math.max(...allValues) : 1;
  maxVal = niceCeiling(maxVal * 1.08);

  // 网格与Y轴
  ctx.font = "11px sans-serif";
  ctx.fillStyle = "#64748b";
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 1;
  const gridLines = 5;
  for (let i = 0; i <= gridLines; i++) {
    const y = padding.top + plotH - (plotH * i / gridLines);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(W - padding.right, y);
    ctx.stroke();
    const val = maxVal * i / gridLines;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(formatTick(val, decimals), padding.left - 8, y);
  }

  // 分组柱
  const n = labels.length;
  if (n === 0) return;
  const groupW = plotW / n;
  const barCount = series.length;
  const barW = Math.min(26, (groupW - 10) / barCount - 4);

  labels.forEach((label, i) => {
    const cx = padding.left + groupW * i + groupW / 2;
    ctx.fillStyle = "#64748b";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(label, cx, padding.top + plotH + 8);

    series.forEach((s, si) => {
      const v = s.values[i];
      if (v === null || !Number.isFinite(v)) return;
      const h = plotH * (v / maxVal);
      const x = cx - (barW * barCount + 4 * (barCount - 1)) / 2 + si * (barW + 4);
      const y = padding.top + plotH - h;
      ctx.fillStyle = s.color;
      roundedRect(ctx, x, y, barW, Math.max(h, 1), 3);
      ctx.fill();
      if (n <= 8 && h > 0) {
        ctx.fillStyle = "#334155";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(formatTick(v, decimals), x + barW / 2, y - 3);
      }
    });
  });

  if (unit) {
    ctx.fillStyle = "#94a3b8";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(unit, padding.left, 4);
  }
}

function roundedRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function formatTick(v, decimals) {
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000 && Number.isInteger(v)) return v.toLocaleString("zh-CN");
  return v.toFixed(decimals).replace(/\.?0+$/, "");
}

function niceCeiling(v) {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const frac = v / 10 ** exp;
  const nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  return nice * 10 ** exp;
}


/** 表格行构建辅助 */
export function tableRows(tbody, rows, { empty = "暂无数据" } = {}) {
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.append(el("tr", { class: "empty-row" }, el("td", { colspan: "99" }, empty)));
    return;
  }
  for (const cells of rows) {
    const tr = el("tr");
    for (const cell of cells) {
      if (cell instanceof Node) tr.append(el("td", {}, cell));
      else tr.append(el("td", { html: String(cell) }));
    }
    tbody.append(tr);
  }
}
