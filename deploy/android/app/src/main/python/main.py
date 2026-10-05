"""Android Chaquopy 入口：MainActivity 在后台线程调用 start_server(base_dir, frontend_dir)。

关键约束（跨平台坑）：uvicorn 的 asyncio 事件循环必须运行在 Python 主线程
（= 调用 Python.start 的线程，即 Kotlin 的 py-server 线程）。若另开 threading.Thread
跑 server.run，Android 上 SelectorEventLoop 的非主线程事件循环会触发
`signal.set_wakeup_fd only works in main thread`（Windows 用 ProactorEventLoop
无此限制，故本机冒烟测试未暴露）。因此本函数在当前线程阻塞运行 server.run。

端口用固定值 8123（避开 Termux 版 8000；自闭环 App 独立进程，无外部冲突）。
WebView 加载 http://127.0.0.1:8123/，前端 fetch 相对路径天然同源，无需注入 API_BASE。
"""
import pathlib

_server = None
_PORT = 8123


def start_server(base_dir: str, frontend_dir: str) -> None:
    """当前线程（Python 主线程）阻塞运行 uvicorn，直到进程结束。"""
    global _server
    if _server is not None and _server.started:  # 幂等：Activity 重建时服务已在跑
        return
    from backend.app import build_server  # APK 内 sys.path 含 backend 包（python.srcDirs）

    base = pathlib.Path(base_dir)
    _server = build_server(
        host="127.0.0.1",
        port=_PORT,
        db_path=str(base / "app.db"),
        static_dir=frontend_dir,
        log_dir=str(base / "logs"),
    )
    _server.run()  # 阻塞：必须在 Python 主线程（本线程）运行
