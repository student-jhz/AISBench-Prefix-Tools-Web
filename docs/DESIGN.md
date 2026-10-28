# AISBench-Prefix-Tools-Web 架构设计

> 版本：v2.0 · 多用户容器资源池重构 · 前身为桌面版 [AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools) 的 Web 迁移

## 1. 背景与目标

桌面版工具以 Windows EXE 形式分发，在测试人员本机运行，通过 SSH 远程操作执行机完成 AISBench prefix cache 性能测试环境的部署、配置、执行与结果提取。

网页版目标：

1. **去 EXE 化**：工具本身以 Docker 容器服务形式部署在执行机（或任一台内网服务器）上，浏览器直接访问，无需在测试人员电脑安装任何软件。
2. **功能完整对齐桌面版**：完整保留向导流程（SSH 连接 → 容器准备 → config.py 配置 → 测试用例设计 → 执行测试与结果提取）。
3. **多用户容器资源池**（v2.0 核心理念）：执行机上的 `aisbench_*` 容器是共享测试资源池，多位用户通过浏览器各选各的容器、互不干扰；创建容器不是必须的，复用已有容器即可直接进入配置/测试流程。
4. **保留独立工具**：远程日志工具（KV cache 筛选）、结果对比分析。
5. **保留环境清理**：按容器名勾选删除（绝不模糊/批量删除）+ 远程工作目录清理。

### v2.0 交互理念要点

| 理念 | 设计 |
|------|------|
| 容器即工作区 | 用户可自定义容器名，但强制 `aisbench_` 前缀（`aisbench_[A-Za-z0-9][A-Za-z0-9_.-]*`），既便于识别归属，又构成清理白名单 |
| 镜像优先复用 | 新建容器前先列出执行机已有镜像供选择；只有不存在时才上传 tar / 指定远程 tar（上传不再是必选项） |
| 挂载与配置解耦 | 步骤 2 只负责"容器能访问什么"（镜像 + 模型父目录挂载 + 容器名）；模型名称 / IP / Port 等服务参数延后到步骤 3 配置 config.py 时填写 |
| Busy 感知 | 容器列表实时检测是否正在执行测试（`docker top` 匹配 `run_tests.sh|aisbench_test.py` + 本服务执行分槽）；busy 容器给出醒目警示并建议新建容器，选择/执行前二次确认 |
| 安全清理 | 清理必须逐个勾选容器名；仅 `aisbench` 前缀（含旧版 `aisbench-`）可操作；正在执行测试的容器自动跳过，避免误伤他人任务 |

> v2.0.1 修复：docker `--format` 中 `\t` 在 Go 模板动作外**不解释转义**、按字面输出，
> 曾导致 inspect 解析失败（state=unknown、Binds 为空 → 界面显示"已停止"、config.py
> SFTP 写入挂载目录失败）。现改为解析 `docker inspect` 标准 JSON 与 `--format '{{json .}}'`
> 逐行输出，彻底规避分隔符转义问题。

> v2.0.2 修复"界面加载失败"误报：旧版首屏渲染前 `await /api/state`，而该端点同步执行
> `docker ps`/`docker top`（SSH 慢或长任务阻塞时数秒~分钟），4 秒启动自检只查一次且
> 永不撤销 → 永久误报、刷新也无效。现：
> - **首屏不依赖后端**：`showView()` 先渲染，状态异步补齐；`main.js` 模块顶层设置
>   `window.__appBooted` 启动标记；
> - **自检只在前端真失败时提示**：依据启动标记（而非 `.view.active`）判定，多轮
   复查、启动成功自动隐藏、脚本 404 立即提示；
> - **`/api/state` 零 SSH 往返**：运行状态取容器注册表、busy 取执行分槽，高频轮询
>   始终毫秒级；跨用户实时检测仍由 `/api/containers/busy` 提供；
> - **前端请求 30s 超时**（AbortController），SSH 阻塞不再让请求永远 pending；
>   docker 状态类命令加 10–15s 短超时（原默认 300s）。

