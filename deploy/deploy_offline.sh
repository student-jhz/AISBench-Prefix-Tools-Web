#!/usr/bin/env bash
# AISBench-Prefix-Tools-Web 离线部署脚本
# 用法: 解压离线包后在当前目录执行  bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

VERSION=__VERSION__
ARCH=__ARCH__
IMAGE=aisbench-prefix-tools-web:${VERSION}
TAR=aisbench-prefix-tools-web_${VERSION}_linux_${ARCH}.docker.tar.gz
CONTAINER=aisbench-prefix-tools-web
PORT=8320

# ---------- 前置检查 ----------
if ! command -v docker >/dev/null 2>&1; then
    echo "错误: 未找到 docker 命令, 请先安装 Docker 引擎" >&2
    exit 1
fi

# 架构预检查: docker load 不校验架构, 只有 run 时才报错, 提前拦截
HOST_ARCH=$(uname -m)
case "${HOST_ARCH}" in
    x86_64)  NEED=amd64 ;;
    aarch64) NEED=arm64 ;;
    *)       NEED=unknown ;;
esac
if [ "${NEED}" != "${ARCH}" ]; then
    echo "错误: 当前主机架构 ${HOST_ARCH}(对应 ${NEED}) 与离线包架构 ${ARCH} 不匹配!" >&2
    echo "请下载对应架构的离线包: x86_64 -> linux_amd64, aarch64 -> linux_arm64" >&2
    exit 1
fi

echo "==> 加载 Docker 镜像 ${IMAGE} (文件较大, 可能需要几分钟)..."
docker load -i "${TAR}"

# ---------- 启动服务(直接 docker run, 不依赖 docker compose 插件) ----------
# 升级场景: 同名旧容器先移除
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "${CONTAINER}"; then
    echo "==> 移除旧容器 ${CONTAINER}..."
    docker rm -f "${CONTAINER}" >/dev/null
fi

echo "==> 启动服务..."
mkdir -p data
docker run -d \
    --name "${CONTAINER}" \
    -p "${PORT}:${PORT}" \
    -v "${PWD}/data:/app/data" \
    --add-host host.docker.internal:host-gateway \
    --restart unless-stopped \
    "${IMAGE}" >/dev/null

# ---------- 等待服务就绪 ----------
echo "==> 等待服务启动..."
READY=0
for i in $(seq 1 30); do
    if docker exec "${CONTAINER}" python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=2)" \
        >/dev/null 2>&1; then
        READY=1
        break
    fi
    # 容器异常退出则立即报错并打印日志
    if [ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null)" != "true" ]; then
        echo "错误: 容器启动失败, 日志如下:" >&2
        docker logs "${CONTAINER}" >&2 || true
        exit 1
    fi
    sleep 1
done

if [ "${READY}" != "1" ]; then
    echo "警告: 服务 30 秒内未就绪, 请稍后查看: docker logs ${CONTAINER}" >&2
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-<本机IP>}
echo ""
echo "=========================================="
echo " 部署完成!"
echo " 浏览器访问: http://${IP}:${PORT}"
echo " 数据目录:   ${PWD}/data (上传文件与测试结果)"
echo "=========================================="
