#!/usr/bin/env bash
# AISBench-Prefix-Tools-Web 离线部署脚本
# 用法: 解压离线包后在当前目录执行  bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

VERSION=__VERSION__
ARCH=__ARCH__
IMAGE=aisbench-prefix-tools-web:${VERSION}
TAR=aisbench-prefix-tools-web_${VERSION}_linux_${ARCH}.docker.tar.gz

echo "==> 加载 Docker 镜像 ${IMAGE} (文件较大, 可能需要几分钟)..."
docker load -i "${TAR}"

echo "==> 启动服务..."
docker compose up -d

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<本机IP>")
echo ""
echo "=========================================="
echo " 部署完成!"
echo " 浏览器访问: http://${IP}:8320"
echo " 数据目录:   ${PWD}/data (上传文件与测试结果)"
echo "=========================================="