> v2.0.4 修复带伤发布事故：v2.0.1~v2.0.3 的 `step2.js` 字符串字面量内被写入**真实换行符**
> （0D 0A，源自用 Python 脚本改写代码时 `\n` 被解释为换行），浏览器抛 `SyntaxError`
> 整站不可用；而 `node --check xx.js`（脚本目标）对这类错误不敏感导致连续漏检。
> 修复后新增双闸门：①pytest 参数化用例将每个前端 JS 复制为 `.mjs` 以**模块目标**解析
> （与浏览器同款语义）；②CI 在镜像构建前静态解析全部模块、冒烟测试中对**镜像实际提供的**
> 每个 JS 做同样解析。v2.0.0~v2.0.3 的 Release 已全部撤回。

## 2. 总体架构

```
┌────────────┐   HTTP/WS    ┌─────────────────────────────┐    SSH/SFTP    ┌──────────────┐
│  浏览器     │ ───────────▶ │  AISBench-Prefix-Tools-Web  │ ─────────────▶ │   执行机      │
│ (测试人员)  │ ◀─────────── │  (Docker 容器, 端口 8320)    │ ◀───────────── │  (Linux+Docker)│
└────────────┘   实时日志     │  FastAPI + 静态前端 + 持久卷  │   命令/文件     └──────────────┘
                             └─────────────────────────────┘
```

- Web 服务容器与被测执行机之间仍通过 **SSH** 交互，保持桌面版全部能力（远程执行机可以是本机宿主，也可以是任意其他机器）。
- 执行机上只需运行 Web 服务容器，测试人员浏览器访问 `http://<执行机IP>:8320`。

## 3. 技术选型

| 层 | 选型 | 说明 |
|----|------|------|
| 后端 | Python 3.11 + FastAPI + Uvicorn | 与桌面版同为 Python，核心业务逻辑可近乎原样复用 |
| SSH | Paramiko | 与桌面版一致 |
| 前端 | 原生 HTML/CSS/JS（ES Modules），无构建步骤 | 内网离线可用，不依赖 CDN；柱状图为自研 Canvas 组件 |
| 实时日志 | WebSocket + 服务端环形缓冲 | 部署/执行日志实时推送，断线重连自动补发历史 |
| 部署 | Dockerfile + docker-compose | 一键 `docker compose up -d --build` |

### 发布产物（离线部署）

执行机通常位于内网、无法访问外网，因此每次打 `v*` 标签时由
GitHub Actions（`.github/workflows/release.yml`）自动构建并附加到 Release：

| 产物 | 构建方式 | 适用场景 |
|------|----------|----------|
| `aisbench-web-offline_<版本>_linux_<架构>.zip` | `docker build` + `docker save`，含镜像 tar + 可选 compose + deploy.sh | 执行机部署（推荐），`bash deploy.sh` 自动 load 并 `docker run`（不依赖 Compose 插件） |
| `aisbench-prefix-tools-web_<版本>_linux_<架构>.docker.tar.gz` | 同上（裸镜像 tar） | 自行 `docker load` |
| `aisbench-web_<版本>_linux_<架构>.tar.gz` | manylinux2014（glibc 2.17）容器内 PyInstaller onefile（`deploy/aisbench-web.spec`，Python 为 python-build-standalone 共享库构建），内置后端+前端+core_code | 服务宿主机无 Docker 的 Linux 机器（glibc ≥ 2.17），SSH 控制执行机；更老系统请用 Docker 镜像包 |

架构覆盖 `linux/amd64` 与 `linux/arm64`。PyInstaller 打包形态下
`app/paths.py` 将资源根切换到 `sys._MEIPASS`（按源码布局打入
`frontend/` 与 `backend/core_code`），数据目录默认改为**运行目录**下的
`data/`，避免写入每次启动重建的临时解包目录。

## 4. 代码结构

