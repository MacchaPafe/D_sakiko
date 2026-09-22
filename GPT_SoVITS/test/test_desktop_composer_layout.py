import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from queue import Queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from tempfile import TemporaryDirectory
from pathlib import Path
from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from desktop_pet.window import PetWindow
from runtime.drafts import DraftStore
from runtime.voice_input import VoiceInputService
from ui_main.theme import derive_theme_palette


class ComposerLayoutTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_image_preview_and_long_draft_keep_send_button_inside_panel(self):
        drafts = DraftStore()
        voice = VoiceInputService(drafts)
        voice._state("idle")
        host = Mock(current_chat_id="a", drafts=drafts, voice_input=voice)
        host._theme_palette = derive_theme_palette("#7799CC")
        host.is_response_active.return_value = False
        host._current_model_supports_vision.return_value = True
        pet = PetWindow(host, Queue(), Queue(), SimpleNamespace(value=True))
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "image.png"
        fixture = QImage(16, 16, QImage.Format_RGB32)
        fixture.fill(0xFF7788AA)
        fixture.save(str(path))
        try:
            pet.expand()
            pet.refresh_state()
            initial = pet.panel.height()
            pet.input.add_managed_draft(
                dict(
                    draft_attachment_id="image",
                    staging_path=str(path),
                    mime_type="image/png",
                    original_name="image.png",
                    upload_state="ready",
                )
            )
            pet.input.text_edit.setPlainText("line\n" * 5)
            QTest.qWait(150)
            pet.panel.layout().activate()
            pet.refresh_state()
            self.assertGreater(pet.panel.height(), initial)
            bottom = pet.send_button.mapTo(
                pet.panel, QPoint(0, pet.send_button.height())
            ).y()
            self.assertLessEqual(bottom, pet.panel.height())
            self.assertLessEqual(pet.panel.geometry().bottom(), pet.height())
            self.assertEqual(len(drafts.get("a").images), 1)
        finally:
            pet.shutdown()
            voice.close()
