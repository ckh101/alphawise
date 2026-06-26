#!/bin/bash
set -e

# ============================================================================
# 灵智投研助手 - 一键构建 (macOS)
# 对等 scripts/build.bat
# ============================================================================

echo "============================================"
echo " 灵智投研助手 - 一键构建 (macOS)"
echo "============================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/../frontend"
BACKEND_DIR="$SCRIPT_DIR/../backend"

# Step 0: 清理残留进程
echo ""
echo "[Step 0] 清理残留进程..."
pkill -f "灵智投研助手" 2>/dev/null || true
pkill -f "Electron" 2>/dev/null || true
# 杀应用专属 worker (命令行含 worker_main.py)
pkill -f "worker_main.py" 2>/dev/null || true

# 增量构建选项: --skip-backend 只同步代码不重装依赖
if [ "$1" = "--skip-backend" ]; then
    echo ""
    echo "[跳过] 后端打包 (--skip-backend)"
    echo "同步最新代码到 build-backend..."
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$BACKEND_DIR/harness/" "$FRONTEND_BUILD/harness/"
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$BACKEND_DIR/skills/" "$FRONTEND_BUILD/skills/"
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$BACKEND_DIR/worker/" "$FRONTEND_BUILD/worker/"
    rsync -a --delete "$BACKEND_DIR/resources/" "$FRONTEND_BUILD/resources/"
    cp "$BACKEND_DIR/worker_main.py" "$FRONTEND_BUILD/worker_main.py"
    if [ -d "$BACKEND_DIR/.claude/skills" ]; then
        rm -rf "$FRONTEND_BUILD/.claude/skills"
        cp -R "$BACKEND_DIR/.claude/skills" "$FRONTEND_BUILD/.claude/skills"
    fi
    echo "代码同步完成"
else
    # Step 1: 打包后端 Python 环境
    echo ""
    echo "[Step 1/3] 打包后端 Python 环境..."
    bash "$SCRIPT_DIR/pack-backend.sh"
    if [ $? -ne 0 ]; then
        echo "错误: 后端打包失败"
        exit 1
    fi
fi

# Step 2: 安装前端依赖
echo ""
echo "[Step 2/3] 安装前端依赖..."
cd "$FRONTEND_DIR"
npm install
if [ $? -ne 0 ]; then
    echo "错误: 前端依赖安装失败"
    exit 1
fi

# Step 3: 构建 Electron dmg
echo ""
echo "[Step 3/3] 构建 Electron 安装包 (dmg)..."
export CSC_IDENTITY_AUTO_DISCOVERY=false  # 跳过自动签名发现 (内测不签名)
npm run build:mac
if [ $? -ne 0 ]; then
    echo "错误: electron-builder 构建失败"
    exit 1
fi

echo ""
echo "============================================"
echo " 构建完成!"
echo " 安装包位置: $FRONTEND_DIR/dist/"
echo "============================================"
open "$FRONTEND_DIR/dist"
