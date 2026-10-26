# 离线部署说明

适用于执行机无法访问外网的场景。在有外网的电脑上从
[Releases](https://github.com/student-jhz/AISBench-Prefix-Tools-Web/releases)
下载离线包, 通过 SCP / U 盘等方式传到执行机后按以下步骤部署。

## 方式一: Docker 镜像离线包(推荐)

下载 `aisbench-web-offline_<版本>_linux_<架构>.zip`(架构选择见下文)。

```bash
unzip aisbench-web-offline_<版本>_linux_<架构>.zip
cd aisbench-web-offline_<版本>_linux_<架构>
bash deploy.sh
```

脚本会自动 `docker load` 镜像并启动服务, 完成后浏览器访问
`http://<执行机IP>:8320`。上传文件与测试结果持久化在解压目录的 `data/` 下。

升级新版本: 下载新离线包重复上述步骤(同一容器名会自动重建)。

### 架构选择

在执行机上执行 `uname -m`:

| 输出 | 选择包 |
|------|--------|
| `x86_64` | `linux_amd64` |
| `aarch64` | `linux_arm64` |

## 方式二: Linux 单文件可执行(服务宿主机无 Docker 时)

如果 Web 服务不部署在执行机上、而是部署在其他没有 Docker 的机器
(服务通过 SSH 控制执行机, 执行机本身仍需 Docker):

```bash
tar xzf aisbench-web_<版本>_linux_<架构>.tar.gz
chmod +x aisbench-web
./aisbench-web          # 默认端口 8320, 可用 AISBENCH_PORT 环境变量修改
```

可执行文件已内置后端、前端与测试代码, 无需安装 Python。
数据目录默认为**运行目录**下的 `data/`, 也可用 `AISBENCH_DATA_DIR`
环境变量指定。

## 两种方式如何选择

| | Docker 镜像离线包 | 单文件可执行 |
|---|---|---|
| 服务宿主机需要 Docker | ✅ 是 | ❌ 否 |
| 适用场景 | 服务直接部署在执行机上 | 服务部署在跳板机/办公电脑, SSH 控制执行机 |
| 进程管理 | restart: unless-stopped 自动拉起 | 需自行配置 systemd / 计划任务 |

> 无论哪种方式, **被测执行机(SSH 目标)必须已安装 Docker** —— 测试容器
> 会在执行机上创建运行。
