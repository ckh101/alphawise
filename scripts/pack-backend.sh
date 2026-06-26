#!/bin/bash
set -e

echo "============================================"
echo " 智能投研Agent - 后端打包脚本 (macOS)"
echo "============================================"
echo ""
echo "策略: 自带 python.org universal2 Python.framework (绿色可移植)"
echo "      依赖装到 framework 同级的 site-packages (PYTHONPATH 指向)"
echo "      不用 venv (venv 含构建机绝对路径，不可移植)"
echo ""

PYTHON_VERSION="3.14.4"
PYTHON_PKG_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-macos11.pkg"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build/backend"
BACKEND_DIR="$SCRIPT_DIR/../backend"
FRONTEND_BUILD="$SCRIPT_DIR/../frontend/build-backend"
PKG_CACHE="$SCRIPT_DIR/build/python-${PYTHON_VERSION}-macos11.pkg"
WORK_DIR="$SCRIPT_DIR/build/_pkg_extract"

cd "$SCRIPT_DIR"

# 清理旧的构建目录
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ============================================================================
# [1/6] 下载 python.org universal2 pkg (universal2 同时含 x64 + arm64)
# ============================================================================
echo "[1/6] 下载 Python ${PYTHON_VERSION} macOS universal2 pkg..."
if [ ! -f "$PKG_CACHE" ]; then
    echo "正在下载..."
    curl -L -o "$PKG_CACHE" "$PYTHON_PKG_URL"
    if [ $? -ne 0 ]; then
        echo "错误: 下载 Python pkg 失败"
        echo "请手动下载: $PYTHON_PKG_URL"
        echo "放到 scripts/build/ 目录后重新运行"
        exit 1
    fi
else
    echo "已缓存，跳过下载"
fi

# ============================================================================
# [2/6] 解包 pkg，提取 Python.framework
# ============================================================================
echo ""
echo "[2/6] 解包 pkg 提取 Python.framework..."
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
# xar 解包 pkg -> 解开 payload（cpio.gz）-> 提取 Python.framework
xar -xf "$PKG_CACHE"
# payload 文件名可能是 Payload
PAYLOAD="$WORK_DIR/Payload"
if [ ! -f "$PAYLOAD" ]; then
    echo "错误: pkg 内未找到 Payload 文件"
    exit 1
fi
# 解压 payload (gzipped cpio)
mkdir -p "$WORK_DIR/payload"
cd "$WORK_DIR/payload"
gunzip -dc "$PAYLOAD" | cpio -i --quiet
# Python.framework 通常在 Library/Frameworks/Python.framework
FRAMEWORK_SRC="$WORK_DIR/payload/Library/Frameworks/Python.framework"
if [ ! -d "$FRAMEWORK_SRC" ]; then
    echo "错误: 未找到 Python.framework"
    echo "实际内容:"
    find "$WORK_DIR/payload" -name "Python.framework" -maxdepth 5 2>/dev/null
    exit 1
fi

# framework 版本化子目录 (Versions/3.14)
FW_VER="Versions/${PYTHON_VERSION%.*}"  # 3.14
FRAMEWORK_BIN="$FRAMEWORK_SRC/$FW_VER/bin"
PYTHON3_EXE="$FRAMEWORK_BIN/python3"
if [ ! -x "$PYTHON3_EXE" ]; then
    echo "错误: framework 内未找到可执行 python3 ($FRAMEWORK_BIN)"
    ls -la "$FRAMEWORK_BIN" 2>/dev/null
    exit 1
fi
echo "framework python: $($PYTHON3_EXE --version 2>&1)"

# ============================================================================
# [3/6] 把 Python.framework 拷进构建目录
# ============================================================================
echo ""
echo "[3/6] 复制 Python.framework 到构建目录..."
# 只拷带版本号的实际内容，扁平化为 build-backend/python/
# 结构: python/bin/python3, python/lib/python3.14/..., python/Python.framework/...
mkdir -p "$BUILD_DIR/python"
# 拷整个 framework (universal2，两架构都带)
cp -R "$FRAMEWORK_SRC" "$BUILD_DIR/python/Python.framework"

# 在 python/ 下建立 bin 软链，指向 framework 的 python3
mkdir -p "$BUILD_DIR/python/bin"
ln -sf "../Python.framework/$FW_VER/bin/python3" "$BUILD_DIR/python/bin/python"
ln -sf "../Python.framework/$FW_VER/bin/python3" "$BUILD_DIR/python/bin/python3"

# 确保 framework 内的二进制动态库用 @rpath/@loader_path 相对引用 (python.org 官方包已是 relocatable)
echo "framework 二进制依赖 (确认无构建机绝对路径):"
otool -L "$BUILD_DIR/python/Python.framework/$FW_VER/bin/python3" 2>&1 | grep -vE "@rpath|@loader_path|/usr/lib|/System" | head || echo "  (全部为相对/系统路径，OK)"

# ============================================================================
# [4/6] 安装生产依赖到独立 site-packages (--target，不污染 framework)
# ============================================================================
echo ""
echo "[4/6] 安装生产依赖（精简清单）..."
SITE_PKG="$BUILD_DIR/python/lib/python${PYTHON_VERSION%.*}/site-packages"
mkdir -p "$SITE_PKG"

