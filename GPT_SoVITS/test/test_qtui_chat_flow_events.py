from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import qtUI
from chat.chat import Chat
from chat.chat import Message
from chat_flow.audio_scheduler import AudioTask
from chat_flow.audio_scheduler import AudioTaskCompleted
from chat_flow.audio_scheduler import AudioTaskQueued
from chat_flow.audio_scheduler import HistoricalAudioRegenerationResult
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.controller import PendingTurnCancellationResult
from emotion_enum import EmotionEnum


class FakeChatDisplay:
    """记录聊天展示组件收到的工具状态行。"""

    def __init__(self) -> None:
        """初始化空状态行列表。"""
        self.lines: list[tuple[str, str, str]] = []
        self.transient_lines: list[tuple[str, str, bool, int]] = []
        self.messages: list[tuple[Message, int, bool, int]] = []

    def append_tool_status_line(self, tool_call_id: str, text: str, status: str = "running") -> None:
        """记录一次工具状态展示。"""
        self.lines.append((tool_call_id, text, status))

    def append_transient_text(
        self,
        character_name: str,
        text: str,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """记录一次临时对话文本展示。"""
        self.transient_lines.append((character_name, text, stream, interval_ms))

    def append_message(
        self,
        message: Message,
        msg_index: int,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """记录一次普通消息展示。"""
        self.messages.append((message, msg_index, stream, interval_ms))


class FakeStatusBox:
    """记录状态提示框收到的文本。"""

    def __init__(self) -> None:
        """初始化空状态文本列表。"""
        self.lines: list[str] = []

    def clear(self) -> None:
        """清空已记录的状态文本。"""
        self.lines.clear()

    def append(self, text: str) -> None:
        """记录一条状态提示文本。"""
        self.lines.append(text)


class FakeCharacter:
    """提供测试用当前角色对象。"""

    def __init__(self, character_name: str, voice_valid: bool = True) -> None:
        """保存角色名。"""
        self.character_name = character_name
        self.voice_valid = voice_valid
        self.GPT_model_path = "/tmp/gpt.ckpt"
        self.sovits_model_path = "/tmp/sovits.pth"
        self.gptsovits_ref_audio = "/tmp/ref.wav"

    def has_valid_voice_model(self) -> bool:
        """返回测试角色是否具备语音模型。"""
        return self.voice_valid


class FakeChatManager:
    """提供按编号查找对话的最小接口。"""

    def __init__(self, chat: Chat) -> None:
        """保存单个测试对话。"""
        self.chat = chat

    def get_chat_by_id(self, chat_id: str) -> Chat | None:
        """按编号返回测试对话。"""
        if chat_id == self.chat.chat_id:
            return self.chat
        return None


class FakeAudioSettingsState:
    """提供历史音频再生成需要的语音设置。"""

    def __init__(self) -> None:
        """初始化默认语音设置。"""
        self.sakiko_state = True
        self.audio_language_choice = "日英混合"


class FakeHistoricalRegenerationController:
    """记录历史音频再生成请求并模拟 controller 返回值。"""

    def __init__(self, result: HistoricalAudioRegenerationResult | None = None) -> None:
        """保存可选返回值并初始化调用记录。"""
        self.result = result
        self.calls: list[tuple[str, int, object, bool, str]] = []
        self.audio_completed_calls: list[tuple[str, str, str, str]] = []

    def queue_historical_audio_regeneration(
        self,
        *,
        chat: Chat,
        chat_id: str,
        message_index: int,
        character: object,
        sakiko_state: bool,
        audio_language_choice: str,
    ) -> HistoricalAudioRegenerationResult:
        """记录一次历史音频再生成请求。"""
        self.calls.append((chat_id, message_index, character, sakiko_state, audio_language_choice))
        if self.result is not None:
            return self.result
        return HistoricalAudioRegenerationResult(
            queued=True,
            event=AudioTaskQueued(task=AudioTask(
                chat_id=chat_id,
                turn_id="regen-test",
                message_index=message_index,
                text=chat.message_list[message_index].text,
                character=character,
                settings=ChatAudioSettings(True, sakiko_state, audio_language_choice),
                priority=100,
                task_kind="historical_regeneration",
                emotion=chat.message_list[message_index].emotion.as_label(),
            )),
        )

    def handle_audio_completed(
        self,
        *,
        chat_id: str,
        audio_path: str,
        emotion: str,
        display_text: str,
        live2d_client: object,
    ) -> None:
        """记录并转发前台音频完成播放请求。"""
        self.audio_completed_calls.append((chat_id, audio_path, emotion, display_text))
        play_segment = getattr(live2d_client, "play_segment")
        play_segment(audio_path=audio_path, emotion=emotion, display_text=display_text)


class FakeHistoricalLive2DClient:
    """记录历史音频再生成完成后的前台播放请求。"""

    def __init__(self) -> None:
        """初始化播放调用记录。"""
        self.play_calls: list[tuple[str, str, str]] = []

    def play_segment(self, *, audio_path: str, emotion: str, display_text: str = "") -> bool:
        """记录一次播放请求。"""
        self.play_calls.append((audio_path, emotion, display_text))
        return True


class FakeHistoricalRegenerationWindow:
    """提供 ChatGUI 历史音频再生成路径所需的最小窗口对象。"""

    def __init__(
        self,
        controller: FakeHistoricalRegenerationController | None = None,
        *,
        current_chat_id: str = "chat-1",
    ) -> None:
        """初始化当前聊天、角色表和可观测 UI 状态。"""
        self.current_chat = Chat(
            message_list=[
                Message("祥子", "历史消息", "历史翻译", EmotionEnum.HAPPINESS, "NO_AUDIO"),
            ],
            chat_id="chat-1",
        )
        self.chat_manager = FakeChatManager(self.current_chat)
        self.current_chat_id = current_chat_id
        self.character_by_name = {"祥子": FakeCharacter("祥子")}
        self.current_character = self.character_by_name["祥子"]
        self.dp_chat = FakeAudioSettingsState()
        self.flow_controller = controller or FakeHistoricalRegenerationController()
        self.live2d_client = FakeHistoricalLive2DClient()
        self.window_titles: list[str] = []
        self.status_messages: list[str] = []
        self.refresh_current_count = 0
        self.refresh_chat_list_count = 0

    def setWindowTitle(self, title: str) -> None:
        """记录窗口标题更新。"""
        self.window_titles.append(title)

    def refresh_current_chat_display(self) -> None:
        """记录当前聊天被刷新。"""
        self.refresh_current_count += 1

    def refresh_chat_list(self) -> None:
        """记录聊天列表被刷新。"""
        self.refresh_chat_list_count += 1

    def _notify_status(self, message: str) -> None:
        """记录状态提示。"""
        self.status_messages.append(message)


class FakeUserInput:
    """提供用户输入框所需的最小接口。"""

    def __init__(self, text: str) -> None:
        """保存输入框文本和清空状态。"""
        self._text = text
        self.was_cleared = False

    def text(self) -> str:
        """返回当前输入框文本。"""
        return self._text

    def clear(self) -> None:
        """记录输入框已被清空。"""
        self.was_cleared = True


class FakeCancelController:
    """提供取消路径需要的 controller 替身。"""

    def __init__(self, result: PendingTurnCancellationResult) -> None:
        """保存取消返回值并初始化调用记录。"""
        self.result = result
        self.cancelled_chat_ids: list[str] = []

    def cancel_visible_chat(self, chat: Chat) -> PendingTurnCancellationResult:
        """记录被取消的可见 chat。"""
        self.cancelled_chat_ids.append(chat.chat_id)
        return self.result


class FakeCancelLive2DClient:
    """记录取消播放请求的 Live2D 客户端替身。"""

    def __init__(self) -> None:
        """初始化取消调用记录。"""
        self.cancelled_turns: list[tuple[str, str]] = []

    def cancel_turn_playback(self, *, chat_id: str, turn_id: str) -> bool:
        """记录一轮播放取消请求。"""
        self.cancelled_turns.append((chat_id, turn_id))
        return True


class FakeCancelWindow:
    """提供 ChatGUI.cancel_active_turn 需要的最小窗口对象。"""

    def __init__(self, cancel_result: PendingTurnCancellationResult) -> None:
        """初始化一轮活跃对话和取消依赖。"""
        self.current_chat = Chat(chat_id="chat-1")
        self.current_chat_id = self.current_chat.chat_id
        self.active_chat_id = self.current_chat_id
        self.active_turn_id = "turn-1"
        self.active_turn_phase = "llm"
        self.cancelled_turn_ids: set[str] = set()
        self.flow_controller = FakeCancelController(cancel_result)
        self.dp_chat = object()
        self.live2d_client = FakeCancelLive2DClient()
        self.messages_box = FakeStatusBox()
        self.refreshed_chat_count = 0
        self.refreshed_chat_list_count = 0
        self.finished_stream_count = 0

    def refresh_current_chat_display(self) -> None:
        """记录当前 chat 被重渲染。"""
        self.refreshed_chat_count += 1

    def refresh_chat_list(self) -> None:
        """记录聊天列表刷新。"""
        self.refreshed_chat_list_count += 1

    def _finish_current_stream_immediately(self) -> None:
        """记录流式展示被立即补完。"""
        self.finished_stream_count += 1

    def _clear_active_turn(self) -> None:
        """清空活跃轮次状态。"""
        self.active_chat_id = None
        self.active_turn_id = None
        self.active_turn_phase = None


class FakeControllerInputWindow:
    """提供 controller 输入路径所需的最小窗口对象。"""

    def __init__(self) -> None:
        """初始化一条已有运行中轮次的对话。"""
        self.current_chat = Chat(chat_id="chat-1")
        self.current_chat.runtime.running_turn_ids.add("turn-running")
        self.current_chat_id = self.current_chat.chat_id
        self.current_character = FakeCharacter("祥子")
        self.user_input = FakeUserInput("第二句")
        self.chat_display = FakeChatDisplay()
        self.flow_controller = object()
        self.started_turns: list[tuple[str, str, str]] = []
        self.submitted_turns: list[tuple[str, str]] = []
        self.window_titles: list[str] = []

    def setWindowTitle(self, title: str) -> None:
        """记录窗口标题更新。"""
        self.window_titles.append(title)

    def _handle_command_before_send(self, text: str) -> bool:
        """测试对象不拦截普通输入命令。"""
        return False

    def is_chat_busy(self) -> bool:
        """测试对象模拟当前 chat 正忙。"""
        return True

    def _start_active_turn(self, chat_id: str, turn_id: str, phase: str) -> None:
        """记录被标记为 active 的轮次。"""
        self.started_turns.append((chat_id, turn_id, phase))

    def _submit_text_via_controller(self, user_text: str, turn_id: str) -> None:
        """记录提交给 controller 的文本。"""
        self.submitted_turns.append((user_text, turn_id))


class QtUiChatFlowEventsTestCase(unittest.TestCase):
    """验证 Qt 对话窗口消费 chat_flow UI 事件。"""

    def test_running_controller_chat_input_does_not_append_pending_as_message(self) -> None:
        """controller 路径中运行中 chat 的新输入不应被乐观追加为普通消息。"""
        window = FakeControllerInputWindow()

        qtUI.ChatGUI.handle_user_input(window)

        self.assertEqual(window.submitted_turns[0][0], "第二句")
        self.assertEqual(window.chat_display.messages, [])
        self.assertEqual(window.started_turns, [])
        self.assertTrue(window.user_input.was_cleared)

    def test_cancel_active_turn_refreshes_chat_after_audio_queue_cancellation(self) -> None:
        """取消语音队列回填历史后，当前 chat 应重新渲染。"""
        window = FakeCancelWindow(PendingTurnCancellationResult(cancelled_audio_tasks=1))

        qtUI.ChatGUI.cancel_active_turn(window)

        self.assertEqual(window.refreshed_chat_count, 1)
        self.assertEqual(window.finished_stream_count, 1)
        self.assertEqual(window.live2d_client.cancelled_turns, [("chat-1", "turn-1")])
        self.assertIn("turn-1", window.cancelled_turn_ids)

    def test_manual_historical_regeneration_uses_controller_scheduler_path(self) -> None:
        """手动历史音频再生成应走 controller/scheduler，而不是启动旧线程。"""
        controller = FakeHistoricalRegenerationController()
        window = FakeHistoricalRegenerationWindow(controller)

        qtUI.ChatGUI.regenerate_audio(window, 0)

        self.assertEqual(len(controller.calls), 1)
        chat_id, message_index, character, sakiko_state, audio_language_choice = controller.calls[0]
        self.assertEqual(chat_id, "chat-1")
        self.assertEqual(message_index, 0)
        self.assertIs(character, window.character_by_name["祥子"])
        self.assertTrue(sakiko_state)
        self.assertEqual(audio_language_choice, "日英混合")
        self.assertFalse(hasattr(window, "regen_thread"))
        self.assertEqual(window.window_titles, ["正在重新生成音频..."])

    def test_blocked_historical_regeneration_restores_status_without_thread(self) -> None:
        """调度器拒绝历史音频再生成时，UI 应提示原因且不启动旧线程。"""
        result = HistoricalAudioRegenerationResult(
            queued=False,
            status_message="请先终止生成或等待语音任务完成后再重新生成音频。",
        )
        controller = FakeHistoricalRegenerationController(result)
        window = FakeHistoricalRegenerationWindow(controller)

        qtUI.ChatGUI.regenerate_audio(window, 0)

        self.assertEqual(controller.calls[0][0], "chat-1")
        self.assertEqual(window.status_messages, ["请先终止生成或等待语音任务完成后再重新生成音频。"])
        self.assertFalse(hasattr(window, "regen_thread"))
        self.assertEqual(window.window_titles, ["正在重新生成音频...", "数字小祥"])

    def test_historical_audio_completion_refreshes_and_plays_visible_chat(self) -> None:
        """历史音频完成时若目标 chat 仍可见，应刷新当前展示并走前台播放路径。"""
        controller = FakeHistoricalRegenerationController()
        window = FakeHistoricalRegenerationWindow(controller)
        window.current_chat.message_list[0].audio_path = "/tmp/regen.wav"
        event = AudioTaskCompleted(
            task=AudioTask(
                chat_id="chat-1",
                turn_id="regen-test",
                message_index=0,
                text="历史消息",
                character=window.character_by_name["祥子"],
                settings=ChatAudioSettings(True, True, "日英混合"),
                priority=100,
                task_kind="historical_regeneration",
                emotion=window.current_chat.message_list[0].emotion.as_label(),
            ),
            audio_path="/tmp/regen.wav",
            playback_audio_path="/tmp/regen.wav",
            real_audio_generated=True,
            foreground_at_completion=True,
        )

        qtUI.ChatGUI._handle_audio_task_completed(window, event)

        self.assertEqual(window.refresh_current_count, 1)
        self.assertEqual(
            controller.audio_completed_calls,
            [("chat-1", "/tmp/regen.wav", window.current_chat.message_list[0].emotion.as_label(), "历史消息\n历史翻译")],
        )
        self.assertEqual(
            window.live2d_client.play_calls,
            [("/tmp/regen.wav", window.current_chat.message_list[0].emotion.as_label(), "历史消息\n历史翻译")],
        )
        self.assertEqual(window.window_titles, ["数字小祥"])

    def test_historical_audio_completion_after_switch_only_updates_chat_list(self) -> None:
        """历史音频完成时若已切走，不应播放也不应刷新当前聊天框。"""
        controller = FakeHistoricalRegenerationController()
        window = FakeHistoricalRegenerationWindow(controller, current_chat_id="other-chat")
        event = AudioTaskCompleted(
            task=AudioTask(
                chat_id="chat-1",
                turn_id="regen-test",
                message_index=0,
                text="历史消息",
                character=window.character_by_name["祥子"],
                settings=ChatAudioSettings(True, True, "日英混合"),
                priority=100,
                task_kind="historical_regeneration",
                emotion=window.current_chat.message_list[0].emotion.as_label(),
            ),
            audio_path="/tmp/regen.wav",
            playback_audio_path="/tmp/regen.wav",
            real_audio_generated=True,
            foreground_at_completion=False,
        )

        qtUI.ChatGUI._handle_audio_task_completed(window, event)

        self.assertEqual(window.refresh_current_count, 0)
        self.assertEqual(window.refresh_chat_list_count, 1)
        self.assertEqual(controller.audio_completed_calls, [])
        self.assertEqual(window.live2d_client.play_calls, [])


if __name__ == "__main__":
    unittest.main()
