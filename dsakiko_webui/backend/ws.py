from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import urlparse

from fastapi import WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from .auth import COOKIE_NAME, AccessController
from .protocol import (
    PROTOCOL_VERSION,
    CommandEnvelope,
    ProtocolError,
    command_result,
)


MAX_FRAME_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


class WebSocketManager:
    """维护唯一活动 WebSocket，并通过统一访问控制校验会话。"""

    def __init__(self, auth: AccessController, runtime: object) -> None:
        """绑定统一访问控制与无界面运行时。"""
        self.auth = auth
        self.runtime = runtime
        self.websocket: WebSocket | None = None
        self.token: str | None = None
        self.sequence = 0
        self._send_lock = asyncio.Lock()

    async def close_token(self, token: str | None, code: int, reason: str) -> None:
        if token and self.websocket is not None and self.token == token:
            socket = self.websocket
            self.websocket = None
            self.token = None
            await socket.close(code=code, reason=reason)

    async def connect(self, websocket: WebSocket, token: str) -> None:
        if self.websocket is not None:
            await self.websocket.close(code=4409, reason="控制端已在新的页面连接")
        await websocket.accept()
        self.websocket = websocket
        self.token = token
        self.sequence = 0

    async def send_event(self, event: dict[str, object], request_id: str | None = None) -> None:
        if self.websocket is None:
            return
        async with self._send_lock:
            if self.websocket is None:
                return
            self.sequence += 1
            envelope = {
                "protocol_version": PROTOCOL_VERSION,
                "kind": "event",
                "type": event["type"],
                "event_id": f"evt_{uuid.uuid4().hex}",
                "session_id": self.runtime.session_id,
                "sequence": self.sequence,
                "timestamp": int(time.time()),
                "request_id": request_id if request_id is not None else event.get("request_id"),
                "chat_id": event.get("chat_id"),
                "turn_id": event.get("turn_id"),
                "data": event.get("data", {}),
            }
            try:
                await self.websocket.send_json(envelope)
            except (RuntimeError, WebSocketDisconnect):
                self.websocket = None
                self.token = None

    async def send_json(self, websocket: WebSocket, data: dict[str, object]) -> None:
        async with self._send_lock:
            await websocket.send_json(data)

    async def serve(self, websocket: WebSocket) -> None:
        token = websocket.cookies.get(COOKIE_NAME)
        if not self.auth.is_authenticated(token):
            await websocket.accept()
            await websocket.close(code=4401, reason="需要重新登录")
            return

        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host", "").split(":", 1)[0].lower()
        if origin and (urlparse(origin).hostname or "").lower() != host:
            await websocket.accept()
            await websocket.close(code=4403, reason="Origin 不允许")
            return

        await self.connect(websocket, token)
        await self.send_event(self.runtime.runtime_status_event())
        ready_event = self.runtime.runtime_ready_event()
        if ready_event:
            await self.send_event(ready_event)

        try:
            while self.websocket is websocket:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    await websocket.close(code=1009, reason="消息过大")
                    return
                request_id = ""
                try:
                    raw_data = json.loads(raw)
                    if isinstance(raw_data, dict) and isinstance(raw_data.get("request_id"), str):
                        request_id = raw_data["request_id"]
                    command = CommandEnvelope.model_validate(raw_data)
                    if command.protocol_version != PROTOCOL_VERSION:
                        error = ProtocolError("UNSUPPORTED_PROTOCOL", "前后端协议版本不一致。")
                        await self.send_json(websocket, command_result(command.request_id, error=error))
                        await websocket.close(code=4406, reason="协议版本不支持")
                        return
                    if command.kind != "command":
                        raise ProtocolError("INVALID_ENVELOPE", "这不是有效的命令信封。")
                    result, events = self.runtime.handle_command(command.type, command.payload)
                    await self.send_json(websocket, command_result(command.request_id, data=result))
                    for event in events:
                        await self.send_event(event, command.request_id)
                except (json.JSONDecodeError, ValidationError, TypeError):
                    error = ProtocolError("INVALID_ENVELOPE", "命令格式不正确。")
                    await self.send_json(websocket, command_result(request_id, error=error))
                except ProtocolError as error:
                    await self.send_json(websocket, command_result(request_id, error=error))
                except Exception:
                    logger.exception("WebSocket 命令处理失败")
                    error = ProtocolError("INTERNAL_ERROR", "后端处理命令时出现错误。", True)
                    await self.send_json(websocket, command_result(request_id, error=error))
        except WebSocketDisconnect:
            pass
        finally:
            if self.websocket is websocket:
                self.websocket = None
                self.token = None
