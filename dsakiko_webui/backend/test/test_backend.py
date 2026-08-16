from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dsakiko_webui.backend.app import create_app
from dsakiko_webui.backend.assets import AssetRegistry, Live2DEntry
from dsakiko_webui.backend.auth import COOKIE_NAME, SingleControllerAuth
from GPT_SoVITS.runtime.runtime_lock import RuntimeLockBusy, acquire_runtime_lock


class FakeRuntime:
    def __init__(self) -> None:
        self.assets = AssetRegistry()
        self.events: queue.Queue[dict] = queue.Queue()
        self.status = "ready"
        self.session_id = "session_test"

    def runtime_status_event(self) -> dict:
        return {
            "type": "runtime_status",
            "chat_id": None,
            "turn_id": None,
            "request_id": None,
            "data": {"state": "ready", "stage": "ready", "message": "就绪", "progress": 1.0},
        }

    def runtime_ready_event(self) -> dict:
        return {
            "type": "runtime_ready",
            "chat_id": None,
            "turn_id": None,
            "request_id": None,
            "data": {"mode": "web", "capabilities": {}},
        }

    def handle_command(self, command_type: str, payload: dict):
        if command_type == "ping":
            return {"accepted": True}, [{
                "type": "pong",
                "chat_id": None,
                "turn_id": None,
                "request_id": None,
                "data": {"client_time": payload.get("client_time"), "server_time": 100},
            }]
        return {"accepted": True}, []


class BackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeRuntime()
        self.auth = SingleControllerAuth("123456")
        self.app = create_app(self.runtime, self.auth, initialize_runtime=False)

    def test_new_login_takes_over_old_cookie(self) -> None:
        with TestClient(self.app) as first, TestClient(self.app) as second:
            first_response = first.post("/api/v1/session", json={"access_code": "123456"})
            old_token = first_response.cookies.get(COOKIE_NAME)
            self.assertTrue(first.get("/api/v1/health").json()["authenticated"])

            second.post("/api/v1/session", json={"access_code": "123456"})
            self.assertIsNotNone(old_token)
            response = first.get("/api/v1/health")
            self.assertFalse(response.json()["authenticated"])
            self.assertTrue(second.get("/api/v1/health").json()["authenticated"])

    def test_wrong_access_code_is_rejected(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/v1/session", json={"access_code": "wrong"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_websocket_ping_uses_protocol_envelope(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/v1/session", json={"access_code": "123456"})
            with client.websocket_connect("/api/v1/ws") as websocket:
                self.assertEqual(websocket.receive_json()["type"], "runtime_status")
                self.assertEqual(websocket.receive_json()["type"], "runtime_ready")
                websocket.send_json({
                    "protocol_version": 1,
                    "kind": "command",
                    "type": "ping",
                    "request_id": "req_ping",
                    "payload": {"client_time": 99},
                })
                response = websocket.receive_json()
                event = websocket.receive_json()
                self.assertTrue(response["ok"])
                self.assertEqual(event["type"], "pong")
                self.assertEqual(event["data"]["client_time"], 99)

    def test_new_login_closes_old_websocket(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/v1/session", json={"access_code": "123456"})
            with client.websocket_connect("/api/v1/ws") as websocket:
                websocket.receive_json()
                websocket.receive_json()
                client.post("/api/v1/session", json={"access_code": "123456"})
                with self.assertRaises(WebSocketDisconnect) as raised:
                    websocket.receive_json()
                self.assertEqual(raised.exception.code, 4409)

    def test_media_requires_login(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/v1/media/not-found")
            self.assertEqual(response.status_code, 401)

    def test_registered_media_supports_range_request(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/v1/session", json={"access_code": "123456"})
            media_url = self.runtime.assets.backgrounds[0]["image_url"]
            response = client.get(media_url, headers={"Range": "bytes=0-9"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(len(response.content), 10)

    def test_media_registry_rejects_files_outside_asset_roots(self) -> None:
        with tempfile.NamedTemporaryFile() as temporary:
            with self.assertRaises(ValueError):
                self.runtime.assets.register_media(temporary.name, "audio")

    def test_live2d_registry_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "model.json").write_text("{}", encoding="utf-8")
            outside = root.parent / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            try:
                self.runtime.assets._models["model_test"] = Live2DEntry(root, "model.json")
                self.assertIsNone(self.runtime.assets.live2d_file("model_test", "../outside.txt"))
                self.assertEqual(
                    self.runtime.assets.live2d_file("model_test", "model.json"),
                    root / "model.json",
                )
            finally:
                outside.unlink(missing_ok=True)

    def test_runtime_lock_rejects_second_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = acquire_runtime_lock(directory, "web")
            try:
                with self.assertRaises(RuntimeLockBusy):
                    acquire_runtime_lock(directory, "desktop")
            finally:
                first.release()


if __name__ == "__main__":
    unittest.main()
