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
| 并发使用 | 单机单人 | 单会话模型（与桌面版一致），多人共用同一环境状态 |
| 功能 | 6 步向导 + 日志工具 + 结果对比 | ✅ 全部保留，行为一致 |

## 功能清单

### 六步测试向导

- [x] **步骤 1 · SSH 连接执行机**：密码 / 密钥（.pem、id_rsa、ed25519 等）登录，支持远程目录浏览
- [x] **步骤 2 · 文件与模型**：
  - 镜像 tar 上传（带进度条、自动预读 manifest 识别镜像名）或直接使用执行机上已有的远程 tar
  - 测试代码：内置 `aisbench_auto_tools_prefix` 或上传自定义 zip
  - 模型权重路径远程浏览选择、vLLM 服务信息配置
- [x] **步骤 3 · Docker 部署**：检查 Docker 环境 → 镜像去重（已存在则跳过上传加载）→ 上传镜像/代码 → 创建并启动容器，全程 **WebSocket 实时日志**，支持取消
- [x] **步骤 4 · 配置 config.py**：表单化编辑、实时预览生成内容、写入容器、**回读校验**一致性、一键读取容器当前配置
- [x] **步骤 5 · 测试用例设计**：按 KV cache 信息计算并发数/请求数，输入/输出长度勾选（超上下文自动禁用），用例增删改/复制/系数调整，低于最小请求数醒目警告，计算明细与命令预览
- [x] **步骤 6 · 执行测试**：MODEL_PATH 预检查 → 生成 `run_tests.sh` → 容器内执行 → 自动下载日志 → 解析生成结果 CSV → 结果摘要表 + CSV/ZIP 下载 + 结果历史管理
- [x] **环境清理**：删除测试容器 + 清理远程工作目录

### 独立工具

- [x] **远程日志工具**：当前执行机或**其他服务器**临时连接；宿主机文件 / 容器内文件 / `docker logs` 三种来源；浏览目录、筛选 KV cache 信息并**一键填入步骤 5**、查看日志尾部内容
- [x] **结果对比分析**：两份结果 CSV（历史 run 或上传），按输入/输出长度自动匹配用例；21 项指标（TTFT/TPOT/E2E/吞吐/QPS/命中率等）分组勾选；差异表（A/B/Δ/Δ%，正绿负红）+ 双柱状图 + 导出对比 CSV

## 快速部署（推荐）

### 前置要求

- 执行机上已安装 Docker 与 Docker Compose（v2）
- 执行机可 SSH 到目标测试机（也可以就是本机：服务容器通过 `host.docker.internal` 访问宿主机）

### 一键启动

```bash
git clone https://github.com/student-jhz/AISBench-Prefix-Tools-Web.git
cd AISBench-Prefix-Tools-Web
docker compose up -d --build
```

浏览器访问：**http://\<执行机IP\>:8320**

升级到新版本：

```bash
git pull
docker compose up -d --build
```

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

# 静态检查
python -m pyflakes backend/app tests
```

## 使用流程

1. **连接**：步骤 1 输入执行机 SSH 信息（密码或上传密钥）
2. **准备**：步骤 2 上传镜像 tar / 指定远程 tar，选择模型路径，填写 vLLM 地址
3. **部署**：步骤 3 开始部署，实时查看日志直至容器启动
4. **配置**：步骤 4 填写测试参数并保存到容器（可回读校验）
5. **设计**：步骤 5 填入 KV cache 信息（可从日志工具一键获取）生成用例，按需增删改
6. **执行**：步骤 6 执行测试，完成后查看/下载结果 CSV；测试结束后可清理环境
7. **（可选）对比**：在"结果对比分析"中加载两次结果，查看指标差异

## 截图

<!-- TODO: 补充界面截图 -->

| 六步向导 | 日志工具 | 结果对比 |
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
