from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from queue import Queue

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root not in sys.path:
    sys.path.insert(0, root)
gpt_root = os.path.join(root, "GPT_SoVITS")
if gpt_root not in sys.path:
    sys.path.insert(0, gpt_root)

from bridge.saki_bridge import Bridge
from bridge.ws_server import WSServer


class BridgeRuntimeFactTest(unittest.TestCase):
    def test_ws_rejects_bad_token_and_origin_before_upgrade(self):
        class Reader:
            def __init__(self, request): self.request = request.encode(); self.used = False
            async def read(self, _size):
                if self.used: return b""
                self.used = True
                return self.request
        class Writer:
            def __init__(self): self.closed = False; self.writes = []
            def write(self, data): self.writes.append(data)
            async def drain(self): pass
            def close(self): self.closed = True
        async def exercise():
            server = WSServer(auth_token="expected")
            bad_token = Writer()
            await server._handle_client(Reader(
                "GET /?token=wrong HTTP/1.1\r\nSec-WebSocket-Key: key\r\n\r\n"
            ), bad_token)
            self.assertTrue(bad_token.closed)
            bad_origin = Writer()
            await server._handle_client(Reader(
                "GET /?token=expected HTTP/1.1\r\nOrigin: http://evil\r\nSec-WebSocket-Key: key\r\n\r\n"
            ), bad_origin)
            self.assertTrue(bad_origin.closed)
        asyncio.run(exercise())

    def test_ws_binds_renderer_identity_for_lifecycle_messages(self):
        def frame(payload):
            body = payload.encode()
            mask = b"abcd"
            encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
            return bytes([0x81, 0x80 | len(encoded)]) + mask + encoded
        hello = json.dumps({"type": "renderer_hello", "data": {
            "renderer_id": "electron", "renderer_instance_id": "instance-1",
        }})
        fact = json.dumps({"type": "motion_started", "data": {
            "renderer_id": "electron", "renderer_instance_id": "instance-1", "token": "t",
        }})
        class Reader:
            def __init__(self, request, frames): self.request = request.encode(); self.frames = frames; self.used = False
            async def read(self, _size):
                if self.used: return b""
                self.used = True
                return self.request
            async def readexactly(self, size):
                if not self.frames: raise asyncio.IncompleteReadError(b"", size)
                data, self.frames = self.frames[:size], self.frames[size:]
                if len(data) != size: raise asyncio.IncompleteReadError(data, size)
                return data
        class Writer:
            def __init__(self): self.closed = False; self.writes = []
            def write(self, data): self.writes.append(data)
            async def drain(self): pass
            def close(self): self.closed = True
        async def exercise():
            seen = []
            server = WSServer(auth_token="expected", on_message=lambda message, writer: seen.append(message))
            request = "GET /?token=expected HTTP/1.1\r\nOrigin: file://\r\nSec-WebSocket-Key: key\r\n\r\n"
            reader = Reader(request, frame(hello) + frame(fact))
            await server._handle_client(reader, Writer())
            self.assertEqual([message["type"] for message in seen], ["renderer_hello", "motion_started"])
        asyncio.run(exercise())

    def test_audio_server_rejects_path_traversal_and_binds_loopback(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                (root / "ok.wav").write_bytes(b"ok")
                (root / "project-secret.txt").write_text("secret", encoding="utf-8")
                model_root = root / "models"
                model_root.mkdir()
                (model_root / "model.json").write_text("{}", encoding="utf-8")
                outside = root.parent / "outside-live2d-secret.txt"
                outside.write_text("secret", encoding="utf-8")
                bridge = Bridge(Queue(), audio_base=str(root), model_base=str(model_root), audio_root=str(root), audio_port=0)
                await bridge._start_audio_server()
                self.assertIsNotNone(bridge._audio_server)
                sockets = bridge._audio_server.sockets
                self.assertEqual(sockets[0].getsockname()[0], "127.0.0.1")
                port = sockets[0].getsockname()[1]

                async def request(path):
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.write(f"GET {path} HTTP/1.0\\r\\n\\r\\n".encode())
                    await writer.drain()
                    response = await reader.read()
                    writer.close()
                    await writer.wait_closed()
                    return response

                self.assertTrue((await request("/audio/ok.wav")).startswith(b"HTTP/1.0 200"))
                self.assertTrue((await request("/model/model.json")).startswith(b"HTTP/1.0 200"))
                self.assertTrue((await request("/project-secret.txt")).startswith(b"HTTP/1.0 404"))
                self.assertTrue((await request("/audio/../outside-live2d-secret.txt")).startswith(b"HTTP/1.0 404"))
                bridge._audio_server.close()
                await bridge._audio_server.wait_closed()

        asyncio.run(exercise())

    def test_model_switch_is_never_replayed_by_bridge_snapshot(self):
        bridge = Bridge(Queue())
        for targets in (["pygame-renderer"], ["electron-one"], None):
            data = {"model_url": "model.json"}
            if targets is not None:
                data["target_renderer_ids"] = targets
            bridge._cache_command({"v": 2, "type": "switch_live2d", "data": data})
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append((writer, message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent, [])

    def test_late_renderer_receives_untargeted_thinking_snapshot(self):
        bridge = Bridge(Queue())
        bridge._cache_command({
            "v": 2,
            "type": "thinking_changed",
            "data": {"active": True, "target_renderer_ids": ["electron-one"]},
        })
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append((writer, message_type, data))

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent[0][0:2], ("electron-ws", "renderer_snapshot"))
        self.assertEqual(sent[0][2]["commands"][0]["data"], {"active": True})

    def test_snapshot_does_not_cache_motion_without_owner_lifecycle_fact(self):
        bridge = Bridge(Queue())
        bridge._cache_command({"type": "play_motion", "data": {"token": "stale"}})
        sent = []

        class SnapshotWS:
            async def send_to(self, writer, message_type, data):
                sent.append(data)

        bridge.ws = SnapshotWS()
        asyncio.run(bridge._on_renderer_connect("electron-ws"))
        self.assertEqual(sent, [])

    def test_renderer_fact_is_forwarded_without_controller_specific_filter(self):
        facts = Queue()
        bridge = Bridge(Queue(), renderer_fact_queue=facts)
        asyncio.run(bridge._on_renderer_message({"v": 2, "type": "command_failed", "data": {"token": "cmd", "phase": "motion_start"}}))
        self.assertEqual(facts.get_nowait()["type"], "command_failed")

    def test_renderer_command_queue_is_kept_separate_from_legacy_bridge_events(self):
        commands = Queue()
        bridge = Bridge(Queue(), renderer_command_queue=commands)
        self.assertIs(bridge.renderer_command_queue, commands)

    def test_renderer_command_paths_are_adapted_at_bridge_edge(self):
        bridge = Bridge(Queue(), audio_base="C:/app", audio_host="127.0.0.1", audio_port=9877)
        command = {
            "type": "switch_live2d",
            "data": {"model_url": "C:/app/live2d_related/anon/live2D_model/3.model.json"},
        }
        adapted = bridge._adapt_command_for_electron(command)
        self.assertEqual(
            adapted["data"]["model_url"],
            "http://127.0.0.1:9877/model/anon/live2D_model/3.model.json",
        )
        self.assertEqual(command["data"]["model_url"], "C:/app/live2d_related/anon/live2D_model/3.model.json")

    def test_legacy_pygame_motion_queue_cannot_create_an_electron_executor(self):
        bridge = Bridge(Queue(), motion_queue=Queue())
        self.assertFalse(hasattr(bridge, "_motion_reader_thread"))
        self.assertFalse(hasattr(bridge, "_motion_reader"))

    def test_renderer_disconnect_fact_is_emitted_for_hello_writer(self):
        facts = Queue()
        bridge = Bridge(Queue(), renderer_fact_queue=facts)
        writer = object()
        asyncio.run(bridge._on_renderer_message({
            "type": "renderer_hello",
            "data": {"renderer_id": "electron", "renderer_instance_id": "instance-1"},
        }, writer))
        self.assertEqual(facts.get_nowait()["type"], "renderer_hello")
        asyncio.run(bridge._on_renderer_disconnect(writer))
        disconnected = facts.get_nowait()
        self.assertEqual(disconnected["type"], "renderer_disconnected")
        self.assertEqual(disconnected["data"], {"renderer_id": "electron", "renderer_instance_id": "instance-1"})

    def test_bridge_uses_per_launch_token_and_ws_validation_policy(self):
        first = Bridge(Queue())
        second = Bridge(Queue())
        self.assertTrue(first.auth_token)
        self.assertNotEqual(first.auth_token, second.auth_token)
        self.assertIn("file://", first.ws.allowed_origins)
        self.assertLessEqual(first.ws.max_message_size, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
