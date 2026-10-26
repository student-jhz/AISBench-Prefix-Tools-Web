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

> 前置要求仅需 **Docker 引擎**, 不需要安装 Docker Compose 插件
> (脚本使用 `docker run` 直接启动容器)。

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
在 manylinux2014 (glibc 2.17) 环境构建, 兼容 CentOS 7+ / RHEL 7+ /
麒麟 V10 / openEuler / Ubuntu 18.04+ 等主流发行版 (aarch64 与 x86_64)。
更老的系统请改用方式一。
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

## 常见问题

### GitHub Release 下载页没有文件 / Actions 失败

Release 页面空说明发布流水线没有跑完 —— 全部产物构建成功后才会上传。
到仓库 Actions 页查看 `Release` 工作流: 若 job 显示红叉, 在其中找到失败
步骤查看日志; 全绿后 Release 页才会出现文件。v1.0.2/v1.0.3 曾因构建
环境问题发布失败, v1.0.5 起已修复, 请下载最新版本。

### `docker: 'compose' is not a docker command`

新版 `deploy.sh` 已不使用 Compose。如使用旧版离线包, 可手动启动:

```bash
docker load -i aisbench-prefix-tools-web_<版本>_linux_<架构>.docker.tar.gz
docker run -d --name aisbench-prefix-tools-web \
  -p 8320:8320 \
  -v "$PWD/data:/app/data" \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  aisbench-prefix-tools-web:<版本>
```

### `GLIBC_x.xx not found`

可执行文件对系统 glibc 版本有要求。如报
`version 'GLIBC_2.xx' not found`, 说明系统 glibc 低于构建环境
(v1.0.5 起在 glibc 2.17 环境构建)。遇到时:

1. 优先改用 **Docker 镜像离线包**(方式一), 镜像内自带 Python 运行时,
   不受宿主机 glibc 限制;
2. 或在有外网的机器上下载本页对应架构的离线包后传输部署。
