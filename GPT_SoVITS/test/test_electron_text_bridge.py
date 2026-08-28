from __future__ import annotations

import os
import sys
import unittest
from queue import Queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from live2d_support.authoritative_owner import AuthoritativeLive2DOwner
from live2d_support.renderer_host import SharedRendererService
from qtUI import ChatGUI


class ElectronTextBridgeTest(unittest.TestCase):
    def _chat_stub(self, intent_queue):
        chat = ChatGUI.__new__(ChatGUI)
        chat.live2d_text_queue = Queue()
        chat.live2d_text_intent_queue = intent_queue
        return chat

    def test_formatted_text_reaches_electron_exact_command(self):
        intents, facts, commands = Queue(), Queue(), Queue()
        chat = self._chat_stub(intents)
        display_text = ChatGUI._format_live2d_display_text("（微笑）你好，祥子。", "Hello, Sakiko.")

        chat._queue_live2d_display_text(display_text)
        self.assertEqual(chat.live2d_text_queue.get_nowait(), display_text)

        service = SharedRendererService(intents, facts, commands, AuthoritativeLive2DOwner())
        self.assertEqual(service.run_once(), 1)
        self.assertEqual(commands.get_nowait(), {"type": "text", "data": {"text": display_text}})

    def test_pygame_only_text_path_keeps_legacy_queue(self):
        chat = self._chat_stub(None)
        display_text = ChatGUI._format_live2d_display_text("你好。", "")

        chat._queue_live2d_display_text(display_text)
        self.assertEqual(chat.live2d_text_queue.get_nowait(), display_text)


if __name__ == "__main__":
    unittest.main()
