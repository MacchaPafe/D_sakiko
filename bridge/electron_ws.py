"""Authenticated loopback WebSocket transport for the Electron bridge."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import struct
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .protocol import create_message

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
BRIDGE_PROTOCOL = "dsakiko.bridge.v1"
_LOCAL_WEB_ORIGIN = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$")
_MAX_FRAME_BYTES = 1 << 20


def is_allowed_electron_origin(origin: str | None) -> bool:
    """Accept the packaged file renderer and the local Vite development UI.

    A token is still required. Origin is an additional browser-side guard so a
    random local web page cannot subscribe merely because it reaches loopback.
    """
    return origin in {"file://", "null"} or bool(origin and _LOCAL_WEB_ORIGIN.fullmatch(origin))


def is_loopback_peer(writer: asyncio.StreamWriter) -> bool:
    peer = writer.get_extra_info("peername")
    if not isinstance(peer, tuple) or not peer:
        return False
    try:
        return ipaddress.ip_address(str(peer[0])).is_loopback
    except ValueError:
        return False


class ElectronWSServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        on_message: Callable[[dict[str, Any], asyncio.StreamWriter], Any] | None = None,
        on_disconnect: Callable[[asyncio.StreamWriter], Any] | None = None,
        *,
        session_token: str,
    ) -> None:
        if not session_token:
            raise ValueError("ElectronWSServer requires a session token")
        self.host = host
        self.port = int(port)
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.session_token = session_token
        self.clients: set[asyncio.StreamWriter] = set()
        # Authentication only proves that the socket owns the bridge token.
        # The renderer becomes eligible for business events after the bridge
        # has sent the hello initialization packet and explicitly marks it
        # ready.
        self.ready_clients: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None

    @property
    def bound_port(self) -> int:
        if self.server is None or not self.server.sockets:
            return self.port
        return int(self.server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        for writer in tuple(self.clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.clients.clear()
        self.ready_clients.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def broadcast(self, message_type: str, data: Any) -> None:
        if not self.ready_clients:
            return
        dead: set[asyncio.StreamWriter] = set()
        for writer in tuple(self.ready_clients):
            try:
                await self.send(writer, message_type, data)
            except Exception:
                dead.add(writer)
        for writer in dead:
            self.clients.discard(writer)
            self.ready_clients.discard(writer)

    def mark_ready(self, writer: asyncio.StreamWriter) -> None:
        """Allow one authenticated socket to receive business events."""
        if writer in self.clients:
            self.ready_clients.add(writer)

    def revoke_ready(self, writer: asyncio.StreamWriter) -> None:
        self.ready_clients.discard(writer)

    async def send(self, writer: asyncio.StreamWriter, message_type: str, data: Any) -> None:
        await self._send_frame(writer, 0x1, create_message(message_type, data).encode("utf-8"))

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, headers = self._parse_request(request)
            if not is_loopback_peer(writer):
                await self._reject(writer, 403, "loopback peer required")
                return
            if method != "GET" or headers.get("upgrade", "").lower() != "websocket":
                await self._reject(writer, 400, "websocket upgrade required")
                return
            token = (parse_qs(urlsplit(target).query).get("token") or [""])[0]
            if not hmac.compare_digest(token, self.session_token):
                await self._reject(writer, 401, "invalid session")
                return
            if not is_allowed_electron_origin(headers.get("origin")):
                await self._reject(writer, 403, "invalid origin")
                return
            protocols = {part.strip() for part in headers.get("sec-websocket-protocol", "").split(",")}
            if BRIDGE_PROTOCOL not in protocols:
                await self._reject(writer, 400, "bridge protocol required")
                return
            key = headers.get("sec-websocket-key")
            if not key:
                await self._reject(writer, 400, "websocket key required")
                return
            accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    f"Sec-WebSocket-Protocol: {BRIDGE_PROTOCOL}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            self.clients.add(writer)
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                opcode, payload, masked = frame
                if not masked:
                    break
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    await self._send_frame(writer, 0xA, b"")
                elif opcode == 0x1 and self.on_message is not None:
                    try:
                        message = json.loads(payload.decode("utf-8"))
                        if not isinstance(message, dict):
                            continue
                        result = self.on_message(message, writer)
                        if isinstance(result, Awaitable):
                            await result
                    except Exception:
                        # A malformed UI message has no effect on the bridge.
                        pass
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError):
            pass
        finally:
            self.clients.discard(writer)
            self.ready_clients.discard(writer)
            if self.on_disconnect is not None:
                try:
                    result = self.on_disconnect(writer)
                    if isinstance(result, Awaitable):
                        await result
                except Exception:
                    # Disconnect cleanup is advisory; never retain a failed
                    # renderer socket because its bookkeeping raised.
                    pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _parse_request(request: bytes) -> tuple[str, str, dict[str, str]]:
        lines = request.decode("utf-8", errors="replace").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return method.upper(), target, headers

    @staticmethod
    async def _reject(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        body = message.encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} Unauthorized\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()

    @staticmethod
    async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes, bool] | None:
        try:
            header = await reader.readexactly(2)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
        if length > _MAX_FRAME_BYTES:
            raise ValueError("WebSocket frame too large")
        mask = await reader.readexactly(4) if masked else b""
        payload = bytearray(await reader.readexactly(length))
        if mask:
            for index in range(length):
                payload[index] ^= mask[index % 4]
        return opcode, bytes(payload), masked

    @staticmethod
    async def _send_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
        length = len(payload)
        frame = bytearray([0x80 | opcode])
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.extend((126, (length >> 8) & 0xFF, length & 0xFF))
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(payload)
        writer.write(frame)
        await writer.drain()
