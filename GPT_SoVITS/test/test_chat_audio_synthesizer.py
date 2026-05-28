from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from audio_generator import SILENCE_WAV_PATH
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import ChatAudioSynthesizer
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.events import AssistantSegmentDraft


class FakeCharacter:
    """提供测试用的已解析角色对象。"""


class FakeAudioGenerator:
    """提供稳定的测试语音生成结果。"""

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        """初始化测试生成器的调用记录。"""
        self.calls: list[str] = []
        self._responses = responses if responses is not None else ["/tmp/generated.wav"]

    def generate_audio_for_character_sync(
        self,
        text: str,
        character: object,
        sakiko_state: bool,
        audio_lan_choice: str,
    ) -> str:
        """返回测试生成音频路径。"""
        self.calls.append(text)
        response = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class ChatAudioSynthesizerTestCase(unittest.TestCase):
    """验证聊天单段语音合成服务的可观察行为。"""

    def test_enabled_segment_returns_generated_path_for_display_and_playback(self) -> None:
        """启用语音时，成功合成的路径同时用于展示和播放。"""
        audio_generator = FakeAudioGenerator()
        synthesizer = ChatAudioSynthesizer(audio_generator)
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=False,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=lambda _message: None,
        )

        self.assertEqual(result.display_audio_path, "/tmp/generated.wav")
        self.assertEqual(result.playback_audio_path, "/tmp/generated.wav")
        self.assertTrue(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, ["你好"])

    def test_disabled_audio_returns_no_audio_display_and_silence_playback(self) -> None:
        """关闭语音生成时，展示无音频但播放队列仍获得静音路径。"""
        audio_generator = FakeAudioGenerator()
        synthesizer = ChatAudioSynthesizer(audio_generator)
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=False,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=False,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=lambda _message: None,
        )

        self.assertEqual(result.display_audio_path, NO_AUDIO)
        self.assertEqual(result.playback_audio_path, SILENCE_WAV_PATH)
        self.assertFalse(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, [])

    def test_force_no_audio_returns_no_audio_display_and_silence_playback(self) -> None:
        """片段强制无音频时，跳过合成并返回静音播放路径。"""
        audio_generator = FakeAudioGenerator()
        synthesizer = ChatAudioSynthesizer(audio_generator)
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=True,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=lambda _message: None,
        )

        self.assertEqual(result.display_audio_path, NO_AUDIO)
        self.assertEqual(result.playback_audio_path, SILENCE_WAV_PATH)
        self.assertFalse(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, [])

    def test_missing_voice_model_silence_result_returns_no_audio_display(self) -> None:
        """底层因缺少语音模型返回静音时，展示路径转换为无音频。"""
        audio_generator = FakeAudioGenerator([SILENCE_WAV_PATH])
        synthesizer = ChatAudioSynthesizer(audio_generator)
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=False,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=lambda _message: None,
        )

        self.assertEqual(result.display_audio_path, NO_AUDIO)
        self.assertEqual(result.playback_audio_path, SILENCE_WAV_PATH)
        self.assertFalse(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, ["你好"])

    def test_one_retry_then_success_returns_generated_audio(self) -> None:
        """第一次合成失败后，默认第二次重试成功时返回真实音频。"""
        audio_generator = FakeAudioGenerator([RuntimeError("boom"), "/tmp/recovered.wav"])
        synthesizer = ChatAudioSynthesizer(audio_generator)
        status_messages: list[str] = []
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=False,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=status_messages.append,
        )

        self.assertEqual(result.display_audio_path, "/tmp/recovered.wav")
        self.assertEqual(result.playback_audio_path, "/tmp/recovered.wav")
        self.assertTrue(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, ["你好", "你好"])
        self.assertEqual(status_messages, ["语音合成出错，重试中"])

    def test_repeated_failure_returns_fallback_after_default_attempts(self) -> None:
        """默认两次合成都失败时，返回无音频展示和静音播放路径。"""
        audio_generator = FakeAudioGenerator([RuntimeError("boom"), RuntimeError("again")])
        synthesizer = ChatAudioSynthesizer(audio_generator)
        status_messages: list[str] = []
        draft = AssistantSegmentDraft(
            message_index=0,
            text="你好",
            translation="hello",
            emotion="LABEL_0",
            force_no_audio=False,
        )

        result = synthesizer.synthesize_segment(
            draft=draft,
            character=FakeCharacter(),
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=True,
                audio_language_choice="日英混合",
            ),
            status_callback=status_messages.append,
        )

        self.assertEqual(result.display_audio_path, NO_AUDIO)
        self.assertEqual(result.playback_audio_path, SILENCE_WAV_PATH)
        self.assertFalse(result.real_audio_generated)
        self.assertEqual(audio_generator.calls, ["你好", "你好"])
        self.assertEqual(status_messages, ["语音合成出错，重试中", "语音合成失败，已切换为静音"])
