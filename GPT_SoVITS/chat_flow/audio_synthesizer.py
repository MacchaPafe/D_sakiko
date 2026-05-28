from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from audio_generator import SILENCE_WAV_PATH
from chat_flow.events import AssistantSegmentDraft

# 固定的“无音频”情况下的占位内容
NO_AUDIO = "NO_AUDIO"


class ChatAudioGenerator(Protocol):
    """描述聊天语音合成所需的同步生成器接口。"""

    def generate_audio_for_character_sync(
        self,
        text: str,
        character: object,
        sakiko_state: bool,
        audio_lan_choice: str,
    ) -> str:
        """按给定角色同步生成语音并返回音频路径。"""


@dataclass(frozen=True)
class ChatAudioSettings:
    """
    保存单段聊天语音合成所需的不可变设置。
    这些设置不可以在一轮对话中变化，否则，整体行为会比较奇怪）
    """

    audio_enabled: bool
    sakiko_state: bool
    audio_language_choice: str


@dataclass(frozen=True)
class ChatAudioSynthesisResult:
    """保存单段聊天语音合成对展示和播放队列的结果。"""

    display_audio_path: str
    playback_audio_path: str
    real_audio_generated: bool


class ChatAudioSynthesizer:
    """
    为聊天回复中的单个 assistant 片段合成语音。
    audio_generator 只负责“给一段文本和角色信息，生成语音”，本类进一步支持重试，合成一个片段的语音等功能
    """

    def __init__(
        self,
        audio_generator: ChatAudioGenerator,
        *,
        max_attempts: int = 2,
        silence_path: str = SILENCE_WAV_PATH,
    ) -> None:
        """创建单段语音合成服务。"""
        self._audio_generator = audio_generator
        self._max_attempts = max_attempts
        self._silence_path = silence_path

    def synthesize_segment(
        self,
        *,
        draft: AssistantSegmentDraft,
        character: object,
        settings: ChatAudioSettings,
        status_callback: Callable[[str], None],
    ) -> ChatAudioSynthesisResult:
        """合成一个 assistant 片段并返回展示路径与播放路径。"""
        if not settings.audio_enabled or draft.force_no_audio:
            return self._fallback_result()

        audio_path = self._silence_path
        for attempt_number in range(1, self._max_attempts + 1):
            try:
                audio_path = self._audio_generator.generate_audio_for_character_sync(
                    draft.text,
                    character,
                    settings.sakiko_state,
                    settings.audio_language_choice,
                )
                break
            except Exception:
                if attempt_number < self._max_attempts:
                    status_callback("语音合成出错，重试中")
                    continue
                status_callback("语音合成失败，已切换为静音")
                return self._fallback_result()

        # audio_generator 内部有可能因为某些原因失败而返回空的语音路径
        # 此时同样返回失败结果
        if audio_path == self._silence_path:
            return self._fallback_result()

        return ChatAudioSynthesisResult(
            display_audio_path=audio_path,
            playback_audio_path=audio_path,
            real_audio_generated=True,
        )

    def _fallback_result(self) -> ChatAudioSynthesisResult:
        """返回无音频、播放静音等情况下的降级结果。"""
        return ChatAudioSynthesisResult(
            display_audio_path=NO_AUDIO,
            playback_audio_path=self._silence_path,
            real_audio_generated=False,
        )
