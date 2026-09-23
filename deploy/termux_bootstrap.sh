#!/data/data/com.termux/files/usr/bin/bash
# 策略选股 · Termux 一键部署（安卓手机闭环运行）
# 用法: bash termux_bootstrap.sh
set -e

echo "== 1/4 更新包管理器 =="
pkg update -y && pkg upgrade -y

echo "== 2/4 安装 Python 与基础工具 =="
pkg install -y python python-pip git

echo "== 3/4 安装后端依赖 =="
pip install --upgrade pip
pip install fastapi uvicorn httpx

echo "== 4/4 启动服务 =="
# 项目目录即脚本所在目录；进入后启动
cd "$(dirname "$0")/.." || exit 1
echo ""
echo "服务已启动: http://localhost:8000"
echo "手机浏览器打开上述地址，点菜单 →「添加到主屏幕」即可像 App 一样使用。"
echo "终止: 回到 Termux 按 Ctrl+C"
exec python -m backend.app --host 0.0.0.0 --port 8000
