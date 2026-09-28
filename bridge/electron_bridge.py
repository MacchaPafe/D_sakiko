"""Authenticated business-event transport for the independent Electron pet.

Python publishes business facts only.  Motion and presentation selection remain
entirely inside Electron; the bridge never receives renderer state or callbacks.
"""

from __future__ import annotations

import asyncio
import copy
import hmac
import json
import mimetypes
import os
import posixpath
import secrets
import threading
from http import HTTPStatus
from pathlib import Path
from queue import Queue
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .electron_ws import BRIDGE_PROTOCOL, ElectronWSServer, is_allowed_electron_origin, is_loopback_peer


class ElectronBridge:
    """Loopback-only authenticated resource and business-event bridge."""

    _SELECTION_EVENT_TYPES = {"initial_model", "switch_character", "switch_live2d", "sakiko_state", "theme"}

    def __init__(
        self,
        event_queue: Queue[dict[str, object] | None],
        project_root: str | os.PathLike[str],
        *,
        ws_host: str = "127.0.0.1",
        ws_port: int = 9876,
        http_host: str = "127.0.0.1",
        http_port: int = 9877,
        session_file: str | os.PathLike[str] | None = None,
    ) -> None:
        self.event_queue = event_queue
        self.intent_queue: Queue[dict[str, object]] = Queue()
        self.project_root = Path(project_root).resolve()
        self.model_root = (self.project_root / "live2d_related").resolve()
        self.audio_roots = tuple(
            root.resolve()
            for root in (
                self.project_root / "reference_audio",
                self.project_root / "GPT_SoVITS" / "reference_audio",
            )
        )
        if ws_host not in {"127.0.0.1", "localhost", "::1"} or http_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ElectronBridge only supports loopback listeners")
        self.ws_host, self.ws_port = ws_host, int(ws_port)
        self.http_host, self.http_port = http_host, int(http_port)
        self.session_token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_urlsafe(16)
        self.session_file = Path(session_file or self.project_root / ".electron-bridge-session.json").resolve()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws: ElectronWSServer | None = None
        self._http: asyncio.AbstractServer | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._stopped = threading.Event()
        self._latest_selection: dict[str, dict[str, object]] = {}
        self._active_turn: dict[str, object] | None = None
        # Electron is a single-instance frontend.  Keep only the current
        # hello-ready socket and the exact backend turn observed when it left;
        # there is no renderer registry or renderer-owned snapshot here.
        self._renderer_writer: asyncio.StreamWriter | None = None
        self._renderer_ready = False
        self._disconnected_turn: dict[str, object] | None = None
        self._disconnected_turn_completed = True
        self._orphan_turn: dict[str, object] | None = None
        # Events queued before hello (or during a transport gap) must not be
        # replayed into a newly initialized renderer.  This is delivery
        # bookkeeping, not renderer state.
        self._delivery_generation = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        # Do not touch a descriptor until this instance owns both listeners.
        # A failed contender must not erase a healthy backend's session.
        self._thread = threading.Thread(target=self._run, name="electron-bridge", daemon=True)
        self._thread.start()

    def publish(self, message_type: str, data: dict[str, object] | None = None) -> None:
        event = {"type": str(message_type), "data": dict(data or {})}
        if event["type"] in self._SELECTION_EVENT_TYPES:
            self._cache_selection_event(event)
        self._cache_active_turn_event(event)
        event["_delivery_generation"] = self._delivery_generation
        self.event_queue.put(event)

    def shutdown(self) -> None:
        self._stopped.set()
        self.event_queue.put(None)
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_async(), loop)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None
        self._remove_session_descriptor()

    def url_for_path(self, path: str | os.PathLike[str], kind: str = "model") -> str:
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self.project_root / "GPT_SoVITS" / raw
        resolved = candidate.resolve()
        if kind == "audio":
            if not any(self._inside(resolved, root) for root in self.audio_roots):
                return ""
            return self._resource_url("audio", resolved.relative_to(self.project_root))
        if kind != "model" or not self._inside(resolved, self.model_root):
            return ""
        return self._resource_url("model", resolved.relative_to(self.model_root))

    def _resource_url(self, kind: str, relative: Path) -> str:
        query = urlencode({"token": self.session_token})
        return f"http://{self.http_host}:{self._http_bound_port}/{kind}/{relative.as_posix()}?{query}"

    @property
    def _http_bound_port(self) -> int:
        if self._http is None or not self._http.sockets:
            return self.http_port
        return int(self._http.sockets[0].getsockname()[1])

    def _session_descriptor(self) -> dict[str, object]:
        return {
            "protocol": BRIDGE_PROTOCOL,
            "token": self.session_token,
            "instance_id": self.instance_id,
            "ws_url": f"ws://{self.ws_host}:{self._ws.bound_port if self._ws else self.ws_port}/?{urlencode({'token': self.session_token})}",
        }

    def _write_session_descriptor(self) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.session_file.with_suffix(self.session_file.suffix + ".tmp")
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._session_descriptor(), handle)
            os.replace(temporary, self.session_file)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _remove_session_descriptor(self) -> None:
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and hmac.compare_digest(str(data.get("token", "")), self.session_token):
                self.session_file.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        if self._stopped.is_set():
            loop.close()
            self._loop = None
            return
        self._ws = ElectronWSServer(
            self.ws_host,
            self.ws_port,
            self._handle_client_message,
            self._handle_renderer_disconnect,
            session_token=self.session_token,
        )
        try:
            loop.run_until_complete(self._ws.start())
            loop.run_until_complete(self._start_http())
            self._write_session_descriptor()
            self._pump_task = loop.create_task(self._pump_events())
            loop.run_forever()
        except OSError as error:
            print(f"[ElectronBridge] unavailable: {error}", flush=True)
        finally:
            try:
                loop.run_until_complete(self._stop_async())
            except Exception:
                pass
            loop.close()
            self._loop = None
            self._remove_session_descriptor()

    async def _pump_events(self) -> None:
        while not self._stopped.is_set():
            try:
                event = await asyncio.to_thread(self.event_queue.get)
            except Exception:
                return
            if event is None:
                return
            if not isinstance(event, dict) or not event.get("type") or self._ws is None:
                continue
            # A hello initialization always starts a fresh delivery epoch.
            # Drop frames from before that epoch instead of replaying a partial
            # assistant turn after reconnect.
            if event.get("_delivery_generation") != self._delivery_generation:
                continue
            event_data = event.get("data") or {}
            if not self._renderer_ready or self._renderer_writer is None:
                continue
            await self._ws.broadcast(str(event["type"]), event_data)

    def _cache_selection_event(self, event: dict[str, object]) -> None:
        message_type = str(event["type"])
        data = dict(event.get("data") or {})
        if message_type in {"initial_model", "switch_character", "switch_live2d"}:
            self._latest_selection["model"] = {"type": "initial_model", "data": data}
            if "sakiko_state" in data:
                self._latest_selection["sakiko_state"] = {
                    "type": "sakiko_state",
                    "data": {key: data[key] for key in ("sakiko_state", "presentation_base", "character_folder", "character_name") if key in data},
                }
        elif message_type == "sakiko_state":
            identity = data.get("sakiko_state")
            if identity not in ("black", "white"):
                identity = data.get("value")
            # ``maskoff`` is an interaction request, not a new character
            # identity. Do not let it replace the black/white fact used for a
            # reconnect snapshot.
            if identity not in ("black", "white"):
                return
            current = dict(self._latest_selection.get("model", {}).get("data", {}))
            current.update(data)
            current["sakiko_state"] = identity
            if identity == "black":
                current["presentation_base"] = "serious"
            elif identity == "white":
                current["presentation_base"] = "idle"
            self._latest_selection["model"] = {"type": "initial_model", "data": current}
            self._latest_selection["sakiko_state"] = {"type": "sakiko_state", "data": data}
        elif message_type == "theme":
            self._latest_selection["theme"] = {"type": "theme", "data": data}

    def _cache_active_turn_event(self, event: dict[str, object]) -> None:
        """Track only enough backend fact to cancel a refreshed renderer safely."""
        message_type = str(event["type"])
        data = dict(event.get("data") or {})
        if (message_type == "text_generating" and data.get("active") is True) or message_type == "assistant_segment":
            turn = self._turn_identity(data)
            if turn is not None:
                self._active_turn = turn
                # A backend turn must not run on after Electron has no
                # hello-ready socket: its first segment could never be
                # presented consistently.  Reuse the exact Qt cancellation
                # path once per turn.
                if not self._renderer_ready and self._orphan_turn != turn:
                    self._orphan_turn = copy.deepcopy(turn)
                    self.intent_queue.put({"intent": "recover_renderer", "data": turn})
        elif message_type in {"assistant_turn_complete", "cancel", "cancel_turn", "bye"}:
            completed_turn = self._turn_identity(data)
            if completed_turn is None:
                self._active_turn = None
                return
            if self._disconnected_turn == completed_turn:
                self._disconnected_turn_completed = True
            if self._orphan_turn == completed_turn:
                self._orphan_turn = None
            if self._active_turn == completed_turn:
                self._active_turn = None

    @staticmethod
    def _turn_identity(data: dict[str, object]) -> dict[str, object] | None:
        chat_id, turn_id = str(data.get("chat_id") or ""), str(data.get("turn_id") or "")
        if not chat_id or not turn_id:
            return None
        return {"chat_id": chat_id, "turn_id": turn_id}

    async def _send_hello_snapshot(
        self,
        ws: ElectronWSServer,
        writer: asyncio.StreamWriter,
        *,
        recovery: dict[str, object] | None,
    ) -> None:
        await ws.send(writer, "bridge_ready", {
            "authenticated": True,
            "protocol": BRIDGE_PROTOCOL,
            "instance_id": self.instance_id,
        })
        if recovery is not None:
            await ws.send(writer, "renderer_recovery", recovery)
        # The model business fact already includes current Sakiko identity and
        # base presentation. A second sakiko_state event would look like a new
        # conversion after a refresh.
        for key in ("model", "theme"):
            event = self._latest_selection.get(key)
            if event:
                await ws.send(writer, str(event["type"]), event.get("data") or {})

        # Mark readiness only after bridge_ready and the initialization
        # snapshot have been sent.  This closes the pre-hello business-event
        # window without replaying the old event queue.
        self._delivery_generation += 1
        self._renderer_writer = writer
        self._renderer_ready = True
        ws.mark_ready(writer)

    async def _send_readiness_confirmation(
        self,
        ws: ElectronWSServer,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Confirm an authenticated bridge without claiming renderer ownership."""
        await ws.send(writer, "bridge_ready", {
            "authenticated": True,
            "protocol": BRIDGE_PROTOCOL,
            "instance_id": self.instance_id,
        })

    def _handle_client_message(self, message: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        if message.get("type") == "bridge_readiness_probe":
            loop, ws = self._loop, self._ws
            if loop is not None and ws is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._send_readiness_confirmation(ws, writer),
                    loop,
                )
            return
        if message.get("type") == "electron_hello":
            loop, ws = self._loop, self._ws
            if loop is not None and ws is not None and loop.is_running():
                # Single-instance renderer: a new hello supersedes any old
                # socket and captures its exact active turn before the new
                # initialization begins.
                if self._renderer_writer is not None and self._renderer_writer is not writer:
                    previous_writer = self._renderer_writer
                    ws.revoke_ready(previous_writer)
                    self._capture_renderer_disconnect(previous_writer)
                    # A stale authenticated socket must not remain open while
                    # the replacement hello is being initialized. Otherwise
                    # the old renderer never observes disconnect and cannot
                    # reconnect to the current snapshot.
                    try:
                        previous_writer.close()
                    except Exception:
                        pass
                recovery: dict[str, object] | None = None
                if self._disconnected_turn is not None:
                    recovery = {"action": "cancel", **copy.deepcopy(self._disconnected_turn)}
                    if not self._disconnected_turn_completed and self._active_turn == self._disconnected_turn:
                        self._active_turn = None
                        self.intent_queue.put({
                            "intent": "recover_renderer",
                            "data": copy.deepcopy(self._disconnected_turn),
                        })
                    self._disconnected_turn = None
                    self._disconnected_turn_completed = True
                asyncio.run_coroutine_threadsafe(
                    self._send_hello_snapshot(
                        ws,
                        writer,
                        recovery=recovery,
                    ),
                    loop,
                )
            return
        if message.get("type") != "ui_intent":
            return
        data = message.get("data")
        if not isinstance(data, dict):
            return
        if not self._renderer_ready or self._renderer_writer is not writer:
            return
        intent = data.get("intent")
        if isinstance(intent, str) and intent in {
            "start_voice_input", "stop_voice_input", "open_python_settings",
        }:
            self.intent_queue.put({"intent": intent, "data": dict(data)})

    def _handle_renderer_disconnect(self, writer: asyncio.StreamWriter) -> None:
        """Freeze the leaving renderer's identity before later turns begin."""
        if self._renderer_writer is not writer:
            return
        self._capture_renderer_disconnect(writer)

    def _capture_renderer_disconnect(self, writer: asyncio.StreamWriter) -> None:
        if self._renderer_writer is not writer:
            return
        self._renderer_writer = None
        self._renderer_ready = False
        self._delivery_generation += 1
        self._disconnected_turn = copy.deepcopy(self._active_turn)
        self._disconnected_turn_completed = self._active_turn is None

    async def _stop_async(self) -> None:
        pump_task, self._pump_task = self._pump_task, None
        if pump_task is not None and not pump_task.done():
            pump_task.cancel()
            await asyncio.gather(pump_task, return_exceptions=True)
        if self._http is not None:
            self._http.close()
            await self._http.wait_closed()
            self._http = None
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        asyncio.get_running_loop().stop()

    async def _start_http(self) -> None:
        self._http = await asyncio.start_server(self._handle_http, self.http_host, self.http_port)

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        origin: str | None = None
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, headers = ElectronWSServer._parse_request(request)
            origin = headers.get("origin")
            if not is_loopback_peer(writer):
                self._write_http(writer, HTTPStatus.FORBIDDEN, b"", "text/plain", origin)
                return
            token = (parse_qs(urlsplit(target).query).get("token") or [""])[0]
            if not hmac.compare_digest(token, self.session_token):
                self._write_http(writer, HTTPStatus.UNAUTHORIZED, b"", "text/plain", origin)
                return
            if origin is not None and not is_allowed_electron_origin(origin):
                self._write_http(writer, HTTPStatus.FORBIDDEN, b"", "text/plain", origin)
                return
            if method != "GET":
                self._write_http(writer, HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain", origin)
                return
            path = unquote(urlsplit(target).path)
            file_path = self._resolve_url_path(path)
            if file_path is None or not file_path.is_file():
                self._write_http(writer, HTTPStatus.NOT_FOUND, b"", "text/plain", origin)
                return
            content = file_path.read_bytes()
            if path.startswith("/model/") and file_path.suffix.lower() == ".json":
                content = self._normalize_model_definition(content, file_path)
            self._write_http(
                writer, HTTPStatus.OK, content,
                mimetypes.guess_type(file_path.name)[0] or "application/octet-stream", origin,
            )
        except Exception:
            try:
                self._write_http(writer, HTTPStatus.BAD_REQUEST, b"", "text/plain", origin)
            except Exception:
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _resolve_url_path(self, url_path: str) -> Path | None:
        prefix, _, relative = url_path.lstrip("/").partition("/")
        if prefix not in {"model", "audio"} or not relative or "\\" in relative:
            return None
        if prefix == "model":
            if any(part in {"", ".", ".."} for part in relative.split("/")):
                return None
            candidate = (self.model_root / posixpath.normpath(relative)).resolve()
            return candidate if self._inside(candidate, self.model_root) else None
        if any(part in {"", ".", ".."} for part in relative.split("/")):
            return None
        candidate = (self.project_root / posixpath.normpath(relative)).resolve()
        return candidate if any(self._inside(candidate, root) for root in self.audio_roots) else None

    def _normalize_model_definition(self, content: bytes, model_path: Path) -> bytes:
        try:
            model = self._normalize_legacy_model(json.loads(content.decode("utf-8")))
            return json.dumps(self._tokenize_model_references(model, model_path), ensure_ascii=False).encode("utf-8")
        except Exception:
            return content

    @staticmethod
    def _normalize_legacy_model(model: dict[str, Any]) -> dict[str, Any]:
        motions = model.get("motions") or {}
        legacy = motions.get("rana")
        if not isinstance(legacy, list) or len(legacy) < 42:
            return model
        ranges = {
            "happiness": (0, 6), "sadness": (6, 12), "anger": (12, 18), "disgust": (18, 24),
            "like": (24, 30), "surprise": (30, 36), "fear": (36, 42), "IDLE": (42, 51),
            "text_generating": (51, 54), "bye": (54, 56), "change_character": (56, 59),
            "idle_motion": (59, 60), "talking_motion": (60, 61),
        }
        updated = copy.deepcopy(model)
        updated.pop("controllers", None)
        updated.pop("hit_areas", None)
        updated["motions"] = {name: legacy[start:end] for name, (start, end) in ranges.items()}
        return updated

    def _tokenize_model_references(self, value: Any, model_path: Path, key: str = "") -> Any:
        file_keys = {"file", "File", "model", "Moc", "physics", "Physics", "pose", "Pose", "DisplayInfo", "UserData"}
        sequence_keys = {"textures", "Textures"}
        if isinstance(value, dict):
            return {name: self._tokenize_model_references(item, model_path, name) for name, item in value.items()}
        if isinstance(value, list):
            return [self._tokenize_model_references(item, model_path, key) for item in value]
        if not isinstance(value, str) or key not in file_keys | sequence_keys:
            return value
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or value.startswith("data:"):
            return value
        candidate = (model_path.parent / value).resolve()
        if not self._inside(candidate, self.model_root):
            return value
        return self._resource_url("model", candidate.relative_to(self.model_root))

    @staticmethod
    def _inside(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    @staticmethod
    def _write_http(
        writer: asyncio.StreamWriter, status: HTTPStatus, body: bytes, content_type: str, origin: str | None,
    ) -> None:
        cors = f"Access-Control-Allow-Origin: {origin}\r\n" if origin and is_allowed_electron_origin(origin) else ""
        writer.write(
            (
                f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                f"Content-Type: {content_type}\r\n{cors}"
                "Cache-Control: no-store\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode() + body
        )
