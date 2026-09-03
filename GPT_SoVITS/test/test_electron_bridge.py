from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import time
import json
from pathlib import Path
from queue import Queue

import pytest

from bridge.electron_bridge import ElectronBridge
from bridge.electron_ws import BRIDGE_PROTOCOL, ElectronWSServer


ROOT = Path(__file__).resolve().parents[2]


def test_bridge_resource_allowlist_and_authenticated_urls(tmp_path: Path) -> None:
    model = tmp_path / "live2d_related" / "sakiko" / "3.model.json"
    audio = tmp_path / "reference_audio" / "generated.wav"
    secret = tmp_path / "secret.txt"
    model.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    model.write_text("{}", encoding="utf-8")
    audio.write_bytes(b"RIFF")
    secret.write_text("private", encoding="utf-8")

    bridge = ElectronBridge(Queue(), tmp_path)
    assert bridge._resolve_url_path("/model/sakiko/3.model.json") == model.resolve()
    assert bridge._resolve_url_path("/audio/reference_audio/generated.wav") == audio.resolve()
    assert bridge._resolve_url_path("/file/secret.txt") is None
    assert bridge._resolve_url_path("/model/../secret.txt") is None
    assert bridge._resolve_url_path("/audio/../secret.txt") is None
    assert "token=" in bridge.url_for_path(model, "model")
    assert "token=" in bridge.url_for_path(audio, "audio")
    assert bridge.url_for_path(secret, "audio") == ""


def test_bridge_recovers_latest_backend_selection_not_startup_model(tmp_path: Path) -> None:
    bridge = ElectronBridge(Queue(), tmp_path)
    bridge.publish("initial_model", {"model_url": "http://old", "sakiko_state": "white"})
    bridge.publish("sakiko_state", {
        "value": "black", "sakiko_state": "black", "model_url": "http://current",
        "presentation_base": "serious", "character_folder": "sakiko",
    })
    model = bridge._latest_selection["model"]
    assert model["type"] == "initial_model"
    assert model["data"]["model_url"] == "http://current"
    assert model["data"]["sakiko_state"] == "black"
    assert model["data"]["presentation_base"] == "serious"


def test_bridge_hello_sends_one_current_selection_snapshot(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, _writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        bridge.publish("initial_model", {"model_url": "http://old", "sakiko_state": "white"})
        bridge.publish("sakiko_state", {
            "value": "black", "sakiko_state": "black", "model_url": "http://current",
            "presentation_base": "serious",
        })
        bridge.publish("sakiko_state", {"value": "maskoff"})
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, object())
        await asyncio.sleep(0.01)
        assert fake_ws.sent[0] == (
            "bridge_ready",
            {"authenticated": True, "protocol": BRIDGE_PROTOCOL, "instance_id": bridge.instance_id},
        )
        assert fake_ws.sent[1] == (
            "initial_model",
            {
                "model_url": "http://current", "value": "black", "sakiko_state": "black",
                "presentation_base": "serious",
            },
        )

    asyncio.run(scenario())


def test_bridge_refresh_cancels_an_active_turn_without_replaying_segments(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, _writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        first_writer = object()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, first_writer)
        await asyncio.sleep(0.01)
        assert not any(message_type == "renderer_recovery" for message_type, _ in fake_ws.sent)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat", "turn_id": "turn"})
        bridge._handle_renderer_disconnect(first_writer)
        fake_ws.sent.clear()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, object())
        await asyncio.sleep(0.01)
        assert ("renderer_recovery", {"action": "cancel", "chat_id": "chat", "turn_id": "turn"}) in fake_ws.sent
        assert bridge.intent_queue.get_nowait() == {
            "intent": "recover_renderer", "data": {"chat_id": "chat", "turn_id": "turn"},
        }
        assert bridge._active_turn is None

    asyncio.run(scenario())


def test_bridge_ui_intents_require_hello_ready_socket(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, _writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        writer = object()
        bridge._handle_client_message(
            {"type": "ui_intent", "data": {"intent": "start_voice_input"}}, writer,
        )
        assert bridge.intent_queue.empty()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, writer)
        await asyncio.sleep(0.01)
        bridge._handle_client_message(
            {"type": "ui_intent", "data": {"intent": "start_voice_input"}}, writer,
        )
        assert bridge.intent_queue.get_nowait() == {
            "intent": "start_voice_input", "data": {"intent": "start_voice_input"},
        }

    asyncio.run(scenario())


