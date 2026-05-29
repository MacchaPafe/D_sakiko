from __future__ import annotations

import os
import sys
import time
import unittest
from collections.abc import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat.chat import Chat
from chat.chat import Message
from chat_flow.audio_scheduler import AudioScheduler
from chat_flow.audio_scheduler import PENDING_AUDIO
from chat_flow.audio_scheduler import normalize_pending_audio_on_startup
from chat_flow.audio_scheduler import is_playable_audio_path
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import ChatAudioSynthesisResult
from chat_flow.events import AssistantSegmentDraft
from emotion_enum import EmotionEnum


class FakeSynthesizer:
    """测试用语音合成器替身。"""

    def __init__(self, paths: list[str | Exception] | None = None) -> None:
        """初始化预设路径与调用记录。"""
        self.paths = paths or ["/tmp/generated.wav"]
        self.calls: list[str] = []
        self.emotions: list[str] = []

    def synthesize_segment(
        self,
        *,
        draft: AssistantSegmentDraft,
        character: object,
        settings: ChatAudioSettings,
        status_callback: Callable[[str], None],
    ) -> ChatAudioSynthesisResult:
        """返回下一条预设语音合成结果。"""
        self.calls.append(draft.text)
        self.emotions.append(draft.emotion)
        response = self.paths[min(len(self.calls) - 1, len(self.paths) - 1)]
        if isinstance(response, Exception):
            raise response
        path = response
        return ChatAudioSynthesisResult(path, path, True)


