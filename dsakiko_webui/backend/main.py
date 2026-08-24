from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from .app import WEBUI_PORT, create_app
from .assets import PROJECT_ROOT
from .pairing_ui import (
    PairingPresentation,
    PairingUiLocation,
    create_pairing_ui_app,
    generate_ui_nonce,
)
from GPT_SoVITS.runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock


app = create_app()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """解析 WebUI 启动参数。"""
    parser = argparse.ArgumentParser(description="启动数字小祥 WebUI")
    parser.add_argument(
        "--open-pairing",
        action="store_true",
        help="主服务可用后自动打开仅本机可访问的配对页面",
    )
    return parser.parse_args()


def bind_loopback_socket() -> socket.socket:
    """预绑定随机 loopback 端口，避免固定端口冲突。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    return listener


def wait_until_started(server: uvicorn.Server, thread: threading.Thread) -> bool:
    """等待 Uvicorn 宣告监听完成，线程提前退出时立即失败。"""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if server.started:
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.02)
    return False


def open_pairing_when_ready(main_server: uvicorn.Server, url: str) -> None:
    """等待主监听器可用后尝试打开本机配对页面。"""
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and not main_server.should_exit:
        if main_server.started:
            if not webbrowser.open(url):
                logger.warning("无法自动打开本机配对页，请手动访问：%s", url)
            return
        time.sleep(0.05)


def run_server_until_stopped(server: uvicorn.Server) -> None:
    """运行主服务器，并吞掉优雅关闭后重新抛出的预期中断。"""
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("数字小祥 WebUI 已停止")


def run() -> int:
    """协调主 WebUI、loopback 配对页、浏览器与退出清理。"""
    args = parse_args()
    try:
        app.state.runtime_lease = acquire_runtime_lock(PROJECT_ROOT, "web")
    except RuntimeLockBusy as exc:
        print(str(exc))
        return 1

    local_socket: socket.socket | None = None
    local_server: uvicorn.Server | None = None
    local_thread: threading.Thread | None = None
    try:
        try:
            local_socket = bind_loopback_socket()
            local_port = int(local_socket.getsockname()[1])
            location = PairingUiLocation(local_port, generate_ui_nonce())
            presentation = PairingPresentation(app.state.auth, webui_port=WEBUI_PORT)
            local_config = uvicorn.Config(
                create_pairing_ui_app(presentation, location),
                log_level="warning",
                access_log=False,
            )
            local_server = uvicorn.Server(local_config)
            local_thread = threading.Thread(
                target=local_server.run,
                kwargs={"sockets": [local_socket]},
                name="dsakiko-pairing-ui",
                daemon=True,
            )
            local_thread.start()
            if wait_until_started(local_server, local_thread):
                app.state.pairing_ui_url = location.url
            else:
                logger.warning("本机配对展示服务器未能启动，将使用六位码备用流程")
                local_server.should_exit = True
                local_server = None
        except OSError:
            logger.exception("本机配对展示服务器启动失败，将使用六位码备用流程")

        main_config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=WEBUI_PORT,
            reload=False,
        )
        main_server = uvicorn.Server(main_config)
        if args.open_pairing and app.state.pairing_ui_url:
            threading.Thread(
                target=open_pairing_when_ready,
                args=(main_server, app.state.pairing_ui_url),
                name="dsakiko-pairing-browser",
                daemon=True,
            ).start()
        run_server_until_stopped(main_server)
        return 0
    finally:
        if local_server is not None:
            local_server.should_exit = True
        if local_thread is not None:
            local_thread.join(timeout=5.0)
        if local_socket is not None:
            local_socket.close()
        if app.state.runtime_lease is not None:
            app.state.runtime_lease.release()


if __name__ == "__main__":
    raise SystemExit(run())