def test_bridge_reconnect_after_completed_turn_only_cancels_renderer_locally(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, _writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        first_writer = object()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, first_writer)
        await asyncio.sleep(0.01)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat-a", "turn_id": "turn-a"})
        bridge._handle_renderer_disconnect(first_writer)
        bridge.publish("assistant_turn_complete", {"chat_id": "chat-a", "turn_id": "turn-a"})
        fake_ws.sent.clear()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, object())
        await asyncio.sleep(0.01)
        assert ("renderer_recovery", {"action": "cancel", "chat_id": "chat-a", "turn_id": "turn-a"}) in fake_ws.sent
        assert bridge.intent_queue.empty()
        assert bridge._active_turn is None

    asyncio.run(scenario())


def test_bridge_stale_disconnect_snapshot_never_cancels_a_newer_turn(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, _writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        first_writer = object()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, first_writer)
        await asyncio.sleep(0.01)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat-a", "turn_id": "turn-a"})
        bridge._handle_renderer_disconnect(first_writer)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat-b", "turn_id": "turn-b"})
        # A late terminal event for the disconnected turn must only mark its
        # own recovery snapshot complete; it cannot clear the newer B turn.
        bridge.publish("assistant_turn_complete", {"chat_id": "chat-a", "turn_id": "turn-a"})
        fake_ws.sent.clear()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, object())
        await asyncio.sleep(0.01)
        assert ("renderer_recovery", {"action": "cancel", "chat_id": "chat-a", "turn_id": "turn-a"}) in fake_ws.sent
        assert bridge.intent_queue.get_nowait() == {
            "intent": "recover_renderer", "data": {"chat_id": "chat-b", "turn_id": "turn-b"},
        }
        assert bridge._active_turn == {"chat_id": "chat-b", "turn_id": "turn-b"}

    asyncio.run(scenario())


def test_bridge_disconnect_does_not_track_renderer_registry(tmp_path: Path) -> None:
    class FakeWS:
        async def send(self, _writer: object, _message_type: str, _data: dict[str, object]) -> None:
            return None

        def mark_ready(self, _writer: object) -> None:
            return None

        def revoke_ready(self, _writer: object) -> None:
            return None

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = FakeWS()  # type: ignore[assignment]
        old_writer = object()
        new_writer = object()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, old_writer)
        await asyncio.sleep(0.01)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat-a", "turn_id": "turn-a"})
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, new_writer)
        await asyncio.sleep(0.01)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat-b", "turn_id": "turn-b"})
        bridge._handle_renderer_disconnect(old_writer)
        assert bridge._renderer_writer is new_writer
        bridge._handle_renderer_disconnect(new_writer)
        assert bridge._disconnected_turn == {
            "chat_id": "chat-b", "turn_id": "turn-b",
        }

    asyncio.run(scenario())


def test_session_descriptor_appears_only_after_both_listeners_bind(tmp_path: Path) -> None:
    async def scenario() -> None:
        session_file = tmp_path / ".electron-bridge-session.json"
        bridge = ElectronBridge(Queue(), tmp_path, ws_port=0, http_port=0, session_file=session_file)
        # Calling the bind methods directly keeps the test deterministic while
        # proving descriptor construction uses the bound rather than requested
        # ephemeral ports.
        bridge._ws = ElectronWSServer("127.0.0.1", 0, session_token=bridge.session_token)
        await bridge._ws.start()
        assert not session_file.exists()
        await bridge._start_http()
        bridge._write_session_descriptor()
        descriptor = json.loads(session_file.read_text(encoding="utf-8"))
        assert descriptor["instance_id"] == bridge.instance_id
        assert ":0/" not in descriptor["ws_url"]
        try:
            assert bridge._http is not None
        finally:
            bridge._http.close()  # type: ignore[union-attr]
            await bridge._http.wait_closed()  # type: ignore[union-attr]
            await bridge._ws.stop()

    asyncio.run(scenario())