```
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py          # FastAPI 应用、静态资源、WebSocket
│   │   ├── schemas.py       # Pydantic 请求/响应模型
│   │   ├── state.py         # 全局会话状态（SSH/Docker/部署信息/任务）
│   │   ├── logbus.py        # 日志总线（通道缓冲 + 订阅）
│   │   ├── tasks.py         # 后台任务（部署/执行/清理）+ 取消
│   │   ├── routers/         # API 路由（按模块拆分）
│   │   │   ├── ssh.py  files.py  deploy.py  config.py
│   │   │   ├── containers.py  designer.py  execute.py  results.py
│   │   │   ├── logtools.py  compare.py
│   │   │   └── system.py    # 健康检查 / 聚合状态 / 环境清理
│   │   └── core/            # 从桌面版迁移的核心业务模块（无 GUI 依赖）
│   │       ├── ssh_manager.py      ← 桌面版 ssh_manager.py
│   │       ├── docker_manager.py   ← 桌面版 docker_manager.py
│   │       ├── config_manager.py   ← 桌面版 config_manager.py（解耦 DockerManager）
│   │       ├── test_designer.py    ← 桌面版 test_designer.py
│   │       ├── log_parser.py       ← 桌面版 log_parser.py
│   │       ├── kv_log_tools.py     ← 桌面版 log_tools.py 的非 GUI 逻辑
│   │       └── compare_core.py     ← 桌面版 result_compare.py 的非 GUI 逻辑
│   └── core_code/
│       └── aisbench_auto_tools_prefix/   # 内置测试代码（随镜像分发）
├── frontend/
│   ├── index.html
│   └── static/{css,js}/     # 五步向导 + 日志工具 + 结果对比 SPA
├── tests/                   # pytest：核心模块单测 + API 集成测试（FakeSSH）
├── docs/DESIGN.md
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 5. 会话与状态模型

Web 服务维护一个全局 SSH 连接（步骤 1 建立，全流程共用），在此之上叠加**容器资源池**模型支撑多用户：

- `ssh`：当前 SSH 连接（所有用户共用，指向同一执行机）。
- `containers`：容器注册表 `name → inspect 信息`（选择/创建时登记，含镜像、挂载、运行状态等）。
- `deploy_info` / `docker`：服务端"当前容器"（最近一次选择/创建的容器），供未显式携带容器名的兼容请求使用。
- `tasks`：部署 / 清理任务全局各一个；**execute 任务按容器名分槽**（`_exec_tasks: 容器 → TaskRun`），不同容器可并发执行测试，同一容器互斥。
- `exec_registry`（v2.0.5）：容器执行状态注册表 `name → {status, phase, run_id, script_running, started_at, ended_at}`，内存 + `data/exec_state.json` 持久化；任务启动写入 `running`，完成/失败/取消立即迁移出 `running`，服务重启时残留 `running` 标记为 `interrupted`。
- `logtools_other_ssh`：日志工具"其他服务器"临时连接。

多用户请求隔离：前端在 config / execute / status 等请求中**显式携带各自选择的容器名**，服务端经 `resolve_container(name)` / `docker_for_container(name)` 解析出该容器的挂载与路径信息；浏览器端当前容器选择存于各自 localStorage。

步骤解锁规则（服务端校验 + 前端同步置灰）：

| 步骤 | 前置条件 |
|------|----------|
| 2-5 | SSH 已连接 |
| 3 配置保存/验证、5 执行 | 已选择容器且容器运行 |

## 6. API 设计（摘要）

| 模块 | 端点 | 说明 |
|------|------|------|
| 系统 | `GET /api/health`、`GET /api/state` | 健康检查 / 会话与步骤解锁状态 |
| SSH | `POST /api/ssh/connect`、`POST /api/ssh/disconnect`、`GET /api/ssh/status` | 密码或密钥（密钥先上传） |
| 远程浏览 | `GET /api/fs/list?path=` | SFTP 列目录（模型路径选择器） |
| 上传 | `POST /api/upload/{image|code-zip|ssh-key|compare-csv}` | 保存到 `data/uploads/`，tar 上传时预读 manifest 得到镜像名 |
| 容器池 | `GET /api/containers/list`、`GET /api/containers/images`、`POST /api/containers/{select|start}`、`GET /api/containers/busy?name=` | 列出容器（含 busy）/镜像、选择已有容器（busy 拦截）、启动已停止容器、实时 busy 查询 |
| 容器内浏览 | `GET /api/containers/fs?name=&path=` | `docker exec ls` 列出容器内子目录（步骤 3 MODEL_PATH 浏览选择） |
| 部署 | `GET /api/deploy/preflight`、`POST /api/deploy/start|cancel`、`GET /api/deploy/status` | 后台线程执行 5 阶段容器创建（镜像三模式：existing/upload/remote） |
| 配置 | `POST /api/config/{preview|save|verify|read}`、`GET /api/config/defaults` | 生成/写入/回读校验 config.py（请求携带 container） |
| 用例 | `GET /api/designer/presets`、`POST /api/designer/{generate|cases|script}` | 无状态计算：KV 参数 → 用例/命令/脚本 |
| 执行 | `POST /api/execute/start|cancel`、`GET /api/execute/status?container=` | 预检查 → 脚本执行 → 日志下载 → CSV 解析（按容器分槽） |
| 结果 | `GET /api/results`、`GET /api/results/{run}`、`GET .../download` | 结果历史、单文件/打包下载、删除 |
| 清理 | `POST /api/cleanup` | **按显式勾选的容器名删除** + 清理远程工作目录 |
| 日志工具 | `POST /api/logtools/other/{connect|disconnect}`、`GET /api/logtools/{containers|list}`、`POST /api/logtools/{grep|view}` | 宿主机/容器/docker logs 三种来源，KV cache 解析一键填入 |
| 对比 | `POST /api/compare/{load|compute|export}` | 两份 CSV（上传或历史结果）匹配对比、导出 |
| 实时日志 | `WS /ws/logs` | 按通道（deploy/exec）推送增量日志，连接时补发历史 |

## 7. 关键流程

### 7.1 容器准备（选择已有 / 按需创建）

**路径 A · 选择已有容器（推荐，无需创建）**：

1. `GET /api/containers/list` 列出执行机上 `aisbench_*` 容器（含运行状态与 busy 检测）；
2. 用户点选 → `POST /api/containers/select`：busy 且未 `force` 时返回拦截信息（建议新建容器），确认后带 `force` 重选；已停止容器允许选择并提示可启动；
3. 后端 `docker inspect` 解析 Binds（代码挂载 / 模型挂载 / 模型路径提示）登记入容器注册表，前端进入步骤 3。

**路径 B · 创建新容器**（5 阶段后台任务）：

1. 检查远程 Docker 环境；
2. **镜像准备（三模式）**：`existing` 从执行机已有镜像选择（默认优先）；`upload` 上传 tar（manifest 预读镜像名，已存在则跳过加载）；`remote` 使用执行机上已有 tar 路径；
3. 上传测试代码：内置目录（容器内自带）或 zip（上传 + 远程解压）；
4. 校验容器名：必须 `aisbench_` 前缀且格式合法，**同名冲突报错而非覆盖**；
5. 创建并启动容器（`--shm-size=1g --net=host`，挂载**模型父目录**与代码），成功后 inspect + 登记，解锁后续步骤。

### 7.2 执行测试（对应桌面版 `_run_test_sequence`）

1. 前端显式携带 `container` 发起执行，后端校验：容器 busy（执行状态注册表 `running` 或 `docker top` 检测到测试进程）→ 拒绝；
2. 容器内预检查 MODEL_PATH（存在且含 config.json）；
3. 由前端提交的用例参数生成 run_tests.sh（随机种子、逐用例日志、失败计数）；
4. `docker exec` 执行脚本（执行状态注册表标记 `script_running=true`，脚本退出后置回 `false`），日志按 `exec@<容器名>` 通道实时推送（不同容器的执行日志互不混杂）；
5. SFTP 下载 `test_logs_*/` 下全部日志与 `aisbench_all.log`、`aisbench_result.csv` 到服务端 `data/results/<run_id>/`；
6. 解析日志生成 `results_*.csv` 与结果摘要表，前端展示并支持下载。

**容器执行状态检测（v2.0.5，busy 判定原理）**：

- **执行状态注册表（权威）**：任务启动即在服务端后台数据写入（内存 + `data/exec_state.json`），记录 `status/phase/run_id/script_running` 等字段；任务线程无论正常完成、失败还是取消，`finally` 中都把状态迁移出 `running`。前端 `/api/containers/busy` 的 `executing_here` 即来源于此，因此**测试任务结束后 busy 必然自动消除**（前端 busy 期间每 5 秒轮询）。
- **docker top 进程探测（辅助）**：`docker top <容器> -o pid,cmd` 按 `run_tests.sh / aisbench_test.py` 特征匹配进程，可发现**其他用户或手工 docker exec 启动**的测试（注册表覆盖不到的场景）；已过滤 defunct 僵尸进程，避免测试结束后残留僵尸导致"永远 busy"。
- 两者合并为 `busy` 并附带 `busy_source`（registry/process/both）标明来源；`script_running=false` 且任务仍在收尾（下载/解析日志）时，界面按"收尾中"展示而非"测试执行中"。

### 7.3 实时日志

- 后台线程向 `LogBus` 追加文本（channel=deploy/exec），每通道保留最近 10000 条；
- WebSocket 连接（`?channels=deploy,exec@<容器名>`）后按 seq 补发历史，随后 100ms 轮询增量推送；切换容器时动态重订阅；
- **PTY CRLF 归一化**（v2.0 修复）：远端命令经 PTY 执行时 `ONLCR` 会把 `\n` 输出为 `\r\n`，若前端把段尾 `\r` 当作进度条刷新符，`split("\r").pop()` 得到空串导致"日志区在滚动但看不到内容"。修复方案为后端 `execute_with_callback` 跨 chunk 缓冲归一化 `\r\n` → `\n`，前端 LogConsole 仅将**孤立的 `\r`**（其后无 `\n`）视为单行刷新（进度条），`\r\n` 正常换行。

### 7.4 取消

- 任务阶段间检查取消标志；
- SSHManager 跟踪当前活动 channel，取消时主动关闭以尽快中断远程命令。

### 7.5 环境清理（v2.0 按名清理）

- 请求必须显式携带 `containers: [名称列表]`，逐个校验：
  - 非 `aisbench` 前缀（兼容旧 `aisbench-`）→ **直接拒绝**，绝不操作其他命名格式的容器；
  - 容器内正有测试进程（`docker top`）或本服务正在该容器执行 → **自动跳过**，避免误伤他人任务；
- 删除后同步清理容器注册表；若服务端"当前容器"被删则重置部署信息；
- 可选清理远程工作目录（复用上次部署的 work_dir）。

## 8. 数据存储

| 目录 | 内容 | 生命周期 |
|------|------|----------|
| `data/uploads/` | 镜像 tar、代码 zip、SSH 密钥、对比 CSV | 手动删除（API 提供） |
| `data/results/<run_id>/` | 每次执行下载的日志 + 生成的 CSV | 手动删除（API 提供） |

`data/` 以卷挂载持久化，容器重建不丢失。

## 9. 部署方案

```bash
git clone https://github.com/student-jhz/AISBench-Prefix-Tools-Web.git
cd AISBench-Prefix-Tools-Web
docker compose up -d --build
# 浏览器访问 http://<执行机IP>:8320
```

- 端口：8320（可改 compose 映射）。
- compose 配置 `extra_hosts: host.docker.internal:host-gateway`，连接本机宿主时可用该地址。

## 10. 测试策略

| 层 | 内容 |
|----|------|
| 核心单测 | TestDesigner 计算公式/预设过滤/调整系数；ConfigManager 生成/校验/解析；LogParser 样例日志解析与 CSV 生成；对比核心匹配/差值计算；KV 日志解析与命令构造 |
| API 集成 | FakeSSH 注入：上传、设计器、配置预览、对比全流程、状态与步骤解锁；v2.0 新增容器池（列表/选择/busy 拦截/启动）、镜像三模式创建、按容器执行、按名清理（含非 aisbench 拒绝与 busy 跳过） |
| 前端 | `node tests/test_logconsole.mjs`：LogConsole 对 PTY `\r\n`、跨 chunk 分片、孤立 `\r` 进度条、普通 `\n` 等场景的渲染正确性 |
| 前端 | `node tests/test_bootwatchdog.mjs`：启动自检对已启动/首屏渲染/模块失败/慢启动恢复/脚本 404/非脚本资源错误等场景的判定正确性 |
| 冒烟 | 应用启动、静态页面、WebSocket 握手 |

## 11. 里程碑

1. 设计文档（本文件）
2. 核心业务模块迁移 + 单测
3. FastAPI 后端（会话/任务/日志总线/全部路由）+ API 测试
4. 前端 SPA（向导 + 日志工具 + 结果对比）
5. 容器化（Dockerfile / compose / .dockerignore）
6. v1.0.0 发布（README、tag、GitHub Release）
7. **v2.0 多用户容器资源池重构**：五步向导（容器准备合并原"文件与模型 + Docker 部署"）、镜像优先复用、busy 感知、按容器名执行与清理、PTY CRLF 日志修复
