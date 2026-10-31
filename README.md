# AISBench-Prefix-Tools-Web

**AISBench Prefix Cache 性能测试的多用户 Web 平台**。部署在单台执行机上，测试人员用浏览器登录执行机，即可发布 Prefix Cache 测试任务、跟踪执行进度、查看并对比结果数据。底层执行环境（Docker 容器）由平台自动准备与回收，用户全程无需接触容器等底层概念。

> 📐 架构设计文档：[docs/DESIGN.md](docs/DESIGN.md)
> 🧭 上游桌面版工具：[ucm-system/AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools)

## 交互理念

本工具定位为**单台执行机上的团队测试任务平台**：

1. **登录门禁**：未登录执行机时，页面只是一个登录界面；用密码或密钥登录后，才进入任务平台。
2. **任务即视角**：登录后看到的是执行机上的测试任务情况（排队 / 准备环境 / 执行测试 / 收集结果 / 完成 / 失败），而不是机器、容器、目录。
3. **配置而非运维**：用户只需配置镜像、测试配置（模型名称 / 权重目录 / 服务 IP / Port）与测试用例；执行环境由平台按任务自动准备，任务结束后容器可回收。
4. **结果留档**：每次测试的日志与结果 CSV 保存在服务端 `data/results/`，可随时在“结果对比分析”中跨任务比较，无需手动搬运文件。

## 功能清单

### 任务平台（浏览器界面）

- [x] **登录门禁**：密码 / 密钥（.pem、id_rsa、ed25519 等）登录执行机；密码框支持显示 / 隐藏切换；多用户各自持有会话，互不干扰
- [x] **任务列表**：登录后查看执行机上的全部测试任务与状态；活动任务每 2 秒自动轮询刷新
- [x] **新建任务**：
  - 选择镜像：直接列出执行机上已有镜像，按业务前缀分组聚焦
  - 测试配置：模型名称 / 权重目录（可浏览执行机目录选择）/ 服务 IP / Port / API Key / 默认性能模式 / 输出目录 / Pod 信息等
  - 测试用例设计：按 KV cache 信息计算并发数与请求数，输入 / 输出长度勾选（超出最大上下文自动禁用），用例增删改 / 复制 / 系数调整，低于最小请求数醒目警告
  - **引擎参数自动获取**：从 vLLM 启动日志筛选 `GPU KV cache size` 与 `Maximum concurrency`，一键填入单个 DP 组 KV cache 与最大请求长度；支持宿主机文件 / 容器内文件 / `docker logs` 三种来源；日志在**其他服务器**上时可临时连接该服务器查找；日志暂不可访问时可**复制等价 grep 命令**自行执行，并把输出粘贴回页面解析
  - 提交后由平台自动准备执行环境、生成 `run_tests.sh`、执行并解析结果
- [x] **任务详情**：实时执行日志（按任务通道 WebSocket 推送，历史快照一次整屏展示，进度条原地折叠）、当前阶段、结果表（TTFT / TPOT / E2E / 吞吐 / 命中率等）、CSV / ZIP 下载
- [x] **复制历史任务**：任务列表与详情页提供「复制」入口，一键回填历史任务的镜像、配置与用例设计，调整后提交新任务
- [x] **结果对比分析**：结果历史按 run 选择（自动匹配可对比的 `results_*.csv`）或上传 CSV；按输入 / 输出长度自动匹配用例；21 项指标分组勾选；差异表（A/B/Δ/Δ%，正绿负红）+ A/B 同图分组柱状图 + 导出对比 CSV
- [x] **会话持久化**：SSH 会话保存在服务端（服务重启自动重连执行机）；测试用例设计保存在浏览器本地，刷新不丢失

### 自动化 REST API（供脚本 / CI 集成）

浏览器界面之外，后端同时暴露完整的分步 API，可脚本化驱动整个测试流程：

