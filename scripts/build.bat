@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  灵智投研助手 - 一键构建 (Windows)
echo ============================================

set SCRIPT_DIR=%~dp0
set FRONTEND_DIR=%SCRIPT_DIR%..\frontend
set BACKEND_DIR=%SCRIPT_DIR%..\backend

REM 杀掉残留的 Electron 进程
echo.
echo [Step 0] 清理残留进程...
taskkill /f /im electron.exe 2>nul
taskkill /f /im "灵智投研助手.exe" 2>nul

REM 检查是否跳过后端打包（增量构建）
if "%1"=="--skip-backend" (
    echo.
    echo [跳过] 后端打包 (--skip-backend)
    echo 同步最新代码到 build-backend...
    REM 增量同步后端代码（不重装依赖）
    xcopy "%BACKEND_DIR%\harness" "%FRONTEND_DIR%\build-backend\harness\" /E /I /Y /Q
    xcopy "%BACKEND_DIR%\resources" "%FRONTEND_DIR%\build-backend\resources\" /E /I /Y /Q
    copy "%BACKEND_DIR%\main.py" "%FRONTEND_DIR%\build-backend\main.py" /Y
    copy "%BACKEND_DIR%\requirements.txt" "%FRONTEND_DIR%\build-backend\requirements.txt" /Y
    REM 同步 SDK Skills
    if exist "%BACKEND_DIR%\.claude\skills" (
        if exist "%FRONTEND_DIR%\build-backend\.claude\skills" rmdir /s /q "%FRONTEND_DIR%\build-backend\.claude\skills"
        xcopy "%BACKEND_DIR%\.claude\skills" "%FRONTEND_DIR%\build-backend\.claude\skills\" /E /I /Y /Q
    )
    echo 代码同步完成
    goto :frontend
)

echo.
echo [Step 1/3] 打包后端 Python 环境...
call "%SCRIPT_DIR%pack-backend.bat"
if errorlevel 1 (
    echo 错误: 后端打包失败
    exit /b 1
)

:frontend
echo.
echo [Step 2/3] 安装前端依赖...
cd /d "%FRONTEND_DIR%"
call npm install
if errorlevel 1 (
    echo 错误: 前端依赖安装失败
    exit /b 1
)

echo.
echo [Step 3/3] 构建 Electron 安装包...
set ELECTRON_BUILDER_BINARIES_MIRROR=https://ghfast.top/https://github.com/electron-userland/electron-builder-binaries/releases/download/
set WIN_CSC_IDENTITY_AUTO_DISCOVERY=false
call npm run build:win
if errorlevel 1 (
    echo 错误: electron-builder 构建失败
    exit /b 1
)

echo.
echo ============================================
echo  构建完成!
echo  安装包位置: %FRONTEND_DIR%\dist\
echo ============================================
explorer "%FRONTEND_DIR%\dist"