# 用 framework 的 python3，把依赖装到 --target 目录
# --target 装的包是纯文件拷贝，不含构建机绝对路径，运行时通过 PYTHONPATH 指向
"$BUILD_DIR/python/bin/python3" -m pip install \
    -r "$BACKEND_DIR/requirements-prod.txt" \
    --target "$SITE_PKG" \
    --no-warn-script-location \
    --compile
if [ $? -ne 0 ]; then
    echo "错误: 依赖安装失败"
    exit 1
fi

# 清理 __pycache__ 和测试目录，减小体积
find "$SITE_PKG" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$SITE_PKG" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
find "$SITE_PKG" -name "*.pyc" -delete 2>/dev/null || true

# ============================================================================
# [5/6] 复制后端代码
# ============================================================================
echo ""
echo "[5/6] 复制后端代码..."
cp -R "$BACKEND_DIR/harness" "$BUILD_DIR/harness"
cp -R "$BACKEND_DIR/skills" "$BUILD_DIR/skills"
cp -R "$BACKEND_DIR/worker" "$BUILD_DIR/worker"
cp -R "$BACKEND_DIR/resources" "$BUILD_DIR/resources"
cp "$BACKEND_DIR/worker_main.py" "$BUILD_DIR/worker_main.py"
cp "$BACKEND_DIR/requirements-prod.txt" "$BUILD_DIR/requirements-prod.txt"

# 复制 .claude/skills (SDK Skills)
if [ -d "$BACKEND_DIR/.claude/skills" ]; then
    echo "复制 SDK Skills..."
    rm -rf "$BUILD_DIR/.claude/skills"
    cp -R "$BACKEND_DIR/.claude/skills" "$BUILD_DIR/.claude/skills"
fi

# 清理代码 __pycache__
find "$BUILD_DIR/harness" "$BUILD_DIR/skills" "$BUILD_DIR/worker" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

mkdir -p "$BUILD_DIR/data"

# ============================================================================
# [6/6] 下载 Node.js（与开发环境 v22 一致，供生产模式后端使用）
#       electron 28 自带 node 18.18 跑不动 Fastify 5（缺 diagnostics.tracingChannel）
#       按当前 mac 架构下载 (arm64 / x64)，node 官方无 universal2 发行版
# ============================================================================
echo ""
echo "[6/6] 下载 Node.js v22.14.0（按当前架构）..."
NODE_VERSION="22.14.0"
ARCH="$(uname -m)"
case "$ARCH" in
    arm64)  NODE_ARCH="arm64" ;;
    x86_64) NODE_ARCH="x64"   ;;
    *) echo "错误: 不支持的架构 $ARCH"; exit 1 ;;
esac
NODE_TAR="$SCRIPT_DIR/build/node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
if [ ! -f "$NODE_TAR" ]; then
    echo "下载 Node v${NODE_VERSION} darwin-${NODE_ARCH}..."
    curl -L -o "$NODE_TAR" "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
    if [ $? -ne 0 ]; then
        echo "错误: 下载 Node 失败"
        echo "请手动下载: https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
        echo "放到 scripts/build/ 目录后重新运行"
        exit 1
    fi
else
    echo "已缓存，跳过下载"
fi
mkdir -p "$BUILD_DIR/node"
# 解压并把 bin/node 放到 build-backend/node/bin/node
NODE_EXTRACT="$SCRIPT_DIR/build/_node_extract_${NODE_ARCH}"
rm -rf "$NODE_EXTRACT"
mkdir -p "$NODE_EXTRACT"
tar -xzf "$NODE_TAR" -C "$NODE_EXTRACT"
mkdir -p "$BUILD_DIR/node/bin"
cp "$NODE_EXTRACT/node-v${NODE_VERSION}-darwin-${NODE_ARCH}/bin/node" "$BUILD_DIR/node/bin/node"
rm -rf "$NODE_EXTRACT"
echo "已放置 node v${NODE_VERSION} darwin-${NODE_ARCH} → build-backend/node/bin/node"

# 清理解包临时目录
cd "$SCRIPT_DIR"
rm -rf "$WORK_DIR"

# ============================================================================
# 复制到 frontend/build-backend 供 electron-builder 使用
# ============================================================================
echo ""
echo "复制到前端构建目录..."
rm -rf "$FRONTEND_BUILD"
cp -R "$BUILD_DIR" "$FRONTEND_BUILD"

echo ""
echo "============================================"
echo " 后端打包完成!"
echo " 构建目录: $BUILD_DIR"
echo " 前端资源: $FRONTEND_BUILD"
echo ""
echo " 验证 (在另一台干净 Mac 上):"
echo "   PYTHONPATH=\$FRONTEND_BUILD/python/lib/python${PYTHON_VERSION%.*}/site-packages \\"
echo "   \$FRONTEND_BUILD/python/bin/python \$FRONTEND_BUILD/worker_main.py"
echo "============================================"