def test_launcher_readiness_probe_requires_authenticated_bridge_instance(tmp_path: Path) -> None:
    session_file = tmp_path / ".electron-bridge-session.json"
    bridge = ElectronBridge(Queue(), tmp_path, ws_port=0, http_port=0, session_file=session_file)
    bridge.start()
    try:
        deadline = time.monotonic() + 2
        while not session_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session_file.exists(), "descriptor must appear only after bridge listeners bind"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "probe_electron_bridge.py"),
                "--session",
                str(session_file),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        descriptor = json.loads(session_file.read_text(encoding="utf-8"))
        descriptor["instance_id"] = "wrong-instance"
        session_file.write_text(json.dumps(descriptor), encoding="utf-8")
        rejected = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "probe_electron_bridge.py"),
                "--session",
                str(session_file),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert rejected.returncode != 0
    finally:
        bridge.shutdown()


def test_readiness_probe_never_claims_or_disturbs_a_ready_renderer(tmp_path: Path) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[tuple[object, str, dict[str, object]]] = []
            self.ready: set[object] = set()

        async def send(self, writer: object, message_type: str, data: dict[str, object]) -> None:
            self.sent.append((writer, message_type, data))

        def mark_ready(self, writer: object) -> None:
            self.ready.add(writer)

        def revoke_ready(self, writer: object) -> None:
            self.ready.discard(writer)

    async def scenario() -> None:
        bridge = ElectronBridge(Queue(), tmp_path)
        fake_ws = FakeWS()
        bridge._loop = asyncio.get_running_loop()
        bridge._ws = fake_ws  # type: ignore[assignment]
        renderer_writer = object()
        bridge._handle_client_message({"type": "electron_hello", "data": {}}, renderer_writer)
        await asyncio.sleep(0.01)
        bridge.publish("text_generating", {"active": True, "chat_id": "chat", "turn_id": "turn"})
        before = (
            bridge._renderer_writer,
            bridge._renderer_ready,
            bridge._delivery_generation,
            bridge._disconnected_turn,
            bridge._active_turn,
        )
        probe_writer = object()
        bridge._handle_client_message({"type": "bridge_readiness_probe", "data": {}}, probe_writer)
        await asyncio.sleep(0.01)

        assert (
            bridge._renderer_writer,
            bridge._renderer_ready,
            bridge._delivery_generation,
            bridge._disconnected_turn,
            bridge._active_turn,
        ) == before
        assert fake_ws.ready == {renderer_writer}
        assert fake_ws.sent[-1] == (
            probe_writer,
            "bridge_ready",
            {"authenticated": True, "protocol": BRIDGE_PROTOCOL, "instance_id": bridge.instance_id},
        )
        assert bridge.intent_queue.empty()

    asyncio.run(scenario())


def test_readiness_probe_script_leaves_active_renderer_state_untouched(tmp_path: Path) -> None:
    session_file = tmp_path / ".electron-bridge-session.json"
    bridge = ElectronBridge(Queue(), tmp_path, ws_port=0, http_port=0, session_file=session_file)
    bridge.start()
    try:
        deadline = time.monotonic() + 2
        while not session_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session_file.exists(), "bridge must publish an authenticated descriptor"
        renderer_writer = object()
        bridge._renderer_writer = renderer_writer  # Simulate the live Electron socket.
        bridge._renderer_ready = True
        bridge._delivery_generation = 7
        bridge._disconnected_turn = {"chat_id": "old", "turn_id": "old-turn"}
        bridge._active_turn = {"chat_id": "chat", "turn_id": "turn"}
        before = (
            bridge._renderer_writer,
            bridge._renderer_ready,
            bridge._delivery_generation,
            bridge._disconnected_turn,
            bridge._active_turn,
        )

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "probe_electron_bridge.py"), "--session", str(session_file)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        assert (
            bridge._renderer_writer,
            bridge._renderer_ready,
            bridge._delivery_generation,
            bridge._disconnected_turn,
            bridge._active_turn,
        ) == before
        assert bridge.intent_queue.empty()
    finally:
        bridge.shutdown()


