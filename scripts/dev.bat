@echo off
echo ===================================
echo 智能投研Agent - 开发环境启动
echo ===================================

REM 检查Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo 错误: 未找到 python
    exit /b 1
)

REM 检查Node
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo 错误: 未找到 node
    exit /b 1
)

REM 设置环境变量
set HARNESS_ENV=dev
set NODE_ENV=development

REM 创建必要的目录
if not exist backend\logs mkdir backend\logs
if not exist backend\data mkdir backend\data

REM 启动后端
echo 启动后端服务...
start /B python backend\main.py

REM 等待后端启动
timeout /t 3 /nobreak >nul

REM 启动前端
echo 启动前端应用...
cd frontend
start npm run dev
cd ..

echo ===================================
echo 后端和前端已启动
echo ===================================
echo 请查看新打开的窗口
echo 按任意键关闭此窗口
pause >nul
