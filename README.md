# AISBench-Prefix-Tools-Web

[AISBench-Prefix-Tools](https://github.com/ucm-system/AISBench-Prefix-Tools) 的网页版（Web Edition）：将原 Windows 桌面 6 步向导工具完整迁移为**部署在执行机上的容器化 Web 服务**，测试人员用浏览器即可完成 Prefix Cache 性能测试的全流程。

> 📐 架构设计文档：[docs/DESIGN.md](docs/DESIGN.md)

## 为什么要有 Web 版

| | 桌面版 (Windows EXE) | Web 版 (本项目) |
|---|---|---|
| 部署形态 | 每台 Windows 电脑下载 EXE | 执行机上部署一个容器，团队浏览器访问 |
| 访问方式 | 本机 GUI | `http://<执行机IP>:8320`，跨平台（Windows/macOS/Linux/平板） |
| 分发升级 | 重新打包分发 EXE | `git pull && docker compose up -d --build` |
| 依赖 | 无（打包内置） | 仅执行机需 Docker；浏览器零依赖、离线可用（无 CDN） |
| 并发使用 | 单机单人 | 多用户共享执行机：各选各的 `aisbench_*` 容器，互不干扰（见下文"多用户使用理念"） |
| 功能 | 6 步向导 + 日志工具 + 结果对比 | ✅ 全部保留，行为一致 |

## 功能清单

### 五步测试向导（v2.0 容器资源池）

- [x] **步骤 1 · SSH 连接执行机**：密码 / 密钥（.pem、id_rsa、ed25519 等）登录，支持远程目录浏览，密码支持显示/隐藏切换（👁/🙈 图标与状态同步反馈）
- [x] **步骤 2 · 准备测试容器**：
  - **优先复用**：列出执行机上已有的 `aisbench_*` 容器，直接选择即可进入配置/测试流程（**创建容器不是必须的**）
  - **Busy 感知**：列表实时显示每个容器是否正在执行测试（区分「本服务任务执行中」与「检测到他人测试进程」）；选择 busy 容器会收到拦截提示，建议另建新容器；busy 期间界面每 5 秒自动轮询，测试结束后提示自动消除
  - **按需新建**：先从执行机已有镜像中选择（缺失才上传 tar / 指定远程 tar）→ 设置要挂载进容器的**模型父目录** → 自定义容器名（强制 `aisbench_` 前缀）→ 创建，全程 **WebSocket 实时日志**，支持取消
- [x] **步骤 3 · 配置 config.py**：表单化编辑（模型名称 / 服务 IP / Port 等在**本步骤**配置）、实时预览生成内容、写入所选容器、**回读校验**一致性、一键读取容器当前配置
  - **MODEL_PATH 从容器内目录浏览选择**（所选路径即容器内路径，直接生效，无需换算宿主机路径）
- [x] **步骤 4 · 测试用例设计**：按 KV cache 信息计算并发数/请求数，输入/输出长度勾选（超上下文自动禁用），用例增删改/复制/系数调整，低于最小请求数醒目警告，计算明细与命令预览
- [x] **步骤 5 · 执行测试**：选择执行容器（含 busy 提示）→ MODEL_PATH 预检查 → 生成 `run_tests.sh` → 容器内执行（实时日志）→ 自动下载日志 → 解析生成结果 CSV → 结果摘要表 + CSV/ZIP 下载 + 结果历史管理
- [x] **环境清理**：**按容器名勾选删除**（绝不模糊匹配/批量删除；正在执行测试的容器自动跳过，避免误伤他人任务）+ 清理远程工作目录

### 多用户使用理念（v2.0）

本工具部署在执行机上供团队多人通过浏览器使用，执行机上的 `aisbench_*` 容器构成一个**共享测试资源池**：

- **容器即工作区**：每位用户可创建专属容器（名称自定义但保持 `aisbench_` 前缀，如 `aisbench_user1_vllm`），在容器内配置和执行自己的测试任务
- **复用优先**：已有容器可直接选择进入测试流程，无需每次重新部署
- **Busy 感知**：选择/执行前实时检测容器是否正在跑测试，busy 时建议新建容器，避免多人任务挤在同一容器互相干扰（检测原理见下文「容器执行状态检测原理」）
- **安全边界**：仅识别和清理 `aisbench` 前缀容器；清理必须逐个勾选名称，其他容器（他人以其他命名规则创建的、或系统容器）一律拒绝操作

### 容器执行状态检测原理（v2.0.5）

「容器是否正在执行测试」由**两级数据源**共同判定，前端明确区分展示：

| 来源 | 实现 | 语义 | 特点 |
|---|---|---|---|
| **后台执行状态注册表**（权威） | 任务启动时在服务端写入 `data/exec_state.json`（内存 + 磁盘持久化），记录每个容器的 `status / phase / run_id / script_running / started_at / ended_at`；任务完成/失败/取消时立即迁移出 `running` | 本服务在该容器上启动的测试任务是否仍在执行 | 状态迁移与任务生命周期严格同步，不靠猜测；服务重启时残留的 `running` 自动标记为 `interrupted`，不会永久卡死 |
| **docker top 进程探测**（辅助） | `docker top <容器> -o pid,cmd`，按 `run_tests.sh / aisbench_test.py` 进程特征匹配（已过滤僵尸/defunct 进程） | 容器内是否有测试进程在跑——**可发现其他用户或手工 `docker exec` 启动的测试**，这是注册表覆盖不到的场景 | 只作辅助信号：脚本退出后即使任务还在下载/解析日志，容器也按实际空闲展示 |

`busy = 注册表 running 或 检测到测试进程`，并通过 `busy_source`（registry / process / both）标明来源。前端在 busy 期间每 5 秒轮询一次 `/api/containers/busy`，任务结束后"测试执行中"提示**自动消除**，无需手动刷新。

### 独立工具

- [x] **远程日志工具**：当前执行机或**其他服务器**临时连接；宿主机文件 / 容器内文件 / `docker logs` 三种来源；浏览目录、筛选 KV cache 信息并**一键填入步骤 5**、查看日志尾部内容
- [x] **结果对比分析**：结果历史按 run 选择（自动匹配可对比的 `results_*.csv`，无需挑文件）或上传 CSV；按输入/输出长度自动匹配用例；21 项指标（TTFT/TPOT/E2E/吞吐/QPS/命中率等）分组勾选；差异表（A/B/Δ/Δ%，正绿负红）+ A/B 同图分组柱状图 + 导出对比 CSV
- [x] **会话持久化**：SSH 连接参数/部署信息保存在服务端（服务重启自动重连执行机），测试用例设计保存在浏览器本地（页面刷新不丢失）
- [x] **实时执行日志**：测试命令以无缓冲模式（`python3 -u`）运行，执行期间日志实时回传；结果摘要表刷新后自动恢复，也可从结果历史一键查看

## 部署方式

执行机通常位于内网、无法访问外网，推荐使用 Release 中的**离线部署包**（CI 在每次发版时自动构建，无需 clone 代码 / 拉基础镜像 / 安装依赖）。

### 前置要求

- 执行机上已安装 Docker 引擎（无需 Docker Compose 插件，离线部署脚本使用 `docker run` 直接启动）
- 执行机可 SSH 到目标测试机（也可以就是本机：服务容器通过 `host.docker.internal` 访问宿主机）

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
可执行文件在 manylinux2014（glibc 2.17）环境构建，兼容 CentOS 7+ / RHEL 7+ / 麒麟 V10 / openEuler / Ubuntu 18.04+ 等主流发行版；更老系统请使用 Docker 镜像离线包（镜像自带 Python 运行时，不受宿主机 glibc 限制）。

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
└── results/    # 每次测试的结果 run（日志 + CSV），可在页面下载/删除
```

### 连接宿主机上的服务

容器内已配置 `host.docker.internal` 指向宿主机（Linux 下由 compose 的 `host-gateway` 自动映射）。当 Web 服务与被测 vLLM/执行环境在同一台机器时：

- SSH 主机填 `host.docker.internal`（或宿主机内网 IP）
- vLLM 服务 IP 同理

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

# 前端日志组件测试（PTY \r\n 与进度条刷新场景）
node tests/test_logconsole.mjs

# 前端启动自检测试（慢启动/模块失败/脚本404 等场景）
node tests/test_bootwatchdog.mjs

# 静态检查
python -m pyflakes backend/app tests
```

> 前端 JS 均为 ES module：pytest 会自动以模块目标逐个解析校验（`tests/test_frontend_syntax.py`）。
> 手动检查时注意 `node --check xx.js`（脚本目标）**不会**暴露"字符串内含真实换行"这类错误，
> 必须复制为 `.mjs` 后检查：`cp xx.js /tmp/c.mjs && node --check /tmp/c.mjs`。

## 使用流程

1. **连接**：步骤 1 输入执行机 SSH 信息（密码或上传密钥）
2. **选容器**：步骤 2 直接选择已有 `aisbench_*` 容器（推荐）；容器不存在时从已有镜像选择 → 设置模型父目录挂载 → 命名 → 创建
3. **配置**：步骤 3 填写模型服务参数（名称 / IP / Port）并保存到所选容器（可回读校验）
4. **设计**：步骤 4 填入 KV cache 信息（可从日志工具一键获取）生成用例，按需增删改
5. **执行**：步骤 5 选择执行容器（busy 时不建议复用）执行测试，完成后查看/下载结果 CSV
6. **（可选）**：按容器名勾选清理环境；在"结果对比分析"中加载两次结果，查看指标差异

## 截图

<!-- TODO: 补充界面截图 -->

| 五步向导 | 日志工具 | 结果对比 |
|---|---|---|
| ![wizard](docs/screenshots/wizard.png) | ![logtools](docs/screenshots/logtools.png) | ![compare](docs/screenshots/compare.png) |

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
