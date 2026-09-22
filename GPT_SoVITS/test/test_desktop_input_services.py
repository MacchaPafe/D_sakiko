import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from runtime.drafts import DraftStore, DraftBinding
from runtime.voice_input import VoiceInputService
from ui_main.components.message_input import MessageInput
from ui_main.theme import derive_theme_palette


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_views_and_conversation_switch_preserve_drafts(self):
        store = DraftStore()
        palette = derive_theme_palette("#7799CC")
        one, two = MessageInput(palette), MessageInput(palette)
        first, second = DraftBinding(store, one, "a"), DraftBinding(store, two, "a")
        QTest.keyClicks(one.text_edit, "first")
        self.assertEqual(two.toPlainText(), "first")
        first.switch("b")
        second.switch("b")
        self.assertEqual(one.toPlainText(), "")
        QTest.keyClicks(two.text_edit, "second")
        self.assertEqual(one.toPlainText(), "second")
        first.switch("a")
        second.switch("a")
        self.assertEqual(two.toPlainText(), "first")
        one.close()
        two.close()

    def test_recording_binds_original_chat_and_closes_single_stream(self):
        store = DraftStore()
        store.set("a", "prefix ", [])
        stream = Mock()
        service = VoiceInputService(store, stream_factory=lambda **kwargs: stream)
        service.model = SimpleNamespace(
            transcribe=lambda *a, **k: ([SimpleNamespace(text="测试")], None)
        )
        service._state("idle")
        service.start("a", 7)
        service.start("b", 0)
        service._audio(np.ones((3200, 1), dtype=np.float32), 3200, None, None)
        store.set("b", "other", [])
        service.finish()
        service.finish()
        for _ in range(60):
            QTest.qWait(20)
            if service.state == "idle":
                break
        self.assertEqual(store.get("a").text, "prefix 测试")
        self.assertEqual(store.get("b").text, "other")
        stream.start.assert_called_once()
        stream.close.assert_called_once()
        service.close()

    def test_microphone_failure_can_retry(self):
        factory = Mock(side_effect=RuntimeError("device"))
        service = VoiceInputService(DraftStore(), stream_factory=factory)
        service._state("idle")
        errors = []
        service.error.connect(errors.append)
        service.start("a", 0)
        self.assertEqual(service.state, "idle")
        self.assertIsNone(service.stream)
        self.assertTrue(errors)
        service.close()

    def test_broken_stream_does_not_prevent_asr_shutdown(self):
        service = VoiceInputService(DraftStore())
        service.stream = Mock()
        stream = service.stream
        stream.stop.side_effect = RuntimeError("device removed")
        service.model = Mock()
        service.close()
        stream.close.assert_called_once()
        service.model.close.assert_called_once()
        self.assertEqual(service.state, "closed")

    def test_exit_during_model_load_releases_inference_process(self):
        service = VoiceInputService(DraftStore())
        process = Mock()
        service._asr_started(process)
        service.close()
        process.close.assert_called_once()
        late_process = Mock()
        service._asr_started(late_process)
        late_process.close.assert_called_once()

    def test_failed_asr_process_can_be_reloaded_without_losing_draft(self):
        store = DraftStore()
        store.set("a", "retain", [])
        service = VoiceInputService(store)
        service.model = Mock(closed=True)
        service._accept_result((("a", 1, 6), "", "worker exited"))
        self.assertEqual(service.state, "unavailable")
        self.assertEqual(store.get("a").text, "retain")
        service.load = Mock()
        service.toggle("a", 6)
        service.load.assert_called_once()
        service.close()


if __name__ == "__main__":
    unittest.main()
