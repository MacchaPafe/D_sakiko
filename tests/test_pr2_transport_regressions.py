import asyncio
import unittest
from unittest.mock import patch

from bridge.ws_server import WSServer


class _FrameReader:
    def __init__(self):
        self.calls = []

    async def readexactly(self, size):
        self.calls.append(size)
        if size == 2:
            # FIN + text opcode, zero-length payload.
            return b"\x81\x00"
        if size == 0:
            return b""
        raise AssertionError(f"unexpected read size: {size}")


class WebSocketIdleRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_established_frame_read_does_not_apply_idle_timeout(self):
        """An idle renderer stays connected until its peer actually closes."""
        reader = _FrameReader()
        server = WSServer()

        # The established-frame path must not call asyncio.wait_for at all.
        # HTTP handshake timeout remains a separate concern in _handle_client.
        with patch("bridge.ws_server.asyncio.wait_for", side_effect=AssertionError("unexpected frame timeout")):
            frame = await server._read_frame(reader)

        self.assertEqual(frame, (0x1, b""))
        self.assertEqual(reader.calls, [2, 0])


if __name__ == "__main__":
    unittest.main()
