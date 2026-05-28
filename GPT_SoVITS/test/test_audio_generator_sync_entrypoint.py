from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from audio_generator import SILENCE_WAV_PATH, AudioGenerate, VoiceTaskHandle
from character import CharacterAttributes


class CapturingAudioGenerate(AudioGenerate):
    """记录同步入口送入合成 worker 的文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.generated_texts: list[str] = []

    def generate_audio(
        self,
        text: str,
        character: CharacterAttributes,
        sakiko_state: bool,
        audio_lan_choice: str,
    ) -> VoiceTaskHandle:
        """记录合成文本并返回测试用等待句柄。"""
        self.generated_texts.append(text)
        return VoiceTaskHandle(request_id="test-request", command_type="synthesize")

    def _wait_for_synthesize_result(self, handle: VoiceTaskHandle) -> str:
        """返回稳定的测试音频路径。"""
        return "/tmp/generated.wav"


class AudioGeneratorSyncEntrypointTestCase(unittest.TestCase):
    """验证显式角色同步语音生成入口的可观察行为。"""

    def setUp(self) -> None:
        """创建具备有效语音模型配置的测试角色。"""
        self.temp_dir: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        model_dir = self.temp_dir.name
        self.character: CharacterAttributes = CharacterAttributes()
        self.character.character_name = "祥子"
        self.character.GPT_model_path = self._touch_file(model_dir, "model.ckpt")
        self.character.sovits_model_path = self._touch_file(model_dir, "model.pth")

    def tearDown(self) -> None:
        """清理测试角色使用的临时文件。"""
        self.temp_dir.cleanup()

    def _touch_file(self, directory: str, filename: str) -> str:
        """创建一个空文件并返回路径。"""
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8"):
            pass
        return path

    def test_explicit_character_sync_removes_bracketed_content_before_generation(self) -> None:
        """显式角色同步入口在合成前移除括号和中括号内容。"""
        audio_generator = CapturingAudioGenerate()

        audio_path = audio_generator.generate_audio_for_character_sync(
            "「祥子（舞台指示）[翻译]」",
            self.character,
            True,
            "日英混合",
        )

        self.assertEqual(audio_path, "/tmp/generated.wav")
        self.assertEqual(audio_generator.generated_texts, ["さきこ"])

    def test_explicit_character_sync_removes_quote_marks_before_generation(self) -> None:
        """显式角色同步入口在合成前移除常见引号。"""
        audio_generator = CapturingAudioGenerate()

        audio_path = audio_generator.generate_audio_for_character_sync(
            '"祥子"',
            self.character,
            True,
            "日英混合",
        )

        self.assertEqual(audio_path, "/tmp/generated.wav")
        self.assertEqual(audio_generator.generated_texts, ["さきこ"])

    def test_explicit_character_sync_preserves_language_replacement(self) -> None:
        """显式角色同步入口保留语言相关替换。"""
        audio_generator = CapturingAudioGenerate()

        audio_path = audio_generator.generate_audio_for_character_sync(
            "「CRYCHIC」",
            self.character,
            True,
            "日英混合",
        )

        self.assertEqual(audio_path, "/tmp/generated.wav")
        self.assertEqual(audio_generator.generated_texts, ["クライシック"])

    def test_explicit_character_sync_returns_silence_for_invalid_cleaned_text(self) -> None:
        """显式角色同步入口对清洗后无效的文本返回静音。"""
        audio_generator = CapturingAudioGenerate()

        audio_path = audio_generator.generate_audio_for_character_sync(
            "（舞台指示）[翻译]",
            self.character,
            True,
            "日英混合",
        )

        self.assertEqual(audio_path, SILENCE_WAV_PATH)
        self.assertEqual(audio_generator.generated_texts, [])