| 模块 | 端点 | 说明 |
|------|------|------|
| 系统 | `GET /api/health`、`GET /api/state` | 健康检查 / 会话状态 |
| 认证 | `POST /api/auth/{login|logout}`、`GET /api/auth/status` | 任务平台登录门禁 |
| SSH | `POST /api/ssh/{connect|disconnect}`、`GET /api/ssh/status` | 密码或密钥连接执行机 |
| 远程浏览 | `GET /api/fs/list?path=` | SFTP 列目录 |
| 上传 | `POST /api/upload/{image|code-zip|ssh-key|compare-csv}` | 镜像 tar / 代码 zip / 密钥 / 对比 CSV |
| 容器池 | `GET /api/containers/{list|images|busy}`、`POST /api/containers/{select|start}` | 列出（含 busy 检测）/ 选择 / 启动容器 |
| 容器内浏览 | `GET /api/containers/fs?name=&path=` | `docker exec ls` 浏览容器内目录 |
| 部署 | `POST /api/deploy/{start|cancel}`、`GET /api/deploy/{status|preflight}` | 后台线程执行容器创建，WebSocket 实时日志 |
| 配置 | `POST /api/config/{preview|save|verify|read}`、`GET /api/config/defaults` | 生成 / 写入 / 回读校验 `config.py` |
| 用例 | `GET /api/designer/presets`、`POST /api/designer/{generate|cases|script}` | KV 参数 → 用例 / 命令 / 脚本 |
| 执行 | `POST /api/execute/{start|cancel}`、`GET /api/execute/status?container=` | 预检查 → 执行 → 日志下载 → CSV 解析 |
| 任务平台 | `GET /api/platform/tasks`、`GET /api/platform/tasks/{id}`、`POST /api/platform/tasks`、`POST /api/platform/tasks/{id}/cancel`、`GET /api/platform/tasks/{id}/copy-source`、`DELETE /api/platform/tasks/{id}` | 多用户任务发布 / 查询 / 取消 / 复制来源 / 删除 |
| 引擎日志 | `POST /api/platform/engine/grep`、`GET /api/platform/engine/{containers|list}`、`POST /api/platform/engine/other/{connect|disconnect}`、`GET /api/platform/engine/other/status` | 当前执行机或**其他服务器**上筛选 vLLM KV cache 信息 |
| 日志工具 | `POST /api/logtools/other/{connect|disconnect}`、`GET /api/logtools/{containers|list}`、`POST /api/logtools/{grep|view}` | 独立远程日志工具 |
| 结果 | `GET /api/results`、`GET /api/results/{run}`、`GET .../download` | 结果历史与下载 |
| 对比 | `POST /api/compare/{load|compute|export}` | 两份 CSV 匹配对比、导出 |
| 清理 | `POST /api/cleanup` | **按显式勾选的容器名删除** + 清理远程工作目录 |
| 实时日志 | `WS /ws/logs` | 按通道推送增量日志，连接时补发历史 |

### 容器执行状态检测原理

「容器是否正在执行测试」由**两级数据源**共同判定：

| 来源 | 实现 | 语义 | 特点 |
|---|---|---|---|
| **后台执行状态注册表**（权威） | 任务启动时在服务端写入 `data/exec_state.json`（内存 + 磁盘持久化），记录每个容器的 `status / phase / run_id / script_running / started_at / ended_at`；任务完成 / 失败 / 取消时立即迁移出 `running` | 本服务在该容器上启动的任务是否仍在执行 | 状态与任务生命周期严格同步；服务重启时残留 `running` 自动标记为 `interrupted`，不会永久卡死 |
| **docker top 进程探测**（辅助） | `docker top <容器> -o pid,cmd`，按 `run_tests.sh / aisbench_test.py` 进程特征匹配（过滤僵尸进程） | 容器内是否有测试进程——可发现其他用户或手工 `docker exec` 启动的测试 | 只作辅助信号：脚本退出后即使任务还在下载 / 解析日志，容器也按实际空闲展示 |

`busy = 注册表 running 或 检测到测试进程`，并通过 `busy_source`（registry / process / both）标明来源。busy 期间前端每 5 秒轮询，任务结束后提示自动消除。

### 安全边界

- 仅识别 `aisbench_` 前缀容器；其他容器（他人以其他命名规则创建的、或系统容器）一律拒绝操作
- 清理必须**逐个勾选容器名**，绝不模糊匹配或批量删除；正在执行测试的容器自动跳过

## 部署方式

执行机通常位于内网、无法访问外网，推荐使用 Release 中的**离线部署包**（CI 在每次发版时自动构建，无需 clone 代码 / 拉基础镜像 / 安装依赖）。

### 前置要求

- 执行机上已安装 Docker 引擎（无需 Docker Compose 插件，离线部署脚本使用 `docker run` 直接启动）
- 服务可 SSH 到目标测试机（也可以就是本机：服务容器通过 `host.docker.internal` 访问宿主机）

### 方式一：Docker 镜像离线包（推荐，执行机部署）

