#!/bin/bash

# 开发环境启动脚本

echo "==================================="
echo "智能投研Agent - 开发环境启动"
echo "==================================="

# 检查环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo "错误: 未找到 node"
    exit 1
fi

# 设置环境变量
export HARNESS_ENV=dev
export NODE_ENV=development

# 创建必要的目录
mkdir -p backend/logs
mkdir -p backend/data

# 启动后端
echo "启动后端服务..."
cd backend
python3 main.py &
BACKEND_PID=$!
cd ..

# 等待后端启动
sleep 3

# 启动前端
echo "启动前端应用..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo "==================================="
echo "后端 PID: $BACKEND_PID"
echo "前端 PID: $FRONTEND_PID"
echo "==================================="
echo "按 Ctrl+C 停止所有服务"

# 等待信号
trap "kill $BACKEND_PID $FRONTEND_PID; exit" INT TERM

wait
