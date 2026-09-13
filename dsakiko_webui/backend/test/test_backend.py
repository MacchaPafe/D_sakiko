from __future__ import annotations

import base64
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import dsakiko_webui.backend.assets as assets_module
from dsakiko_webui.backend.app import create_app
from dsakiko_webui.backend.assets import AssetRegistry, Live2DEntry
from dsakiko_webui.backend.auth import COOKIE_NAME, AccessController
from dsakiko_webui.backend.runtime import HeadlessRuntime
from dsakiko_webui.backend.uploads import PendingImageStore
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

    def capabilities(self) -> dict[str, bool]:
        return {"image_input": True}

    def settings_snapshot(self) -> dict:
        return {
            "voice": {
                "character_id": "anon",
                "character_name": "爱音",
                "speech_speed": 1.0,
                "sentence_pause_seconds": 0.5,
            },
            "llm": {
                "selected_id": "default_deepseek",
                "options": [{
                    "id": "default_deepseek",
                    "label": "DeepSeek 公共 API",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                }],
            },
            "capabilities": self.capabilities(),
        }

    def update_settings(self, **values) -> dict:
        result = self.settings_snapshot()
        if values.get("speech_speed") is not None:
            result["voice"]["speech_speed"] = values["speech_speed"]
        if values.get("sentence_pause_seconds") is not None:
            result["voice"]["sentence_pause_seconds"] = values["sentence_pause_seconds"]
        return result

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


class FakeChat:
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.message_list = []
        self.meta = SimpleNamespace(extra={})

    def add_message(self, message) -> None:
        self.message_list.append(message)

    def delete_message_at(self, index: int) -> None:
        self.message_list.pop(index)


class BackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeRuntime()
        self.auth = AccessController("123456")
        self.app = create_app(self.runtime, self.auth, initialize_runtime=False)

    def tearDown(self) -> None:
        self.app.state.uploads.close()

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

    def test_pairing_redeem_sets_cookie_and_is_idempotent(self) -> None:
        """配对兑换应签发 Cookie，并允许同一客户端短时重试。"""
        grant = self.auth.regenerate_pairing()
        with TestClient(self.app) as client:
            first = client.post("/api/v1/pairing/redeem", json={
                "pairing_token": grant.token,
                "session_id": "phone-one",
            })
            retry = client.post("/api/v1/pairing/redeem", json={
                "pairing_token": grant.token,
                "session_id": "phone-one",
            })
            self.assertEqual(first.status_code, 200)
            self.assertEqual(retry.status_code, 200)
            self.assertTrue(client.get("/api/v1/health").json()["authenticated"])

    def test_pairing_redeem_hides_specific_failure_reason(self) -> None:
        """无效配对凭证应返回统一错误。"""
        with TestClient(self.app) as client:
            response = client.post("/api/v1/pairing/redeem", json={
                "pairing_token": "x" * 43,
                "session_id": "phone-one",
            })
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "PAIRING_INVALID")

    def test_access_code_rate_limit_returns_retry_metadata(self) -> None:
        """六位码入口被限速时应同时返回响应头和结构化等待秒数。"""
        with TestClient(self.app) as client:
            for _ in range(5):
                self.assertEqual(
                    client.post("/api/v1/session", json={"access_code": "000000"}).status_code,
                    401,
                )
            response = client.post("/api/v1/session", json={"access_code": "000000"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()["error"]["code"], "AUTH_RATE_LIMITED")
            self.assertEqual(
                int(response.headers["Retry-After"]),
                response.json()["error"]["details"]["retry_after_seconds"],
            )

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

    def test_image_upload_requires_login_and_accepts_valid_png(self) -> None:
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with TestClient(self.app) as client:
            unauthorized = client.post(
                "/api/v1/uploads/images",
                files={"file": ("photo.png", png, "image/png")},
            )
            self.assertEqual(unauthorized.status_code, 401)

            client.post("/api/v1/session", json={"access_code": "123456"})
            response = client.post(
                "/api/v1/uploads/images",
                files={"file": ("photo.png", png, "image/png")},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["upload_id"].startswith("upload_"))
            self.assertEqual(response.json()["mime_type"], "image/png")

            deleted = client.delete(f"/api/v1/uploads/images/{response.json()['upload_id']}")
            self.assertEqual(deleted.status_code, 204)

    def test_settings_require_login_and_update_runtime_values(self) -> None:
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/api/v1/settings").status_code, 401)
            client.post("/api/v1/session", json={"access_code": "123456"})

            snapshot = client.get("/api/v1/settings")
            self.assertEqual(snapshot.status_code, 200)
            self.assertEqual(snapshot.json()["voice"]["character_name"], "爱音")
            self.assertNotIn("api_key", snapshot.text.lower())

            updated = client.patch("/api/v1/settings", json={
                "speech_speed": 1.12,
                "sentence_pause_seconds": 0.36,
                "llm_choice_id": "default_deepseek",
            })
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["voice"]["speech_speed"], 1.12)

    def test_settings_reject_out_of_range_voice_values(self) -> None:
        with TestClient(self.app) as client:
            client.post("/api/v1/session", json={"access_code": "123456"})
            response = client.patch("/api/v1/settings", json={"speech_speed": 2.0})
            self.assertEqual(response.status_code, 422)

    def test_image_upload_rejects_model_without_vision_support(self) -> None:
        self.runtime.capabilities = lambda: {"image_input": False}
        with TestClient(self.app) as client:
            client.post("/api/v1/session", json={"access_code": "123456"})
            response = client.post(
                "/api/v1/uploads/images",
                files={"file": ("photo.png", b"not-read", "image/png")},
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "IMAGE_INPUT_UNSUPPORTED")

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

    def test_character_avatar_uses_live2d_root_then_folder_name_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live2d_root = root / "live2d_related"
            headprof_root = root / "char_headprof"
            (live2d_root / "sakiko").mkdir(parents=True)
            (live2d_root / "anon").mkdir(parents=True)
            headprof_root.mkdir()
            sakiko_icon = live2d_root / "sakiko" / "sakiko_icon.png"
            anon_icon = headprof_root / "爱音.png"
            sakiko_icon.write_bytes(b"sakiko")
            anon_icon.write_bytes(b"anon")

            with (
                patch.object(assets_module, "LIVE2D_ROOT", live2d_root.resolve()),
                patch.object(assets_module, "CHAR_HEADPROF_ROOT", headprof_root.resolve()),
            ):
                registry = AssetRegistry()
                sakiko = registry.register_character(SimpleNamespace(
                    character_folder_name="sakiko",
                    character_name="祥子",
                    live2d_json=None,
                ))
                anon = registry.register_character(SimpleNamespace(
                    character_folder_name="anon",
                    character_name="爱音",
                    live2d_json=None,
                ))

                sakiko_media = registry.media(sakiko["avatar_url"].rsplit("/", 1)[-1])
                anon_media = registry.media(anon["avatar_url"].rsplit("/", 1)[-1])
                self.assertEqual(sakiko_media.path, sakiko_icon.resolve())
                self.assertEqual(anon_media.path, anon_icon.resolve())

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

    def test_character_entity_does_not_choose_a_renderer_specific_live2d_model(self) -> None:
        """角色静态实体不应把 v3 配置悄然改写为 v2 模型。"""
        with tempfile.TemporaryDirectory() as directory:
            live2d_root = (Path(directory) / "live2d_related").resolve()
            model3_dir = live2d_root / "anon" / "live2D_model"
            model2_dir = live2d_root / "anon" / "live2D_model_v2_ignore_this"
            model3_dir.mkdir(parents=True)
            model2_dir.mkdir(parents=True)
            model3_path = model3_dir / "anon.model3.json"
            model2_path = model2_dir / "anon.model.json"
            model3_path.write_text("{}", encoding="utf-8")
            model2_path.write_text("{}", encoding="utf-8")

            with patch.object(assets_module, "LIVE2D_ROOT", live2d_root):
                registry = AssetRegistry()
                character = registry.register_character(SimpleNamespace(
                    character_folder_name="anon",
                    character_name="爱音",
                    live2d_json=str(model3_path),
                ))

                self.assertNotIn("model_url", character)
                self.assertEqual(registry._models, {})

    def test_runtime_lock_rejects_second_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = acquire_runtime_lock(directory, "web")
            try:
                with self.assertRaises(RuntimeLockBusy):
                    acquire_runtime_lock(directory, "desktop")
            finally:
                first.release()

    def test_runtime_send_imports_uploaded_image_with_desktop_attachment_logic(self) -> None:
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        uploads = PendingImageStore()
        runtime = HeadlessRuntime(AssetRegistry(), uploads)
        chat = FakeChat("webui_upload_test")
        runtime.dp_chat = SimpleNamespace(
            current_chat_id=chat.chat_id,
            current_chat=chat,
            _current_model_supports_vision=lambda: True,
        )
        saved_meta = []
        runtime.chat_manager = SimpleNamespace(
            save=lambda: saved_meta.append(list(chat.meta.extra.get("webui_messages", []))),
        )
        runtime.command_queue = queue.Queue()
        runtime.chat_list_snapshot = lambda: {"chats": []}
        runtime.status = "ready"
        item = uploads.add(png, "camera.png")

        try:
            result, events = runtime._send_message({
                "chat_id": chat.chat_id,
                "client_message_id": "client_image",
                "text": "看看这张图",
                "image_upload_ids": [item.upload_id],
            })
            self.assertFalse(result["deduplicated"])
            self.assertEqual(len(chat.message_list[0].attachments), 1)
            self.assertEqual(chat.message_list[0].attachments[0].mime_type, "image/png")
            self.assertEqual(events[0]["data"]["message"]["attachments"][0]["type"], "image")
            self.assertEqual(saved_meta[-1][0]["client_message_id"], "client_image")
            command = runtime.command_queue.get_nowait()
            self.assertFalse(command["append_user_message"])
        finally:
            from chat.attachments import delete_chat_attachment_dir

            delete_chat_attachment_dir(chat.chat_id)
            uploads.close()

    def test_runtime_rolls_back_persisted_user_message_after_llm_failure(self) -> None:
        from chat.chat import Message
        from emotion_enum import EmotionEnum

        runtime = HeadlessRuntime(AssetRegistry(), PendingImageStore())
        chat = FakeChat("webui_failed_turn")
        chat.add_message(Message("User", "失败消息", "", EmotionEnum.HAPPINESS, ""))
        chat.meta.extra["webui_messages"] = [{
            "id": "msg_failed",
            "created_at": 1,
            "turn_id": "turn_failed",
            "client_message_id": "client_failed",
            "sequence": 0,
            "role": "user",
        }]
        saves = []
        runtime.chat_manager = SimpleNamespace(
            get_chat_by_id=lambda chat_id: chat if chat_id == chat.chat_id else None,
            save=lambda: saves.append(True),
        )
        runtime._client_message_turns[(chat.chat_id, "client_failed")] = "turn_failed"

        try:
            runtime._rollback_failed_user_message(chat.chat_id, "turn_failed")
            self.assertEqual(chat.message_list, [])
            self.assertEqual(chat.meta.extra["webui_messages"], [])
            self.assertNotIn((chat.chat_id, "client_failed"), runtime._client_message_turns)
            self.assertTrue(saves)
        finally:
            runtime.uploads.close()


if __name__ == "__main__":
    unittest.main()
