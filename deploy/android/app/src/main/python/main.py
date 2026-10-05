"""Android Chaquopy 入口：MainActivity 在后台线程调用 start_server(base_dir, frontend_dir)。

关键约束（跨平台坑）：uvicorn 的 asyncio 事件循环必须运行在 Python 主线程
（= 调用 Python.start 的线程，即 Kotlin 的 py-server 线程）。若另开 threading.Thread
跑 server.run，Android 上 SelectorEventLoop 的非主线程事件循环会触发
`signal.set_wakeup_fd only works in main thread`（Windows 用 ProactorEventLoop
无此限制，故本机冒烟测试未暴露）。因此本函数在当前线程阻塞运行 server.run。

端口用固定值 8123（避开 Termux 版 8000；自闭环 App 独立进程，无外部冲突）。

关键（Android loopback 双栈坑）：Java HttpURLConnection 解析 loopback 偏 IPv4
（127.0.0.1），Chromium/WebView 偏 IPv6（::1）。若 uvicorn 只绑单一地址，必有一方
ERR_CONNECTION_REFUSED。故显式创建 IPv4 127.0.0.1 + IPv6 ::1 两个 socket 传给 uvicorn，
仅监听 loopback（不暴露局域网），Java 用 127.0.0.1、WebView 用 localhost 各自可达。
"""
import pathlib
import socket

_server = None
_PORT = 8123


def _loopback_sockets(port: int) -> list:
    """同时监听 IPv4 127.0.0.1 与 IPv6 ::1（Android 上 Java/Chromium 解析偏好不同）。"""
    socks = []
    for family, addr in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind(addr)
        socks.append(s)
    return socks


def start_server(base_dir: str, frontend_dir: str) -> None:
    """当前线程（Python 主线程）阻塞运行 uvicorn，直到进程结束。"""
    global _server
    if _server is not None and _server.started:  # 幂等：Activity 重建时服务已在跑
        return
    from backend.app import build_server  # APK 内 sys.path 含 backend 包（python.srcDirs）

    base = pathlib.Path(base_dir)
    _server = build_server(
        host="127.0.0.1",  # 占位（实际监听由下面的双 socket 决定）
        port=_PORT,
        db_path=str(base / "app.db"),
        static_dir=frontend_dir,
        log_dir=str(base / "logs"),
    )
    _server.run(sockets=_loopback_sockets(_PORT))  # 双栈 loopback：阻塞于 Python 主线程
