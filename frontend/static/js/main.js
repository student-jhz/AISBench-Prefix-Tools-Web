/** 应用启动入口: 只负责装配各页面模块, 共享状态在 state.js
 *
 * 请勿让其他模块 import "./main.js" —— 那会形成循环依赖, 且在
 * index.html 使用 ?v= 缓存键时造成 main.js 双实例(见 state.js 顶部说明)。
 */

import { $$ } from "./util.js";
import { api, auth } from "./api.js";
import { enterApp, initLogConsoles, showLogin, showView } from "./state.js";
import { initLogin } from "./login.js";
import { initTasks } from "./tasks.js";
import { initNewTask } from "./newtask.js";
import { initCompare } from "./compare.js";

// 兼容既有调用方/测试: 共享 API 由 state.js 单一实例提供
export {
  G, saveDesigner, showView, subscribeTaskLog,
  enterApp, showLogin, refreshSession,
} from "./state.js";

// 模块图启动标记: index.html 内联 watchdog 据此判断前端是否真正启动
window.__appBooted = true;


async function init() {
  initLogConsoles();
  initLogin();
  initTasks();
  initNewTask();
  initCompare();

  // 侧边栏导航
  $$(".nav-item").forEach(n => {
    n.addEventListener("click", () => showView(n.dataset.view));
  });

  // 登录态检查: 有 token 验证, 无 token 直接登录界面
  if (auth.getToken()) {
    const s = await api.get("/api/auth/status").catch(() => null);
    if (s?.authenticated) {
      await enterApp();
      return;
    }
    auth.setToken("");
  }
  showLogin();
}

init();
