from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
ORIGINAL_CWD = os.getcwd()

from PyQt5.QtMultimedia import QMediaPlayer
from PyQt5.QtWidgets import QApplication

import multi_char_main
from qconfig import d_sakiko_config
from ui_main.components.custom_bgm_dialog import CustomBGMDialog


class FakeBGMPlayer:
    """用于验证 BGM 回退逻辑的轻量播放器替身。"""

    def __init__(self, state: QMediaPlayer.State) -> None:
        self.state_value = state
        self.played = False
        self.stopped = False

    def state(self) -> QMediaPlayer.State:
        """返回当前伪造播放状态。"""
        return self.state_value

    def stop(self) -> None:
        """记录停止调用。"""
        self.stopped = True
        self.state_value = QMediaPlayer.StoppedState

    def play(self) -> None:
        """记录播放调用。"""
        self.played = True
        self.state_value = QMediaPlayer.PlayingState


class FakeBGMPlaylist:
    """用于验证 BGM 回退逻辑的轻量播放列表替身。"""

    def __init__(self) -> None:
        self.cleared = False

    def clear(self) -> None:
        """记录清空调用。"""
        self.cleared = True


class CustomBGMHandlingTestCase(unittest.TestCase):
    """验证自定义小剧场 BGM 的坏文件处理与回退。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试用 Qt 应用实例。"""
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        """恢复导入配置模块前的工作目录。"""
        os.chdir(ORIGINAL_CWD)

    def setUp(self) -> None:
        """保存全局配置和校验函数，避免测试间互相污染。"""
        self.original_bgm_path = d_sakiko_config.multi_char_background_music_path.value
        self.original_config_set = d_sakiko_config.set
        self.original_main_validate = multi_char_main.validate_bgm_media_file

    def tearDown(self) -> None:
        """恢复全局配置和校验函数。"""
        d_sakiko_config.multi_char_background_music_path.value = (
            self.original_bgm_path
        )
        d_sakiko_config.set = self.original_config_set
        multi_char_main.validate_bgm_media_file = self.original_main_validate
        self.app.processEvents()

    def _write_broken_mp3(self) -> Path:
        """创建一个扩展名正确但内容不可解码的临时 mp3。"""
        handle, file_name = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(handle, "wb") as file:
            file.write(b"not an audio stream")
        self.addCleanup(lambda: Path(file_name).unlink(missing_ok=True))
        return Path(file_name)

    def test_replace_bgm_rejects_unplayable_file_without_changing_config(
        self,
    ) -> None:
        """选择不可解码音频时不应写入当前 BGM 配置。"""
        old_path = self.original_bgm_path
        set_calls: list[str] = []

        def set_config_value(
            item: object,
            value: object,
            save: bool = True,
            copy: bool = True,
        ) -> None:
            """记录测试中的配置写入。"""
            del save, copy
            if item is d_sakiko_config.multi_char_background_music_path:
                set_calls.append(str(value))
                d_sakiko_config.multi_char_background_music_path.value = value

        d_sakiko_config.set = set_config_value
        dialog = CustomBGMDialog()
        self.addCleanup(dialog.deleteLater)

        dialog.replace_bgm(self._write_broken_mp3())

        self.assertEqual(set_calls, [])
        self.assertEqual(
            d_sakiko_config.multi_char_background_music_path.value,
            old_path,
        )
        self.assertIn("更改背景音乐失败", dialog.error_label.text())
        self.assertIn("音频解码失败", dialog.error_label.text())

    def test_add_bgm_reports_unplayable_file_reason(self) -> None:
        """添加不可解码音频时应在对话框中显示失败原因。"""
        dialog = CustomBGMDialog()
        self.addCleanup(dialog.deleteLater)

        dialog.add_bgm(str(self._write_broken_mp3()))

        self.assertIn("添加背景音乐失败", dialog.error_label.text())
        self.assertIn("音频解码失败", dialog.error_label.text())

    def test_runtime_bgm_failure_falls_back_to_playable_candidate(self) -> None:
        """当前 BGM 运行时失效后应回退到可用候选并更新配置。"""
        failed_path = "/tmp/broken-bgm.mp3"
        fallback_path = "/tmp/fallback-bgm.mp3"
        set_calls: list[str] = []
        messages: list[str] = []
        loaded_paths: list[str] = []

        window = multi_char_main.ViewerGUI.__new__(multi_char_main.ViewerGUI)
        window.bgm_player = FakeBGMPlayer(QMediaPlayer.PlayingState)
        window.bgm_playlist = FakeBGMPlaylist()
        window._current_bgm_path = failed_path
        window._recovering_bgm_error = False

        def candidate_bgm_paths(
            failed_bgm_path: str,
            preferred_path: str | None = None,
        ) -> list[str]:
            """返回测试用候选列表。"""
            del failed_bgm_path, preferred_path
            return [failed_path, fallback_path]

        def load_bgm(candidate_path: str) -> bool:
            """模拟只成功载入备用 BGM。"""
            loaded_paths.append(candidate_path)
            if candidate_path != fallback_path:
                return False
            window._current_bgm_path = candidate_path
            return True

        def show_message(message: str) -> None:
            """记录界面提示文本。"""
            messages.append(message)

        def set_config_value(
            item: object,
            value: object,
            save: bool = True,
            copy: bool = True,
        ) -> None:
            """记录测试中的配置写入。"""
            del save, copy
            if item is d_sakiko_config.multi_char_background_music_path:
                set_calls.append(str(value))
                d_sakiko_config.multi_char_background_music_path.value = value

        def validate_media(candidate_path: Path) -> str | None:
            """模拟坏文件校验失败、备用文件校验成功。"""
            if str(candidate_path) == failed_path:
                return "音频解码失败：InvalidMedia"
            return None

        window._candidate_bgm_paths = candidate_bgm_paths
        window._load_bgm = load_bgm
        window._show_bgm_failure_message = show_message
        d_sakiko_config.set = set_config_value
        multi_char_main.validate_bgm_media_file = validate_media

        recovered = multi_char_main.ViewerGUI._recover_bgm_after_failure(
            window,
            failed_path,
            "媒体无效或无法解码",
        )

        self.assertTrue(recovered)
        self.assertEqual(loaded_paths, [fallback_path])
        self.assertEqual(set_calls, [fallback_path])
        self.assertEqual(window._current_bgm_path, fallback_path)
        self.assertTrue(window.bgm_player.played)
        self.assertIn("媒体无效或无法解码", messages[0])
        self.assertIn("fallback-bgm.mp3", messages[0])


if __name__ == "__main__":
    unittest.main()