class AudioSchedulerTestCase(unittest.TestCase):
    """验证 controller 层音频调度器的可观察行为。"""

    def test_pending_audio_is_distinct_non_playable_sentinel(self) -> None:
        """PENDING_AUDIO 应区别于真实路径和 NO_AUDIO，且不可播放。"""
        self.assertNotEqual(PENDING_AUDIO, NO_AUDIO)
        self.assertFalse(is_playable_audio_path(PENDING_AUDIO))
        self.assertFalse(is_playable_audio_path(NO_AUDIO))
        self.assertTrue(is_playable_audio_path("/tmp/voice.wav"))

    def test_queue_message_marks_pending_audio_and_records_task_snapshot(self) -> None:
        """排队语音任务应先把历史消息标记为 PENDING_AUDIO 并保存任务快照。"""
        chat = Chat(message_list=[
            Message("祥子", "こんにちは。", "", EmotionEnum.HAPPINESS, "")
        ])
        settings = ChatAudioSettings(
            audio_enabled=True,
            sakiko_state=True,
            audio_language_choice="日英混合",
        )
        synthesizer = FakeSynthesizer()
        scheduler = AudioScheduler(
            synthesizer=synthesizer,
            foreground_resolver=lambda chat_id: chat_id == chat.chat_id,
        )

        event = scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            message_index=0,
            text="こんにちは。",
            character="祥子",
            settings=settings,
            emotion="LABEL_4",
        )

        self.assertEqual(chat.message_list[0].audio_path, PENDING_AUDIO)
        self.assertEqual(chat.runtime.queued_audio_tasks, 1)
        self.assertEqual(event.task.chat_id, chat.chat_id)
        self.assertEqual(event.task.turn_id, "turn-1")
        self.assertEqual(event.task.message_index, 0)
        self.assertEqual(event.task.text, "こんにちは。")
        self.assertEqual(event.task.character, "祥子")
        self.assertEqual(event.task.settings, settings)
        self.assertEqual(event.task.emotion, "LABEL_4")

        scheduler.run_next()
        self.assertEqual(synthesizer.emotions, ["LABEL_4"])

    def test_run_next_prioritizes_current_foreground_chat_dynamically(self) -> None:
        """取下一个任务时应按当前可见 chat 动态优先，而不是按入队时状态。"""
        visible_chat_id = ""
        background_chat = Chat(message_list=[
            Message("祥子", "后台", "", EmotionEnum.HAPPINESS, "")
        ])
        foreground_chat = Chat(message_list=[
            Message("祥子", "前台", "", EmotionEnum.HAPPINESS, "")
        ])
        synthesizer = FakeSynthesizer(["/tmp/foreground.wav", "/tmp/background.wav"])
        scheduler = AudioScheduler(
            synthesizer=synthesizer,
            foreground_resolver=lambda chat_id: chat_id == visible_chat_id,
        )
        settings = ChatAudioSettings(True, True, "日英混合")
        scheduler.queue_message(
            chat=background_chat,
            chat_id=background_chat.chat_id,
            turn_id="turn-bg",
            message_index=0,
            text="后台",
            character="祥子",
            settings=settings,
        )
        scheduler.queue_message(
            chat=foreground_chat,
            chat_id=foreground_chat.chat_id,
            turn_id="turn-fg",
            message_index=0,
            text="前台",
            character="祥子",
            settings=settings,
        )

        visible_chat_id = foreground_chat.chat_id
        event = scheduler.run_next()

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.task.chat_id, foreground_chat.chat_id)
        self.assertEqual(synthesizer.calls, ["前台"])
        self.assertEqual(foreground_chat.message_list[0].audio_path, "/tmp/foreground.wav")
        self.assertEqual(background_chat.message_list[0].audio_path, PENDING_AUDIO)

    def test_failed_task_fills_history_with_no_audio(self) -> None:
        """合成异常时应把 pending audio 回填为 NO_AUDIO。"""
        chat = Chat(message_list=[
            Message("祥子", "会失败", "", EmotionEnum.HAPPINESS, "")
        ])
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer([RuntimeError("tts failed")]),
            foreground_resolver=lambda chat_id: chat_id == chat.chat_id,
        )
        scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            message_index=0,
            text="会失败",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        event = scheduler.run_next()

        self.assertIsNotNone(event)
        assert event is not None
        self.assertFalse(event.success)
        self.assertEqual(event.audio_path, NO_AUDIO)
        self.assertEqual(chat.message_list[0].audio_path, NO_AUDIO)
        self.assertEqual(chat.runtime.queued_audio_tasks, 0)
        self.assertEqual(chat.runtime.running_audio_tasks, 0)

    def test_background_completion_updates_history_without_foreground_callback(self) -> None:
        """后台语音完成只能回填历史，不应触发前台播放/展示回调。"""
        visible_chat_id = "visible-chat"
        background_chat = Chat(message_list=[
            Message("祥子", "后台音频", "", EmotionEnum.HAPPINESS, "")
        ])
        foreground_events: list[str] = []
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(["/tmp/background.wav"]),
            foreground_resolver=lambda chat_id: chat_id == visible_chat_id,
            foreground_completion_callback=lambda event: foreground_events.append(event.audio_path),
        )
        scheduler.queue_message(
            chat=background_chat,
            chat_id=background_chat.chat_id,
            turn_id="turn-bg",
            message_index=0,
            text="后台音频",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        event = scheduler.run_next()

        self.assertIsNotNone(event)
        assert event is not None
        self.assertFalse(event.foreground_at_completion)
        self.assertEqual(background_chat.message_list[0].audio_path, "/tmp/background.wav")
        self.assertEqual(foreground_events, [])

    def test_foreground_completion_notifies_controller_callback(self) -> None:
        """前台语音完成时应通知 controller 做可见播放/展示决策。"""
        chat = Chat(message_list=[
            Message("祥子", "前台音频", "", EmotionEnum.HAPPINESS, "")
        ])
        foreground_events: list[str] = []
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(["/tmp/foreground.wav"]),
            foreground_resolver=lambda chat_id: chat_id == chat.chat_id,
            foreground_completion_callback=lambda event: foreground_events.append(event.audio_path),
        )
        scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-fg",
            message_index=0,
            text="前台音频",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        event = scheduler.run_next()

        self.assertIsNotNone(event)
        assert event is not None
        self.assertTrue(event.foreground_at_completion)
        self.assertEqual(foreground_events, ["/tmp/foreground.wav"])

    def test_started_scheduler_consumes_queued_task_in_background(self) -> None:
        """启动后的调度器应自动消费队列并回填音频路径。"""
        chat = Chat(message_list=[
            Message("祥子", "后台线程音频", "", EmotionEnum.HAPPINESS, "")
        ])
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(["/tmp/threaded.wav"]),
            foreground_resolver=lambda chat_id: chat_id == chat.chat_id,
        )
        scheduler.start()

        scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-threaded",
            message_index=0,
            text="后台线程音频",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        deadline = time.monotonic() + 1.0
        while chat.message_list[0].audio_path == PENDING_AUDIO and time.monotonic() < deadline:
            time.sleep(0.01)
        scheduler.shutdown(cancel_background=True, timeout_seconds=0.1)

        self.assertEqual(chat.message_list[0].audio_path, "/tmp/threaded.wav")

    def test_cancel_queued_tasks_by_chat_fills_pending_audio_with_no_audio(self) -> None:
        """按 chat 取消未开始语音任务时，应将 pending audio 回填为 NO_AUDIO。"""
        chat = Chat(message_list=[
            Message("祥子", "待取消", "", EmotionEnum.HAPPINESS, "")
        ])
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(),
            foreground_resolver=lambda chat_id: False,
        )
        scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            message_index=0,
            text="待取消",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        cancelled = scheduler.cancel_queued_tasks_for_chat(chat.chat_id)

        self.assertEqual(cancelled, 1)
        self.assertEqual(chat.message_list[0].audio_path, NO_AUDIO)
        self.assertEqual(chat.runtime.queued_audio_tasks, 0)
        self.assertIsNone(scheduler.run_next())

    def test_shutdown_normalizes_all_unfinished_pending_audio(self) -> None:
        """关闭调度器时，应取消未完成队列并归一化 pending audio。"""
        chat = Chat(message_list=[
            Message("祥子", "待关闭", "", EmotionEnum.HAPPINESS, "")
        ])
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(),
            foreground_resolver=lambda chat_id: False,
        )
        scheduler.queue_message(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            message_index=0,
            text="待关闭",
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        cancelled = scheduler.shutdown(cancel_background=True, timeout_seconds=0.01)

        self.assertEqual(cancelled, 1)
        self.assertEqual(chat.message_list[0].audio_path, NO_AUDIO)
        self.assertEqual(chat.runtime.queued_audio_tasks, 0)

    def test_startup_normalization_rewrites_stale_pending_audio(self) -> None:
        """启动加载旧存档时，应把 stale PENDING_AUDIO 改为 NO_AUDIO。"""
        chat = Chat(message_list=[
            Message("祥子", "旧 pending", "", EmotionEnum.HAPPINESS, PENDING_AUDIO),
            Message("祥子", "已有音频", "", EmotionEnum.HAPPINESS, "/tmp/voice.wav"),
        ])

        changed = normalize_pending_audio_on_startup([chat])

        self.assertEqual(changed, 1)
        self.assertEqual(chat.message_list[0].audio_path, NO_AUDIO)
        self.assertEqual(chat.message_list[1].audio_path, "/tmp/voice.wav")

    def test_historical_regeneration_is_blocked_when_chat_has_unfinished_work(self) -> None:
        """历史音频再生成在 chat 有未完成工作时应被阻塞。"""
        chat = Chat(message_list=[
            Message("祥子", "历史消息", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        chat.runtime.running_turn_ids.add("turn-running")
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(),
            foreground_resolver=lambda chat_id: True,
        )

        result = scheduler.queue_historical_regeneration(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="regen-1",
            message_index=0,
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )

        self.assertFalse(result.queued)
        self.assertIn("请先终止生成", result.status_message)
        self.assertEqual(chat.message_list[0].audio_path, "NO_AUDIO")

    def test_historical_regeneration_fills_path_and_auto_plays_if_still_foreground(self) -> None:
        """历史音频再生成完成时，前台 chat 应触发前台完成回调。"""
        chat = Chat(message_list=[
            Message("祥子", "历史消息", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        foreground_events: list[str] = []
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(["/tmp/regen.wav"]),
            foreground_resolver=lambda chat_id: chat_id == chat.chat_id,
            foreground_completion_callback=lambda event: foreground_events.append(event.audio_path),
        )

        result = scheduler.queue_historical_regeneration(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="regen-1",
            message_index=0,
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )
        event = scheduler.run_next()

        self.assertTrue(result.queued)
        self.assertIsNotNone(result.event)
        assert result.event is not None
        self.assertGreater(result.event.task.priority, 0)
        self.assertEqual(result.event.task.task_kind, "historical_regeneration")
        self.assertIsNotNone(event)
        self.assertEqual(chat.message_list[0].audio_path, "/tmp/regen.wav")
        self.assertEqual(foreground_events, ["/tmp/regen.wav"])

    def test_historical_regeneration_does_not_auto_play_after_switching_away(self) -> None:
        """历史音频完成时若已切走，只回填路径不自动播放。"""
        visible_chat_id = "other-chat"
        chat = Chat(message_list=[
            Message("祥子", "历史消息", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        foreground_events: list[str] = []
        completion_events: list[str] = []
        scheduler = AudioScheduler(
            synthesizer=FakeSynthesizer(["/tmp/regen.wav"]),
            foreground_resolver=lambda chat_id: chat_id == visible_chat_id,
            completion_callback=lambda event: completion_events.append(event.audio_path),
            foreground_completion_callback=lambda event: foreground_events.append(event.audio_path),
        )

        scheduler.queue_historical_regeneration(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="regen-1",
            message_index=0,
            character="祥子",
            settings=ChatAudioSettings(True, True, "日英混合"),
        )
        event = scheduler.run_next()

        self.assertIsNotNone(event)
        self.assertEqual(chat.message_list[0].audio_path, "/tmp/regen.wav")
        self.assertEqual(completion_events, ["/tmp/regen.wav"])
        self.assertEqual(foreground_events, [])


if __name__ == "__main__":
    unittest.main()
