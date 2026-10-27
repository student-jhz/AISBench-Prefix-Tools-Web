// 启动自检(watchdog)组件测试 —— v2.0.2 误报修复的回归验证
//
// 旧版 bug: 在 4s 时检查一次 ".view.active" 且永不撤销; 而 main.js 的 init()
// 会先 await /api/state 再渲染首屏 —— 后端/SSH 慢时(如正在 docker load)
// 永久误报"界面加载失败", 刷新也无效。新版依据 window.__appBooted
// (模块图求值成功标记)判定, 且启动成功后自动隐藏提示。
import fs from "node:fs";
import path from "node:path";

let failed = 0;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failed++;
}

// ---- 从 index.html 提取内联(非 module)脚本 ----
const html = fs.readFileSync(path.resolve("frontend/index.html"), "utf8");
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)]
  .filter(m => !/\bsrc=/.test(m[1]) && !/\btype=/.test(m[1]));
check("index.html 含内联自检脚本", scripts.length === 1);
const watchdogSrc = scripts[0][2];

// main.js 必须在模块顶层设置启动标记(任何模块加载失败都到不了这里)
const mainSrc = fs.readFileSync(
  path.resolve("frontend/static/js/main.js"), "utf8");
check("main.js 顶层设置 __appBooted",
  /window\.__appBooted\s*=\s*true/.test(mainSrc));

// main.js 必须先渲染首屏再等待状态(不再被 /api/state 阻塞)
const initIdx = mainSrc.lastIndexOf("async function init()");
const initSrc = mainSrc.slice(initIdx);
const showIdx = initSrc.indexOf('showView("step1")');
const waitIdx = initSrc.indexOf("await refreshState()");
check("init 先渲染后等状态", showIdx >= 0 && waitIdx >= 0 && showIdx < waitIdx);

// ---- 在 DOM 桩中执行自检脚本 ----
function makeEnv({ booted, hasActiveView }) {
  const errorEl = { hidden: true };
  const listeners = [];
  const timeouts = [];
  const window = {
    __appBooted: booted,
    addEventListener: (ev, fn) => listeners.push({ ev, fn }),
  };
  const document = {
    getElementById: id => (id === "bootError" ? errorEl : null),
    querySelector: sel => (sel === ".view.active" && hasActiveView ? {} : null),
  };
  const runScript = new Function("window", "document", "setTimeout",
    `${watchdogSrc}`);
  runScript(window, document, (fn, ms) => timeouts.push({ fn, ms }));
  return { errorEl, listeners, timeouts, window };
}

// 场景1: 模块已启动(__appBooted) → 永不显示错误; 若曾显示则自动隐藏
{
  const { errorEl, timeouts } = makeEnv({ booted: true, hasActiveView: false });
  timeouts.forEach(t => t.fn());
  check("已启动: 不显示错误", errorEl.hidden === true);
}

// 场景2: 首屏已渲染(main.js 先渲染后等状态) → 同样判定成功
{
  const { errorEl, timeouts } = makeEnv({ booted: false, hasActiveView: true });
  timeouts.forEach(t => t.fn());
  check("首屏已渲染: 不显示错误", errorEl.hidden === true);
}

// 场景3: 模块图真失败(无标记无视图) → 显示错误
{
  const { errorEl, timeouts } = makeEnv({ booted: false, hasActiveView: false });
  timeouts.forEach(t => t.fn());
  check("模块失败: 显示错误提示", errorEl.hidden === false);
}

// 场景4: 慢启动恢复 —— 前几轮未启动(误显示), 随后 __appBooted 出现 → 自动隐藏
{
  const env = makeEnv({ booted: false, hasActiveView: false });
  env.timeouts[0].fn();               // 3s: 尚未启动 → 显示
  check("慢启动: 先误显示", env.errorEl.hidden === false);
  env.window.__appBooted = true;      // 5s 时模块加载完成
  env.timeouts.slice(1).forEach(t => t.fn());
  check("慢启动: 后自动隐藏(修复刷新也没用的误报)", env.errorEl.hidden === true);
}

// 场景5: 脚本资源加载失败(404) → 立即触发自检并显示
{
  const env = makeEnv({ booted: false, hasActiveView: false });
  const res = env.listeners.find(l => l.ev === "error");
  check("注册了资源加载失败监听", !!res);
  if (res) {
    res.fn({ target: { tagName: "SCRIPT" } });
    check("脚本404: 立即显示错误", env.errorEl.hidden === false);
  }
}

// 场景6: 非脚本资源(如图片)报错 → 不误触发
{
  const env = makeEnv({ booted: true, hasActiveView: true });
  const res = env.listeners.find(l => l.ev === "error");
  if (res) {
    res.fn({ target: { tagName: "IMG" } });
    check("图片错误: 不影响", env.errorEl.hidden === true);
  }
}

if (failed) {
  console.error(`\n${failed} checks failed`);
  process.exit(1);
}
console.log("\nAll boot watchdog checks passed");
