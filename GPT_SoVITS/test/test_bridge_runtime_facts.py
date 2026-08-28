from __future__ import annotations

import asyncio
import os
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


class BridgeRuntimeFactTest(unittest.TestCase):
    def test_audio_server_rejects_path_traversal_and_binds_loopback(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                (root / "ok.wav").write_bytes(b"ok")
                outside = root.parent / "outside-live2d-secret.txt"
                outside.write_text("secret", encoding="utf-8")
                bridge = Bridge(Queue(), audio_base=str(root), audio_port=0)
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


if __name__ == "__main__":
    unittest.main()
