#!/bin/bash
set -e

echo "============================================"
echo " 智能投研Agent - 一键构建 (macOS)"
echo "============================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/../frontend"

echo ""
echo "[Step 1/3] 打包后端 Python 环境..."
"$SCRIPT_DIR/pack-backend.sh"

echo ""
echo "[Step 2/3] 安装前端依赖..."
cd "$FRONTEND_DIR"
npm install

echo ""
echo "[Step 3/3] 构建 Electron DMG..."
npm run build:mac

echo ""
echo "============================================"
echo " 构建完成!"
echo " 安装包位置: $FRONTEND_DIR/dist/"
echo "============================================"
open "$FRONTEND_DIR/dist"
