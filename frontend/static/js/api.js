/** API 客户端 */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, url, body = null, options = {}) {
  const init = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(url, init);
  let data = null;
  try { data = await resp.json(); } catch { /* ignore */ }
  if (!resp.ok) {
    const message = data?.detail || `请求失败 (${resp.status})`;
    if (!options.silent) console.warn(`${method} ${url}:`, message);
    throw new ApiError(message, resp.status);
  }
  return data;
}

export const api = {
  get: (url, options) => request("GET", url, null, options),
  post: (url, body, options) => request("POST", url, body, options),
  put: (url, body, options) => request("PUT", url, body, options),
  del: (url, options) => request("DELETE", url, null, options),

  /** 带进度的文件上传 */
  upload(url, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.upload.addEventListener("progress", e => {
        if (e.lengthComputable && onProgress) {
          onProgress(e.loaded, e.total);
        }
      });
      xhr.addEventListener("load", () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          reject(new ApiError(data?.detail || `上传失败 (${xhr.status})`, xhr.status));
        }
      });
      xhr.addEventListener("error", () => reject(new ApiError("网络错误", 0)));
      xhr.open("POST", url);
      xhr.send(form);
    });
  },

  /** 触发浏览器下载 */
  download(url) {
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.append(a);
    a.click();
    a.remove();
  },

  /** POST 并下载文件响应 */
  async postDownload(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      let data = null;
      try { data = await resp.json(); } catch { /* ignore */ }
      throw new ApiError(data?.detail || `导出失败 (${resp.status})`, resp.status);
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = match ? match[1] : "download";
    document.body.append(a);
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    a.remove();
  },
};

/** WebSocket 日志客户端(自动重连) */
export class LogSocket {
  constructor(onEvent, onStatus) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.ws = null;
    this.closed = false;
    this.retryDelay = 1000;
    this.channels = "deploy,exec";
    this.connect();
  }

  /**切换订阅通道(如按当前容器切换 exec@<容器名>), 自动重连 */
  setChannels(channels) {
    if (this.channels === channels) return;
    this.channels = channels;
    if (this.ws) {
      this.closed = true;   // 防止 onclose 触发旧连接重连
      this.ws.close();
      this.closed = false;
      this.connect();
    }
  }

  connect() {
    if (this.closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(
      `${proto}://${location.host}/ws/logs?channels=${encodeURIComponent(this.channels)}`);
    this.ws.onopen = () => {
      this.retryDelay = 1000;
      if (this.onStatus) this.onStatus(true);
    };
    this.ws.onmessage = e => {
      try {
        const data = JSON.parse(e.data);
        if (this.onEvent) this.onEvent(data);
      } catch { /* ignore */ }
    };
    this.ws.onclose = () => {
      if (this.onStatus) this.onStatus(false);
      if (!this.closed) {
        setTimeout(() => this.connect(), this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 1.5, 10000);
      }
    };
    this.ws.onerror = () => this.ws.close();
  }

  close() {
    this.closed = true;
    if (this.ws) this.ws.close();
  }
}