1. 在**有外网的电脑**上，从 [Releases](https://github.com/student-jhz/AISBench-Prefix-Tools-Web/releases) 下载 `aisbench-web-offline_<版本>_linux_<架构>.zip`
   - 架构选择：执行机上 `uname -m` 输出 `x86_64` 选 `amd64`，`aarch64` 选 `arm64`
2. 通过 SCP / U 盘等方式传输到执行机并解压
3. 执行部署脚本：

```bash
unzip aisbench-web-offline_<版本>_linux_<架构>.zip
cd aisbench-web-offline_<版本>_linux_<架构>
bash deploy.sh        # 自动 docker load 镜像并启动服务
```

浏览器访问：**http://\<执行机IP\>:8320**，全程无需访问外网。

升级新版本：下载新的离线包，重复上述步骤即可（同名容器自动重建）。

> 也可以只下载裸镜像 tar：`aisbench-prefix-tools-web_<版本>_linux_<架构>.docker.tar.gz`，
> 自行 `docker load -i <文件>` 后按需启动。

### 方式二：单文件可执行（服务宿主机无 Docker 时）

如果 Web 服务不部署在执行机上、而是部署在跳板机 / 办公电脑等**没有 Docker** 的机器（服务通过 SSH 控制执行机，执行机本身仍需 Docker）：

```bash
tar xzf aisbench-web_<版本>_linux_<架构>.tar.gz
chmod +x aisbench-web
./aisbench-web            # 默认 8320 端口, AISBENCH_PORT 可修改
```

可执行文件已内置后端 + 前端 + 测试代码，无需安装 Python；数据目录默认为运行目录下的 `data/`（可用 `AISBENCH_DATA_DIR` 指定）。
可执行文件在 manylinux2014（glibc 2.17）环境构建，兼容 CentOS 7+ / RHEL 7+ / 麒麟 V10 / openEuler / Ubuntu 18.04+ 等主流发行版；更老系统请使用 Docker 镜像离线包。

### 方式三：源码构建（可访问外网时）

```bash
git clone https://github.com/student-jhz/AISBench-Prefix-Tools-Web.git
cd AISBench-Prefix-Tools-Web
docker compose up -d --build
```

> 详见离线部署说明：[deploy/OFFLINE-README.md](deploy/OFFLINE-README.md)

### 数据持久化

`docker-compose.yml` 已将宿主机 `./data` 挂载到容器 `/app/data`：

```
data/
├── uploads/    # 上传的镜像 tar、代码 zip、SSH 密钥、对比 CSV
├── platform/   # 任务元数据
└── results/    # 每次测试的结果 run（日志 + CSV），可在页面下载/删除
```

### 连接宿主机上的服务

容器内已配置 `host.docker.internal` 指向宿主机（Linux 下由 compose 的 `host-gateway` 自动映射）。当 Web 服务与被测 vLLM / 执行环境在同一台机器时：

- SSH 主机填 `host.docker.internal`（或宿主机内网 IP）
- vLLM 服务 IP 同理

## 使用流程

1. **登录**：浏览器打开服务地址，输入执行机 SSH 信息（密码或上传密钥）
2. **新建任务**：选择镜像 → 填写测试配置（可浏览目录选择权重目录）→ 设计测试用例（可从引擎日志自动获取 KV cache / 最大请求长度）→ 提交
3. **跟踪**：任务列表实时展示状态；打开任务详情查看实时日志与阶段
4. **查看结果**：任务完成后查看结果表，下载 CSV / ZIP
5. **对比**：在“结果对比分析”中加载两次结果，查看指标差异与图表
6. **（可选）复制历史任务**快速发起同类测试；按容器名勾选清理环境

## 本地开发与测试

```bash
# 安装依赖
pip install -r backend/requirements.txt
pip install pytest httpx pyflakes

# 启动开发服务
$env:PYTHONPATH="backend"; python -m uvicorn app.main:app --port 8320 --reload
# 或 Linux/macOS: PYTHONPATH=backend python -m uvicorn app.main:app --port 8320 --reload

# 运行全部测试（核心模块单测 + FakeSSH API 集成测试）
python -m pytest tests/ -q
```

前端为原生 ES Modules，配套 Node 测试（jsdom 浏览器级断言）：

```bash
node tests/test_bootwatchdog.mjs        # 启动自检/慢启动/脚本404
node tests/test_password_toggle.mjs     # 登录密码显隐
node tests/test_newtask_chips.mjs       # 长度选择栏渲染
node tests/test_copy_task.mjs           # 复制历史任务
node tests/test_engine_kv_fetch.mjs     # 引擎参数自动获取(含其他服务器/命令复制)
node tests/test_logconsole.mjs          # 日志控制台(进度条折叠/历史快照)
node tests/test_platform_flow.mjs       # 任务平台主流程
```

静态检查：

```bash
python -m pyflakes backend/app tests
```

> 前端 JS 均为 ES module：pytest 会自动以模块目标逐个解析校验（`tests/test_frontend_syntax.py`）。
> 手动检查时注意 `node --check xx.js`（脚本目标）**不会**暴露“字符串内含真实换行”这类错误，
> 必须复制为 `.mjs` 后检查：`cp xx.js /tmp/c.mjs && node --check /tmp/c.mjs`。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.11 · FastAPI · Uvicorn · Paramiko |
| 前端 | 原生 HTML/CSS/JS（ES Modules），无构建步骤、无 CDN 依赖 |
| 实时日志 | WebSocket + 服务端环形缓冲，断线自动重连并补发历史 |
| 图表 | 自研 Canvas 柱状图组件 |
| 部署 | Dockerfile + docker-compose |

## 上游项目

- 桌面版原始项目：[ucm-system/AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools)
- 内置测试代码：`backend/core_code/aisbench_auto_tools_prefix`（随镜像分发）