def test_failed_bridge_contender_keeps_healthy_session_descriptor(tmp_path: Path) -> None:
    """A second backend must never erase the authenticated owner's descriptor."""

    session_file = tmp_path / ".electron-bridge-session.json"
    owner = ElectronBridge(Queue(), tmp_path, ws_port=0, http_port=0, session_file=session_file)
    owner.start()
    contender: ElectronBridge | None = None
    try:
        deadline = time.monotonic() + 2
        while not session_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session_file.exists(), "healthy owner must publish its descriptor"
        owner_descriptor = json.loads(session_file.read_text(encoding="utf-8"))
        assert owner._ws is not None

        contender = ElectronBridge(
            Queue(),
            tmp_path,
            ws_port=owner._ws.bound_port,
            http_port=owner._http_bound_port,
            session_file=session_file,
        )
        contender.start()
        assert contender._thread is not None
        contender._thread.join(timeout=2)
        assert not contender._thread.is_alive(), "contender must stop after listener bind failure"

        assert json.loads(session_file.read_text(encoding="utf-8")) == owner_descriptor
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "probe_electron_bridge.py"), "--session", str(session_file)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
    finally:
        if contender is not None:
            contender.shutdown()
        owner.shutdown()


def test_launcher_probes_a_healthy_bridge_before_starting_backend() -> None:
    launcher = (ROOT / "run_electron.bat").read_text(encoding="utf-8")
    probe_index = launcher.index("probe_electron_bridge.py")
    backend_guard_index = launcher.index("if not defined DSAKIKO_ELECTRON_REUSE_BACKEND")
    backend_start_index = launcher.index('start "D_sakiko backend"')
    assert probe_index < backend_guard_index < backend_start_index


def test_bridge_rejects_non_loopback_listener() -> None:
    with pytest.raises(ValueError):
        ElectronBridge(Queue(), Path.cwd(), ws_host="0.0.0.0")


def _masked_text_frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(message).encode("utf-8")
    assert len(payload) < 126
    mask = b"test"
    return bytes([0x81, 0x80 | len(payload)]) + mask + bytes(
        value ^ mask[index % len(mask)] for index, value in enumerate(payload)
    )


async def _ws_handshake(port: int, target: str, *, origin: str = "file://", protocol: str = BRIDGE_PROTOCOL):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        (
            f"GET {target} HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {base64.b64encode(b'nonce-for-test').decode()}\r\n"
            f"Origin: {origin}\r\n"
            f"Sec-WebSocket-Protocol: {protocol}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    return reader, writer, (await reader.readuntil(b"\r\n\r\n")).decode("utf-8")


def test_ws_and_http_require_the_same_ephemeral_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        intents: list[dict[str, object]] = []
        token = "test-session-token"
        server = ElectronWSServer(
            "127.0.0.1", 0, lambda message, _writer: intents.append(message), session_token=token,
        )
        await server.start()
        try:
            _, writer, response = await _ws_handshake(server.bound_port, "/")
            assert response.startswith("HTTP/1.1 401")
            writer.close(); await writer.wait_closed()
            _, writer, response = await _ws_handshake(server.bound_port, "/?token=wrong")
            assert response.startswith("HTTP/1.1 401")
            writer.close(); await writer.wait_closed()
            _, writer, response = await _ws_handshake(server.bound_port, f"/?token={token}", origin="http://evil.invalid")
            assert response.startswith("HTTP/1.1 403")
            writer.close(); await writer.wait_closed()
            _, writer, response = await _ws_handshake(server.bound_port, f"/?token={token}")
            assert response.startswith("HTTP/1.1 101")
            writer.write(_masked_text_frame({"type": "ui_intent", "data": {"intent": "start_voice_input"}}))
            await writer.drain()
            await asyncio.sleep(0.01)
            assert intents == [{"type": "ui_intent", "data": {"intent": "start_voice_input"}}]
            writer.close(); await writer.wait_closed()
        finally:
            await server.stop()

        model = tmp_path / "live2d_related" / "sakiko" / "3.model.json"
        model.parent.mkdir(parents=True)
        model.write_text('{"model":"sakiko.moc"}', encoding="utf-8")
        bridge = ElectronBridge(Queue(), tmp_path)
        http = await asyncio.start_server(bridge._handle_http, "127.0.0.1", 0)
        try:
            port = http.sockets[0].getsockname()[1]
            async def request(token_value: str) -> str:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    f"GET /model/sakiko/3.model.json?token={token_value} HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: file://\r\n\r\n".encode()
                )
                await writer.drain()
                response = (await reader.read()).decode("utf-8")
                writer.close(); await writer.wait_closed()
                return response
            assert (await request("wrong")).startswith("HTTP/1.1 401")
            valid = await request(bridge.session_token)
            assert valid.startswith("HTTP/1.1 200")
            assert f"token={bridge.session_token}" in valid
        finally:
            http.close(); await http.wait_closed()

    asyncio.run(scenario())
