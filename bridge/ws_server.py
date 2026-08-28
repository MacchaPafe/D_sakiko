"""
WebSocket 服务 — 纯 Python 3.9 原生实现，兼容 Electron/Chrome
"""
import asyncio
import hashlib
import base64
import struct
import json
import sys
import os
import inspect
import secrets
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from protocol import create_message

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSServer:
    def __init__(self, host="localhost", port=9876, on_message=None, on_connect=None, on_disconnect=None,
                 auth_token=None, allowed_origins=None, max_message_size=1024 * 1024):
        self.host = host
        self.port = port
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        try:
            self._message_accepts_writer = on_message is not None and len(inspect.signature(on_message).parameters) >= 2
        except (TypeError, ValueError):
            self._message_accepts_writer = False
        self._clients = set()
        self._server = None
        self.auth_token = auth_token
        self.allowed_origins = set(allowed_origins or {"", "null", "file://", "http://localhost", "http://127.0.0.1"})
        self.max_message_size = int(max_message_size)
        self._identities = {}

    async def _read_http_request(self, reader):
        """读取 HTTP 请求头（兼容 LF 和 CRLF）"""
        data = b""
        while b"\r\n\r\n" not in data and b"\n\n" not in data:
            chunk = await reader.read(1024)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", errors="replace")

    async def _handle_client(self, reader, writer):
        try:
            request = await asyncio.wait_for(
                self._read_http_request(reader), timeout=5
            )
        except asyncio.TimeoutError:
            writer.close()
            return

        key = None
        headers = {}
        request_target = "/"
        for line in request.replace("\r\n", "\n").split("\n"):
            if line.startswith("GET "):
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    request_target = parts[1]
                continue
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()

        origin = headers.get("origin", "")
        local_origin = origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")
        if origin not in self.allowed_origins and not local_origin:
            writer.close()
            return
        if self.auth_token is not None:
            supplied = parse_qs(urlsplit(request_target).query).get("token", [""])[0]
            if not supplied or not secrets.compare_digest(str(supplied), str(self.auth_token)):
                writer.close()
                return
        if not key:
            writer.close()
            return

        accept = base64.b64encode(
            hashlib.sha1(key.encode() + GUID.encode()).digest()
        ).decode()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

        self._clients.add(writer)
        print(f"[WS] Client connected ({len(self._clients)} total)")
        if self.on_connect is not None:
            try:
                result = self.on_connect(writer)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

        try:
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # Close
                    await self._send_frame(writer, 0x8, b"")
                    break
                if opcode == 0x9:  # Ping
                    await self._send_frame(writer, 0xA, b"")
                if opcode == 0x1 and self.on_message is not None:
                    try:
                        if len(payload) > self.max_message_size:
                            break
                        message = json.loads(payload.decode("utf-8"))
                        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                            break
                        data = message.get("data")
                        if not isinstance(data, dict):
                            break
                        identity = (str(data.get("renderer_id") or ""), str(data.get("renderer_instance_id") or ""))
                        if message["type"] == "renderer_hello":
                            if not all(identity):
                                break
                            previous = self._identities.get(writer)
                            if previous is not None and previous != identity:
                                break
                            self._identities[writer] = identity
                        elif writer not in self._identities:
                            break
                        elif identity != self._identities[writer]:
                            break
                        result = (
                            self.on_message(message, writer)
                            if self._message_accepts_writer
                            else self.on_message(message)
                        )
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            self._clients.discard(writer)
            self._identities.pop(writer, None)
            if self.on_disconnect is not None:
                try:
                    result = self.on_disconnect(writer)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
            try:
                writer.close()
            except Exception:
                pass
            print(f"[WS] Client disconnected ({len(self._clients)} remaining)")

    async def _read_frame(self, reader):
        """读取 WebSocket 帧"""
        try:
            header = await asyncio.wait_for(reader.readexactly(2), timeout=60)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

        fin = header[0] & 0x80
        opcode = header[0] & 0x0F
        masked = header[1] & 0x80
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
        if length > self.max_message_size:
            return None

        if masked:
            mask = await reader.readexactly(4)
        else:
            mask = b""

        payload = bytearray(await reader.readexactly(length))
        if masked:
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]

        return opcode, bytes(payload)

    async def _send_frame(self, writer, opcode, payload):
        """发送 WebSocket 文本帧"""
        frame = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.extend([126, (length >> 8) & 0xFF, length & 0xFF])
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(payload)
        writer.write(bytes(frame))
        await writer.drain()

    async def broadcast(self, msg_type, data):
        if not self._clients:
            return
        message = create_message(msg_type, data)
        payload = message.encode("utf-8")
        dead = set()
        for writer in self._clients.copy():
            try:
                await self._send_frame(writer, 0x1, payload)
            except Exception:
                dead.add(writer)
        self._clients -= dead

    async def send_to(self, writer, msg_type, data):
        """Send one protocol message to a single connected client."""
        if writer not in self._clients:
            return
        message = create_message(msg_type, data)
        await self._send_frame(writer, 0x1, message.encode("utf-8"))

    async def start(self):
        print(f"[WS] Starting server on ws://{self.host}:{self.port}")
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
