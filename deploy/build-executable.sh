#!/bin/bash
# 在 manylinux_2_28 (glibc 2.28) 容器内用 PyInstaller 打包单文件可执行 aisbench-web
#
# 背景: v1.0.2/v1.0.3 的 workflow 内联 `docker run ... bash -c '...'` 打包在 CI
# 上确定性失败(exit 1), 且事后无法取回容器内的输出。改为检入仓库的脚本:
#   1. 全程 set -x 留痕, workflow 侧 tee 到 build.log, 失败时自动推送到
#      ci-logs 分支, 不登录 GitHub 也能看到完整错误;
#   2. 不再创建 venv、不再 `pip install -U pip`(两者均为嫌疑失败点), 直接
#      用镜像内置 pip 安装依赖 —— 与 v1.0.1 成功发布的做法一致;
#   3. 用 `python -m PyInstaller` 调用, 避免入口脚本的 shebang/PATH 问题;
#   4. 容器内以 root 构建, 结束时把产物 chown 回 runner 用户。
#
# 用法(仓库根目录, 由 .github/workflows/release.yml 调用):
#   docker run --rm -v "$PWD:/src" -w /src \
#     -e RUNNER_UID="$(id -u)" -e RUNNER_GID="$(id -g)" \
#     quay.io/pypa/manylinux_2_28_x86_64 /bin/bash /src/deploy/build-executable.sh
set -euxo pipefail

PYTHON=/opt/python/cp311-cp311/bin/python

echo "== 构建环境 =="
id
uname -a
ls -1 /opt/python/
"$PYTHON" --version
"$PYTHON" -m pip --version

echo "== 安装后端依赖与 PyInstaller =="
"$PYTHON" -m pip install --no-cache-dir --disable-pip-version-check \
  -r backend/requirements.txt pyinstaller
"$PYTHON" -m PyInstaller --version

echo "== PyInstaller 打包 =="
cd /src
"$PYTHON" -m PyInstaller deploy/aisbench-web.spec \
  --noconfirm --distpath dist --workpath build

echo "== 产物属主交还 runner 用户 =="
if [ -n "${RUNNER_UID:-}" ]; then
  chown -R "${RUNNER_UID}:${RUNNER_GID:-${RUNNER_UID}}" dist build
fi
ls -lh dist/
