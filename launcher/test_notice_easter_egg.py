"""彩蛋交互回归测试；替换播放器，避免测试时实际外放。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from launcher_ui import EASTER_EGG_NOTICE, LauncherWindow


class NoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = LauncherWindow(Path(__file__).resolve().parent.parent)
        self.window.timer.stop()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def test_only_easter_egg_is_clickable(self):
        self.window.set_notice(EASTER_EGG_NOTICE)
        self.assertEqual(self.window.notice.textFormat(), Qt.RichText)
        self.assertIn('href="easter-egg"', self.window.notice.text())
        for text in ("", "主程序已关闭", "已启动桌面端。"):
            self.window.set_notice(text)
            self.assertEqual(self.window.notice.text(), text)
            self.assertEqual(self.window.notice.textInteractionFlags(), Qt.NoTextInteraction)
            with patch("launcher_ui.QMessageBox") as box:
                self.window.play_easter_egg("easter-egg")
                box.assert_not_called()

    def test_confirmation_and_single_player(self):
        self.window.set_notice(EASTER_EGG_NOTICE)
        with patch("launcher_ui.QMessageBox") as box, patch("PyQt5.QtMultimedia.QMediaPlayer") as factory:
            dialog = box.return_value
            yes, cancel = MagicMock(), MagicMock()
            dialog.addButton.side_effect = [yes, cancel] * 3
            dialog.clickedButton.return_value = cancel
            self.window.play_easter_egg("easter-egg")
            factory.assert_not_called()
            dialog.setText.assert_called_with("即将播放阿拉蕾小动静，注意外放")
            dialog.clickedButton.return_value = yes
            for _ in range(2):
                self.window.play_easter_egg("easter-egg")
            factory.assert_called_once_with(self.window)
            self.assertEqual(factory.return_value.play.call_count, 2)
            self.assertEqual(factory.return_value.stop.call_count, 2)
            media = factory.return_value.setMedia.call_args.args[0]
            self.assertTrue(media.canonicalUrl().toLocalFile().endswith("夢はパワー_arl.mp3"))

    def test_missing_audio_reports_error(self):
        self.window.set_notice(EASTER_EGG_NOTICE)
        with patch("launcher_ui.QMessageBox") as box, patch("launcher_ui.Path.is_file", return_value=False):
            dialog = box.return_value
            yes, cancel = MagicMock(), MagicMock()
            dialog.addButton.side_effect = [yes, cancel]
            dialog.clickedButton.return_value = yes
            self.window.play_easter_egg("easter-egg")
            box.warning.assert_called_once()
            self.assertIsNone(self.window.easter_egg_player)


if __name__ == "__main__":
    unittest.main()
