/** 可复用组件: 日志控制台 / 远程目录浏览器 / 柱状图 */

import { el, fmtBytes, modal } from "./util.js";
import { api } from "./api.js";


/**
 * 日志控制台 - 处理 \r 单行刷新(进度条)
 * 后端日志以增量文本块推送, 控制台维护逻辑行缓冲。
 *
 * v2.0 修复: 远程命令经 PTY 执行时, 终端驱动会把 \n 转为 \r\n(ONLCR)。
 * 旧实现把每段结尾的 \r 当作"回到行首"的进度条刷新, 取 \r 后内容(为空)
 * 覆盖整行 —— 导致执行日志只滚不动、看不到任何脚本输出。
 * 现在先将 \r\n 归一化为 \n(含跨 chunk 边界的 carry 缓冲), 仅把
 * 单独出现的 \r 视为进度条单行刷新。
 */
export class LogConsole {
  constructor(preNode) {
    this.pre = preNode;
    this.lines = [];
    this.maxLines = 3000;
    this.pending = "";   // 跨 chunk 的 \r\n 边界缓冲
  }

  append(text) {
    this.pending += text;
    let s = this.pending;
    // 结尾的孤立 \r 可能是下一个 chunk 开头 \n 的一半, 先暂存
    if (s.endsWith("\r")) {
      this.pending = "\r";
      s = s.slice(0, -1);
    } else {
      this.pending = "";
    }
    // PTY(ONLCR) 输出的 \r\n 统一还原为 \n
    s = s.replace(/\r\n/g, "\n");

    // 按 \n 分段; 段内单独的 \r 表示回到行首(保留最后一段, 进度条场景)
    const segments = s.split("\n");
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (i > 0) this.lines.push("");
      if (!seg) continue;
      if (this.lines.length === 0) this.lines.push("");
      if (seg.includes("\r")) {
        const last = seg.split("\r").pop();
        this.lines[this.lines.length - 1] = last;
      } else if (seg) {
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
    this.pending = "";
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
 * 浏览容器内目录(步骤 3 选择 MODEL_PATH 用)
 * container: 容器名; initialPath: 起始目录(建议传容器模型挂载路径)
 * onPick(path): 选中目录回调 —— 路径即容器内路径, 直接作为 MODEL_PATH
 */
export function browseContainerDir({ container, initialPath = "/", onPick }) {
  let currentPath = initialPath || "/";

  const m = modal({ title: "浏览容器内目录 - 选择 MODEL_PATH", large: true });
  const pathInput = el("input", { type: "text", value: currentPath });
  const listBox = el("div", { class: "browser-list" });
  const statusEl = el("div", { class: "inline-msg muted", style: "margin-top:8px" });

  function load(path) {
    currentPath = path;
    pathInput.value = path;
    listBox.innerHTML = `<div class="file-list-empty">加载中...</div>`;
    api.get(`/api/containers/fs?name=${encodeURIComponent(container)}` +
      `&path=${encodeURIComponent(path)}`)
      .then(data => renderList(data.entries || []))
      .catch(err => {
        listBox.innerHTML = "";
        listBox.append(el("div", { class: "file-list-empty" },
          `加载失败: ${err.message}`));
      });
  }

  function renderList(entries) {
    listBox.innerHTML = "";
    statusEl.textContent = entries.length
      ? `${entries.length} 个子目录` : "（无子目录）";
    if (currentPath !== "/") {
      listBox.append(dirRow("📁 ..", () => load(parentPath(currentPath))));
    }
    for (const entry of entries) {
      listBox.append(dirRow(`📁 ${entry.name}`, () => load(entry.path)));
    }
  }

  function dirRow(label, onClick) {
    return el("div", {
      class: "file-item",
      onclick: onClick,
      title: "点击进入",
      style: "cursor:pointer",
    }, el("span", {}, label));
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

  load(currentPath);
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
