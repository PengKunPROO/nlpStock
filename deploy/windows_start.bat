@echo off
chcp 65001 >nul
title 策略选股器（关闭本窗口即停止服务）
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请安装 Python 3.10+ 并在安装时勾选 "Add python.exe to PATH"。
  echo 下载地址: https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  echo [首次运行] 正在安装依赖...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

echo 后端服务启动中，浏览器将自动打开 http://localhost:8000
start "" cmd /c "timeout /t 2 >nul & start http://localhost:8000"
python -m backend.app --host 0.0.0.0 --port 8000
pause
