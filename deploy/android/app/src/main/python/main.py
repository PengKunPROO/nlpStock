"""Android Chaquopy 入口：MainActivity 在后台线程调用 start_server(base_dir, frontend_dir) -> port。

设计要点：
- 幂等：Activity 重建时若服务已在跑，直接返回既有端口（main.py 与 uvicorn 同进程存活）。
- 端口由 OS 分配（port=0），避免与手机上其他 127.0.0.1 服务（如 Termux 版）冲突。
- WebView 加载 http://127.0.0.1:<port>/，前端 fetch 相对路径天然同源，无需注入 API_BASE。
"""
import pathlib
import threading
import time

_server = None
_port = 0


def start_server(base_dir: str, frontend_dir: str) -> int:
    """启动内嵌后端（daemon 线程），阻塞至就绪，返回监听端口。"""
    global _server, _port
    if _server is not None and _server.started:
        return _port
    from backend.app import build_server  # APK 内 sys.path 含 backend 包（python.srcDirs）

    base = pathlib.Path(base_dir)
    _server = build_server(
        host="127.0.0.1",
        port=0,  # OS 分配空闲端口
        db_path=str(base / "app.db"),
        static_dir=frontend_dir,
        log_dir=str(base / "logs"),
    )
    threading.Thread(target=_server.run, name="uvicorn", daemon=True).start()
    for _ in range(150):  # 等待就绪（首次含 SQLite/日志初始化），最长 15s
        if _server.started and _server.servers:
            break
        time.sleep(0.1)
    _port = _server.servers[0].sockets[0].getsockname()[1]
    return _port
