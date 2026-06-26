@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  智能投研Agent - 后端打包脚本 (Windows)
echo ============================================

set PYTHON_VERSION=3.14.4
set PYTHON_EMBED_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip
set SCRIPT_DIR=%~dp0
set BUILD_DIR=%SCRIPT_DIR%build\backend
set BACKEND_DIR=%SCRIPT_DIR%..\backend
set FRONTEND_BUILD=%SCRIPT_DIR%..\frontend\build-backend

REM 清理旧的构建目录
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
mkdir "%BUILD_DIR%\python"

echo.
echo [1/5] 下载 Python %PYTHON_VERSION% 嵌入式包...
if not exist "%SCRIPT_DIR%build\python-%PYTHON_VERSION%-embed-amd64.zip" (
    echo 正在下载...
    curl -L -o "%SCRIPT_DIR%build\python-%PYTHON_VERSION%-embed-amd64.zip" "%PYTHON_EMBED_URL%"
    if errorlevel 1 (
        echo 错误: 下载 Python 失败
        echo 请手动下载: %PYTHON_EMBED_URL%
        echo 放到 scripts\build\ 目录后重新运行
        exit /b 1
    )
) else (
    echo 已缓存，跳过下载
)

echo.
echo [2/5] 解压 Python 嵌入式包...
powershell -Command "Expand-Archive -Path '%SCRIPT_DIR%build\python-%PYTHON_VERSION%-embed-amd64.zip' -DestinationPath '%BUILD_DIR%\python' -Force"
if errorlevel 1 (
    echo 错误: 解压失败
    exit /b 1
)

REM 启用 pip 和 site-packages (修改 python314._pth 文件)
set PTH_FILE=%BUILD_DIR%\python\python314._pth
(
    echo python314.zip
    echo .
    echo Lib
    echo Lib\site-packages
    echo .
    echo import site
) > "%PTH_FILE%"

REM 创建 Lib/site-packages 目录
mkdir "%BUILD_DIR%\python\Lib\site-packages" 2>nul

REM 下载 get-pip.py
echo.
echo [3/5] 安装 pip...
if not exist "%SCRIPT_DIR%build\get-pip.py" (
    curl -L -o "%SCRIPT_DIR%build\get-pip.py" https://bootstrap.pypa.io/get-pip.py
)
"%BUILD_DIR%\python\python.exe" "%SCRIPT_DIR%build\get-pip.py" --no-warn-script-location
if errorlevel 1 (
    echo 错误: pip 安装失败
    exit /b 1
)

echo.
echo [4/5] 安装后端依赖（生产精简清单）...
"%BUILD_DIR%\python\python.exe" -m pip install -r "%BACKEND_DIR%\requirements-prod.txt" --no-warn-script-location
if errorlevel 1 (
    echo 错误: 依赖安装失败
    exit /b 1
)

REM 下载并复制 Node.js（与开发环境 v22 一致，供生产模式 backend 使用）
echo.
echo [5/5] 下载 Node.js v22.14.0（供生产模式后端使用）...
set NODE_VERSION=22.14.0
set NODE_ZIP=%SCRIPT_DIR%build\node-v%NODE_VERSION%-win-x64.zip
if not exist "%NODE_ZIP%" (
    echo 下载 Node v%NODE_VERSION%...
    curl -L -o "%NODE_ZIP%" "https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip"
    if errorlevel 1 (
        echo 错误: 下载 Node 失败
        echo 请手动下载: https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip
        echo 放到 scripts\build\ 目录后重新运行
        exit /b 1
    )
) else (
    echo 已缓存，跳过下载
)
mkdir "%BUILD_DIR%\node" 2>nul
powershell -Command "Expand-Archive -Path '%NODE_ZIP%' -DestinationPath '%SCRIPT_DIR%build\node-tmp-%NODE_VERSION%' -Force"
copy "%SCRIPT_DIR%build\node-tmp-%NODE_VERSION%\node-v%NODE_VERSION%-win-x64\node.exe" "%BUILD_DIR%\node\node.exe" /Y

REM 复制后端代码到构建目录
echo.
echo 复制后端代码...
xcopy "%BACKEND_DIR%\harness" "%BUILD_DIR%\harness\" /E /I /Y /Q
xcopy "%BACKEND_DIR%\skills" "%BUILD_DIR%\skills\" /E /I /Y /Q
xcopy "%BACKEND_DIR%\worker" "%BUILD_DIR%\worker\" /E /I /Y /Q
xcopy "%BACKEND_DIR%\resources" "%BUILD_DIR%\resources\" /E /I /Y /Q
copy "%BACKEND_DIR%\worker_main.py" "%BUILD_DIR%\worker_main.py" /Y
copy "%BACKEND_DIR%\requirements-prod.txt" "%BUILD_DIR%\requirements-prod.txt" /Y

REM 复制 .claude/skills/ (SDK Skills，包含妙想等)
if exist "%BACKEND_DIR%\.claude\skills" (
    echo 复制 SDK Skills...
    if exist "%BUILD_DIR%\.claude\skills" rmdir /s /q "%BUILD_DIR%\.claude\skills"
    xcopy "%BACKEND_DIR%\.claude\skills" "%BUILD_DIR%\.claude\skills\" /E /I /Y /Q
)

REM 创建数据目录
mkdir "%BUILD_DIR%\data" 2>nul

REM 复制到 frontend/build-backend 供 electron-builder 使用
echo.
echo 复制到前端构建目录...
if exist "%FRONTEND_BUILD%" rmdir /s /q "%FRONTEND_BUILD%"
xcopy "%BUILD_DIR%" "%FRONTEND_BUILD%\" /E /I /Y /Q

echo.
echo ============================================
echo  后端打包完成!
echo  构建目录: %BUILD_DIR%
echo  前端资源: %FRONTEND_BUILD%
echo ============================================
