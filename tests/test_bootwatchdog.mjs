// 启动自检(watchdog)测试 —— 误报修复 + 失败原因自报的回归验证
import fs from "node:fs";
import path from "node:path";

let failed = 0;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failed++;
}

const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf8");
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
  .filter(m => !/\bsrc=/.test(m[1]) && !/\btype=/.test(m[1]));
check("index.html 含内联自检脚本", scripts.length === 1);
const src = scripts[0][2];

const mainSrc = fs.readFileSync(
  path.resolve("frontend/static/js/main.js"), "utf8");
check("main.js 顶层设置 __appBooted",
  /window\.__appBooted\s*=\s*true/.test(mainSrc));
const initIdx = mainSrc.lastIndexOf("async function init()");
const initSrc = mainSrc.slice(initIdx);
check("v3.0 init: 登录态检查前先注册登录门禁", 
  initSrc.indexOf("initLogin()") < initSrc.indexOf("auth.getToken()"));
check("v3.0 init: 未登录走 showLogin", 
  initSrc.includes("showLogin()"));

// 在 DOM 桩中执行自检脚本
function makeEnv({ booted = false, active = false } = {}) {
  const els = {
    bootError: { hidden: true },
    bootErrorReason: { innerHTML: "" },
    bootErrorDiag: { textContent: "" },
    bootErrorForceBtn: {
      listeners: [], addEventListener(ev, fn) { this.listeners.push(fn); },
    },
  };
  const window = {
    __appBooted: booted,
    listeners: [],
    addEventListener: (ev, fn) => window.listeners.push({ ev, fn }),
  };
  const document = {
    getElementById: id => els[id] || null,
    querySelector: () => (active ? {} : null),
  };
  const timers = [];
  const setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  const setInterval = (fn, ms) => { timers.push({ fn, ms, repeat: true }); return timers.length; };
  window.fetch = () => Promise.resolve({
    ok: true, json: () => Promise.resolve({ version: "9.9.9" }),
  });
  const navigator = { userAgent: "test-agent" };
  const location = { pathname: "/", search: "", origin: "http://x" };
  const run = new Function("window", "document", "setTimeout", "setInterval",
    "navigator", "location", "fetch", src);
  // 包装为动态查找: 场景 7 替换 window.fetch 后闭包内立即生效
  run(window, document, setTimeout, setInterval, navigator, location,
    (...args) => window.fetch(...args));
  return { els, window, timers, document };
}

function fire(env, ev, arg) {
  env.window.listeners.filter(l => l.ev === ev).forEach(l => l.fn(arg));
}
function tick(env) {
  const t = env.timers.shift();
  if (t) t.fn();
}
function tickAll(env) {
  for (let i = 0; i < 60 && env.timers.length; i++) tick(env);
}

// 1) 正常启动: __appBooted 已设置 → 隐藏
{
  const env = makeEnv({ booted: true });
  tickAll(env);
  check("已启动: 不显示错误", env.els.bootError.hidden === true);
}

// 2) 首屏已渲染 → 隐藏
{
  const env = makeEnv({ active: true });
  tickAll(env);
  check("首屏已渲染: 不显示错误", env.els.bootError.hidden === true);
}

// 3) 模块失败(无标记无视图) → 显示 + 原因自报
{
  const env = makeEnv();
  tickAll(env);
  check("模块失败: 显示错误", env.els.bootError.hidden === false);
  check("诊断区含后端健康信息", /后端健康/.test(env.els.bootErrorDiag.textContent));
  check("诊断区含等待时间", /已等待/.test(env.els.bootErrorDiag.textContent));
}

// 4) 慢启动恢复(旧版 20s 停止轮询导致永久残留的缺陷):
//    多轮"未启动"后模块终于启动 → 提示必须自动消失
{
  const env = makeEnv();
  for (let i = 0; i < 10; i++) tick(env);   // 50 秒都未启动
  check("慢启动: 期间显示提示", env.els.bootError.hidden === false);
  env.window.__appBooted = true;            // 之后模块加载成功
  tick(env);
  check("慢启动恢复: 提示自动消失(不再 20s 停止轮询)",
    env.els.bootError.hidden === true);
}

// 5) 脚本 404 → 立即显示且原因包含具体 URL
{
  const env = makeEnv();
  fire(env, "error", {
    target: { tagName: "SCRIPT", src: "http://x/static/js/step5.js?v=9", status: 404 },
  });
  tickAll(env);
  check("脚本404: 立即显示", env.els.bootError.hidden === false);
  check("脚本404: 原因含具体路径",
    /\/static\/js\/step5\.js\?v=9/.test(env.els.bootErrorDiag.textContent));
}

// 6) 模块求值异常 → 原因含异常消息与文件行号
{
  const env = makeEnv();
  fire(env, "error", {
    message: "x is not defined",
    filename: "http://x/static/js/step2.js?v=9",
    lineno: 12,
  });
  tickAll(env);
  check("求值异常: 原因含消息与位置",
    /x is not defined @ \/static\/js\/step2\.js\?v=9:12/.test(
      env.els.bootErrorDiag.textContent));
}

// 7) 后端不可达 → 诊断区标明
{
  const env = makeEnv();
  // 先让初始健康探测的 microtask 完成(避免与故障探测竞态互覆盖)
  await new Promise(r => setTimeout(r, 20));
  env.window.fetch = () => Promise.reject(new Error("HTTP 503"));
  const interval = env.timers.find(t => t.repeat);
  if (interval) interval.fn();
  await new Promise(r => setTimeout(r, 20));
  tickAll(env);
  check("后端不可达: 诊断区标明", /不可达/.test(env.els.bootErrorDiag.textContent));
}

if (failed) {
  console.error(`\n${failed} checks failed`);
  process.exit(1);
}
console.log("\nAll boot watchdog checks passed");
