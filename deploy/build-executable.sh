#!/bin/bash
# 在 manylinux2014 (glibc 2.17) 容器内, 用 python-build-standalone CPython
# + PyInstaller 打包单文件可执行 aisbench-web
#
# 背景: v1.0.2/v1.0.3 的 workflow 内联 `docker run ... bash -c '...'` 打包在 CI
# 上确定性失败(exit 1), 且事后无法取回容器内的输出。改为检入仓库的脚本:
#   1. 全程 set -x 留痕, workflow 侧 tee 到 build.log, 失败时自动推送到
#      ci-logs 分支, 不登录 GitHub 也能看到完整错误;
#   2. 不用镜像自带的 /opt/python/cp311 —— 那是静态库构建(无共享 libpython),
#      PyInstaller 打 onefile 硬性要求共享 libpython, 会直接报
#      "Python was built without a shared library"(v1.0.2~v1.0.4 失败根因)。
#      改用 python-build-standalone 预编译发行版: 自带 lib/libpython3.11.so.1.0,
#      glibc 基线 2.17, 与 manylinux2014 容器组合后产物兼容
#      CentOS 7+ / RHEL 7+ / 麒麟V10 / openEuler / Ubuntu 18.04+;
#   3. 用 `python -m PyInstaller` 调用, 避免入口脚本的 shebang/PATH 问题;
#   4. 容器内以 root 构建, 结束时把产物 chown 回 runner 用户。
#
# 用法(仓库根目录, 由 .github/workflows/release.yml 调用):
#   docker run --rm -v "$PWD:/src" -w /src \
#     -e RUNNER_UID="$(id -u)" -e RUNNER_GID="$(id -g)" \
#     quay.io/pypa/manylinux2014_x86_64 /bin/bash /src/deploy/build-executable.sh
set -euxo pipefail

# ---- 下载共享库构建的 CPython (python-build-standalone) ----
case "$(uname -m)" in
  x86_64)  PBS_ARCH=x86_64 ;;
  aarch64) PBS_ARCH=aarch64 ;;
  *) echo "不支持的构建架构: $(uname -m)" >&2; exit 1 ;;
esac
PBS_TAG=20260114
PBS_VERSION=3.11.14
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PBS_VERSION}+${PBS_TAG}-${PBS_ARCH}-unknown-linux-gnu-install_only.tar.gz"

echo "== 下载 python-build-standalone CPython ${PBS_VERSION} (${PBS_ARCH}) =="
curl -fsSL --retry 3 --retry-delay 2 -o /tmp/cpython.tar.gz "$PBS_URL"
rm -rf /tmp/pbs && mkdir -p /tmp/pbs
tar -xzf /tmp/cpython.tar.gz -C /tmp/pbs --strip-components=1
PYTHON=/tmp/pbs/bin/python3.11
# 保险: 让 ldd / PyInstaller 能解析到 PBS 自带的 libpython3.11.so.1.0
export LD_LIBRARY_PATH="/tmp/pbs/lib:${LD_LIBRARY_PATH:-}"

echo "== 构建环境 =="
id
uname -a
"$PYTHON" --version
"$PYTHON" -m pip --version
"$PYTHON" - <<'PY'
import sysconfig
assert sysconfig.get_config_var("Py_ENABLE_SHARED"), \
    "Py_ENABLE_SHARED=0: PyInstaller 需要共享库构建的 Python"
print("Py_ENABLE_SHARED:", sysconfig.get_config_var("Py_ENABLE_SHARED"))
print("libpython:", sysconfig.get_config_var("LDLIBRARY"))
PY

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
