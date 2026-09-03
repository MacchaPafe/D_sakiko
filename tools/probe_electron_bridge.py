#!/usr/bin/env python3
"""Authenticated readiness probe for the loopback Electron bridge."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import struct
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


BRIDGE_PROTOCOL = "dsakiko.bridge.v1"


class ProbeError(RuntimeError):
    """The bridge has not completed its authenticated ready handshake."""


def read_descriptor(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise ProbeError(f"session descriptor is unavailable: {error}") from error
    if not isinstance(data, dict):
        raise ProbeError("session descriptor is not an object")
    descriptor = {key: data.get(key) for key in ("protocol", "token", "ws_url", "instance_id")}
    if any(not isinstance(value, str) or not value for value in descriptor.values()):
        raise ProbeError("session descriptor is missing protocol, token, ws_url, or instance_id")
    if descriptor["protocol"] != BRIDGE_PROTOCOL:
        raise ProbeError("session descriptor has an unexpected protocol")
    return descriptor  # type: ignore[return-value]


def masked_text_frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    size = len(payload)
    if size < 126:
        header = bytes((0x81, 0x80 | size))
    elif size < 1 << 16:
        header = bytes((0x81, 0x80 | 126)) + struct.pack("!H", size)
    else:
        header = bytes((0x81, 0x80 | 127)) + struct.pack("!Q", size)
    mask = secrets.token_bytes(4)
    return header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))


async def read_server_message(reader: asyncio.StreamReader) -> dict[str, object]:
    while True:
        first, second = await reader.readexactly(2)
        opcode, size = first & 0x0F, second & 0x7F
        if second & 0x80:
            raise ProbeError("server sent an invalid masked frame")
        if size == 126:
            size = struct.unpack("!H", await reader.readexactly(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", await reader.readexactly(8))[0]
        payload = await reader.readexactly(size)
        if opcode == 0x9:
            continue
        if opcode != 0x1:
            raise ProbeError("bridge closed or sent a non-text readiness frame")
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ProbeError("bridge sent an invalid readiness message") from error
        if not isinstance(message, dict):
            raise ProbeError("bridge sent a non-object readiness message")
        return message


async def probe(descriptor: dict[str, str], timeout: float) -> None:
    parsed = urlsplit(descriptor["ws_url"])
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        raise ProbeError("session descriptor does not name a loopback WebSocket")
    token = (parse_qs(parsed.query).get("token") or [""])[0]
    if token != descriptor["token"]:
        raise ProbeError("WebSocket URL does not carry the descriptor token")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(parsed.hostname, parsed.port), timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        writer.write(
            (
                f"GET {parsed.path or '/'}?{parsed.query} HTTP/1.1\r\n"
                f"Host: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Origin: file://\r\n"
                f"Sec-WebSocket-Protocol: {BRIDGE_PROTOCOL}\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        response = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)).decode("latin-1")
        if not response.startswith("HTTP/1.1 101") or f"Sec-WebSocket-Protocol: {BRIDGE_PROTOCOL}" not in response:
            raise ProbeError("bridge rejected the authenticated WebSocket handshake")
        # This confirms the authenticated bridge instance only. It must never
        # claim the single renderer slot or request initialization facts.
        writer.write(masked_text_frame({"type": "bridge_readiness_probe", "data": {}}))
        await writer.drain()
        message = await asyncio.wait_for(read_server_message(reader), timeout)
        data = message.get("data")
        if (
            message.get("type") != "bridge_ready"
            or not isinstance(data, dict)
            or data.get("authenticated") is not True
            or data.get("protocol") != BRIDGE_PROTOCOL
            or data.get("instance_id") != descriptor["instance_id"]
        ):
            raise ProbeError("bridge did not confirm the descriptor instance after authentication")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()
    try:
        descriptor = read_descriptor(Path(args.session))
        asyncio.run(probe(descriptor, max(0.1, args.timeout)))
    except (ProbeError, OSError, asyncio.TimeoutError) as error:
        print(f"Electron bridge is not authenticated-ready: {error}", file=sys.stderr)
        return 2
    print("Electron bridge authenticated readiness confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
