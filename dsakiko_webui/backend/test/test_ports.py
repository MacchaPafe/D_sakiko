from __future__ import annotations

import errno
import io
import http.client
import socket
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from dsakiko_webui.backend.ports import WEBUI_PORT, bind_webui_socket


class WebuiPortTest(unittest.TestCase):
    def test_default_port(self):
        self.assertEqual(WEBUI_PORT, 7799)

    def test_busy_and_reserved_ports_are_skipped(self):
        sockets = [Mock(), Mock(), Mock()]
        sockets[0].bind.side_effect = OSError(errno.EADDRINUSE, "busy")
        sockets[1].bind.side_effect = OSError(errno.EACCES, "reserved")
        with patch("dsakiko_webui.backend.ports.socket.socket", side_effect=sockets):
            self.assertIs(bind_webui_socket(), sockets[2])
        for index, listener in enumerate(sockets):
            listener.bind.assert_called_once_with(("0.0.0.0", 7799 + index))
        sockets[0].close.assert_called_once()
        sockets[1].close.assert_called_once()
        sockets[2].close.assert_not_called()
        sockets[2].listen.assert_called_once()

    def test_unexpected_error_is_not_hidden(self):
        listener = Mock()
        listener.bind.side_effect = OSError(errno.ENETDOWN, "network down")
        with patch("dsakiko_webui.backend.ports.socket.socket", return_value=listener):
            with self.assertRaises(OSError):
                bind_webui_socket()
        listener.close.assert_called_once()

    def test_exhausted_ports_and_invalid_start(self):
        listener = Mock()
        listener.bind.side_effect = OSError(errno.EADDRINUSE, "busy")
        with patch("dsakiko_webui.backend.ports.socket.socket", return_value=listener):
            with self.assertRaisesRegex(OSError, "未找到可用"):
                bind_webui_socket(65535)
        for port in (0, 65536):
            with self.assertRaises(ValueError):
                bind_webui_socket(port)

    def test_real_listener_is_reserved_until_closed(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            if port == 65535:
                self.skipTest("临时端口恰好处于上界")
            with bind_webui_socket(port) as listener:
                self.assertGreater(listener.getsockname()[1], port)
                with socket.socket() as competing:
                    with self.assertRaises(OSError):
                        competing.bind(listener.getsockname())

    def test_banner_uses_selected_port(self):
        from dsakiko_webui.backend.app import print_startup_banner
        with patch("dsakiko_webui.backend.app.discover_network_addresses", return_value=[Mock(address="192.168.1.20")]):
            output = io.StringIO()
            with redirect_stdout(output):
                print_startup_banner("123456", webui_port=7801)
        self.assertIn("http://192.168.1.20:7801", output.getvalue())

    def test_uvicorn_serves_on_reserved_socket(self):
        import uvicorn
        from fastapi import FastAPI
        from dsakiko_webui.backend.main import run_server_until_stopped

        app = FastAPI()

        @app.get("/health")
        def health():
            return {"ok": True}

        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            if port == 65535:
                self.skipTest("临时端口恰好处于上界")
            with bind_webui_socket(port) as listener:
                server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
                thread = threading.Thread(target=run_server_until_stopped, args=(server, listener), daemon=True)
                thread.start()
                connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=3)
                try:
                    deadline = time.monotonic() + 3
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(server.started)
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'{"ok":true}')
                finally:
                    connection.close()
                    server.should_exit = True
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_main_shares_selected_port_and_cleans_up(self):
        from dsakiko_webui.backend import main
        listener = Mock()
        listener.getsockname.return_value = ("0.0.0.0", 7801)
        local_listener = Mock()
        local_listener.getsockname.return_value = ("127.0.0.1", 49123)
        lease = Mock()
        with patch.object(main, "parse_args", return_value=Mock(open_pairing=False)), \
             patch.object(main, "acquire_runtime_lock", return_value=lease), \
             patch.object(main, "bind_webui_socket", return_value=listener), \
             patch.object(main, "bind_loopback_socket", return_value=local_listener), \
             patch.object(main, "PairingPresentation") as presentation, \
             patch.object(main, "create_pairing_ui_app"), \
             patch.object(main, "wait_until_started", return_value=True), \
             patch.object(main.threading, "Thread"), \
             patch.object(main.uvicorn, "Config") as config, \
             patch.object(main.uvicorn, "Server") as server, \
             patch.object(main, "run_server_until_stopped") as run:
            self.assertEqual(main.run(), 0)
            presentation.assert_called_once_with(main.app.state.auth, webui_port=7801)
            self.assertEqual(config.call_args.kwargs["port"], 7801)
            run.assert_called_once_with(server.return_value, listener)
            self.assertEqual(main.app.state.webui_port, 7801)
        listener.close.assert_called_once()
        lease.release.assert_called_once()
        main.app.state.runtime_lease = None
        main.app.state.pairing_ui_url = None
        main.app.state.webui_port = WEBUI_PORT


if __name__ == "__main__":
    unittest.main()
