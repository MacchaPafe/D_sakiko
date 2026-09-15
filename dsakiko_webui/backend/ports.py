"""为 WebUI 预留可用端口，避免探测后被其他进程抢占。"""
from __future__ import annotations

import errno
import socket

WEBUI_PORT = 7799


def bind_webui_socket(start_port: int = WEBUI_PORT) -> socket.socket:
    """从起始端口递增绑定；返回的监听器交由 Uvicorn 使用。"""
    if not 1 <= start_port <= 65535:
        raise ValueError("起始端口必须在 1–65535 之间")
    for port in range(start_port, 65536):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows 不允许用 SO_REUSEADDR 抢占已有监听器。
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("0.0.0.0", port))
            listener.listen(2048)
            return listener
        except OSError as exc:
            listener.close()
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES) and getattr(exc, "winerror", None) not in (10048, 10013):
                raise
    raise OSError(f"未找到可用的 WebUI 端口（{start_port}–65535）")
