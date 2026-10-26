# AISBench-Prefix-Tools-Web 架构设计

> 版本：v1.0 设计稿 · 对应桌面版 [AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools) 全功能迁移

## 1. 背景与目标

桌面版工具以 Windows EXE 形式分发，在测试人员本机运行，通过 SSH 远程操作执行机完成 AISBench prefix cache 性能测试环境的部署、配置、执行与结果提取。

网页版目标：

1. **去 EXE 化**：工具本身以 Docker 容器服务形式部署在执行机（或任一台内网服务器）上，浏览器直接访问，无需在测试人员电脑安装任何软件。
2. **功能完整对齐桌面版**：完整保留 6 步向导（SSH 连接 → 文件与模型 → Docker 部署 → config.py 配置 → 测试用例设计 → 执行测试与结果提取）。
3. **保留独立工具**：远程日志工具（KV cache 筛选）、结果对比分析。
4. **保留环境清理**：容器删除 + 远程工作目录清理。

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
| `aisbench-web_<版本>_linux_<架构>.tar.gz` | manylinux2014（glibc 2.17）容器内 PyInstaller onefile（`deploy/aisbench-web.spec`），内置后端+前端+core_code | 服务宿主机无 Docker 的 Linux 机器（glibc ≥ 2.17），SSH 控制执行机 |

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
│   │   │   ├── designer.py  execute.py  results.py
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
│   └── static/{css,js}/     # 六步向导 + 日志工具 + 结果对比 SPA
├── tests/                   # pytest：核心模块单测 + API 集成测试（FakeSSH）
├── docs/DESIGN.md
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 5. 会话与状态模型

与桌面版"单实例单用户"模型一致，Web 版维护**一个全局会话**：

- `ssh`：当前 SSH 连接（步骤 1 建立，全流程共用）。
- `docker`：部署成功后的 DockerManager（container_name / image_name / code_host_path / model_path / work_dir）。
- `tasks`：部署 / 执行 / 清理三类后台任务，同一时刻每类仅一个。
- `logtools_other_ssh`：日志工具"其他服务器"临时连接。

步骤解锁规则（服务端校验 + 前端同步置灰）：

| 步骤 | 前置条件 |
|------|----------|
| 2-6 | SSH 已连接 |
| 4 配置保存/验证、6 执行 | 部署完成且容器运行 |

## 6. API 设计（摘要）

| 模块 | 端点 | 说明 |
|------|------|------|
| 系统 | `GET /api/health`、`GET /api/state` | 健康检查 / 会话与步骤解锁状态 |
| SSH | `POST /api/ssh/connect`、`POST /api/ssh/disconnect`、`GET /api/ssh/status` | 密码或密钥（密钥先上传） |
| 远程浏览 | `GET /api/fs/list?path=` | SFTP 列目录（模型路径选择器） |
| 上传 | `POST /api/upload/{image|code-zip|ssh-key|compare-csv}` | 保存到 `data/uploads/`，tar 上传时预读 manifest 得到镜像名 |
| 部署 | `GET /api/deploy/preflight`、`POST /api/deploy/start|cancel`、`GET /api/deploy/status` | 后台线程执行 4 阶段部署 |
| 配置 | `POST /api/config/{preview|save|verify|read}`、`GET /api/config/defaults` | 生成/写入/回读校验 config.py |
| 用例 | `GET /api/designer/presets`、`POST /api/designer/{generate|cases|script}` | 无状态计算：KV 参数 → 用例/命令/脚本 |
| 执行 | `POST /api/execute/start|cancel`、`GET /api/execute/status` | 预检查 → 脚本执行 → 日志下载 → CSV 解析 |
| 结果 | `GET /api/results`、`GET /api/results/{run}`、`GET .../download` | 结果历史、单文件/打包下载、删除 |
| 清理 | `POST /api/cleanup` | 删除容器 + 清理远程工作目录 |
| 日志工具 | `POST /api/logtools/other/{connect|disconnect}`、`GET /api/logtools/{containers|list}`、`POST /api/logtools/{grep|view}` | 宿主机/容器/docker logs 三种来源，KV cache 解析一键填入 |
| 对比 | `POST /api/compare/{load|compute|export}` | 两份 CSV（上传或历史结果）匹配对比、导出 |
| 实时日志 | `WS /ws/logs` | 按通道（deploy/exec）推送增量日志，连接时补发历史 |

## 7. 关键流程

### 7.1 部署（对应桌面版 `_deploy_sequence`）

1. 检查远程 Docker 环境；
2. 读取镜像名：本地 tar manifest（或远程 tar `tar -xOf manifest.json`），已存在则跳过上传加载（**镜像去重**）；
3. 不存在则 SFTP 上传 tar（实时进度）→ `docker load`（从输出解析镜像名）；
4. 上传测试代码：内置目录（容器内自带）或 zip（上传 + 远程解压）；
5. 创建并启动容器 `aisbench-test-MMDDHHMM`（`--shm-size=1g --net=host`，挂载模型与代码）；
6. 成功后记录部署信息，解锁步骤 4/6。

### 7.2 执行测试（对应桌面版 `_run_test_sequence`）

1. 容器内预检查 MODEL_PATH（存在且含 config.json）；
2. 由前端提交的用例参数生成 run_tests.sh（随机种子、逐用例日志、失败计数）；
3. `docker exec` 执行脚本，日志实时推送；
4. SFTP 下载 `test_logs_*/` 下全部日志与 `aisbench_all.log`、`aisbench_result.csv` 到服务端 `data/results/<run_id>/`；
5. 解析日志生成 `results_*.csv` 与结果摘要表，前端展示并支持下载。

### 7.3 实时日志

- 后台线程向 `LogBus` 追加文本（channel=deploy/exec），每通道保留最近 10000 条；
- WebSocket 连接后按 seq 补发历史，随后 100ms 轮询增量推送；
- 前端按步骤过滤通道渲染，支持 `\r` 单行刷新（进度条）。

### 7.4 取消

- 任务阶段间检查取消标志；
- SSHManager 跟踪当前活动 channel，取消时主动关闭以尽快中断远程命令。

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
| API 集成 | FakeSSH 注入：上传、设计器、配置预览、对比全流程、状态与步骤解锁 |
| 冒烟 | 应用启动、静态页面、WebSocket 握手 |

## 11. 里程碑

1. 设计文档（本文件）
2. 核心业务模块迁移 + 单测
3. FastAPI 后端（会话/任务/日志总线/全部路由）+ API 测试
4. 前端 SPA（六步向导 + 日志工具 + 结果对比）
5. 容器化（Dockerfile / compose / .dockerignore）
6. v1.0.0 发布（README、tag、GitHub Release）
