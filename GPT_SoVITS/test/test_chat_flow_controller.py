from __future__ import annotations

import os
import queue as queue_module
import sys
import threading
import unittest
from collections.abc import Callable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from audio_generator import AudioGenerate
from chat.chat import Chat
from chat.chat import Message
from chat.chat import PendingUserTurn
from emotion_enum import EmotionEnum
from chat_flow.audio_scheduler import AudioTask
from chat_flow.audio_scheduler import AudioTaskCompleted
from chat_flow.audio_scheduler import AudioTaskQueued
from chat_flow.audio_scheduler import HistoricalAudioRegenerationResult
from chat_flow.audio_scheduler import PENDING_AUDIO
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.controller import ChatFlowController
from chat_flow.controller import IDLE_STATUS_MESSAGES
from chat_flow.turn_runner import ChatAssistantSegmentCommitted
from chat_flow.turn_runner import ChatPendingUserTurnQueued
from chat_flow.turn_runner import ChatTurnCancelled
from chat_flow.turn_runner import ChatTurnCompleted
from chat_flow.turn_runner import ChatTurnEvent
from chat_flow.turn_runner import ChatTurnFailed
from chat_flow.turn_runner import ChatTurnPhaseChanged
from chat_flow.turn_runner import ChatTurnSettings
from chat_flow.turn_runner import ChatUserMessageCommitted
from chat_flow.turn_runner import CancellationToken


class FakeAudioProcess:
    """测试用语音 worker 进程替身。"""

    def __init__(self) -> None:
        """初始化启动记录。"""
        self.started = False

    def is_alive(self) -> bool:
        """返回测试进程是否已启动。"""
        return self.started

    def start(self) -> None:
        """记录一次启动动作。"""
        self.started = True


class CallbackAudioGenerate(AudioGenerate):
    """避免启动真实 worker 的语音生成测试替身。"""

    def __init__(self) -> None:
        """替换真实进程与进度队列。"""
        super().__init__()
        self.fake_process = FakeAudioProcess()
        self.gptsovits_process = self.fake_process
        self.from_gptsovits_com_queue2: queue_module.Queue[object] = queue_module.Queue()

    def _ensure_worker_dispatch_thread(self) -> None:
        """测试中不启动真实调度线程。"""


class ManualQueuedStatusBridge:
    """测试用手动投递状态信号桥。"""

    def __init__(self) -> None:
        """初始化 handler 与待投递消息列表。"""
        self._handler: Callable[[str], None] | None = None
        self.pending_messages: list[str] = []

    def connect(self, handler: Callable[[str], None]) -> None:
        """记录 controller 注册的状态处理器。"""
        self._handler = handler

    def emit(self, text: str) -> None:
        """模拟跨线程信号先入队、不立刻触碰窗口。"""
        self.pending_messages.append(text)

    def drain(self) -> None:
        """在当前测试线程执行已排队的状态处理器。"""
        if self._handler is None:
            return
        while self.pending_messages:
            self._handler(self.pending_messages.pop(0))


class ManualStatusRestoreScheduler:
    """测试用手动触发状态恢复调度器。"""

    def __init__(self) -> None:
        """初始化待执行恢复回调。"""
        self.calls: list[tuple[int, Callable[[], None]]] = []

    def __call__(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """记录一次延时恢复请求。"""
        self.calls.append((delay_ms, callback))

    def run_next(self) -> None:
        """执行下一条恢复回调。"""
        _delay_ms, callback = self.calls.pop(0)
        callback()


class FakeStatusWindow:
    """记录顶层状态区域收到的消息。"""

    def __init__(self) -> None:
        """初始化消息与线程记录。"""
        self.messages: list[str] = []
        self.thread_ids: list[int] = []

    def handle_messages(self, message: object) -> None:
        """模拟 ChatGUI.handle_messages 更新顶部状态框。"""
        if isinstance(message, str):
            self.messages.append(message)
            self.thread_ids.append(threading.get_ident())


class FakeChatDisplay:
    """记录 controller 对聊天显示端口的调用。"""

    def __init__(self) -> None:
        """初始化显示调用记录。"""
        self.finished_stream_count = 0
        self.rendered_chat_ids: list[str] = []
        self.appended_indices: list[int] = []

    def finish_stream_now(self) -> None:
        """记录立即补完流式文本。"""
        self.finished_stream_count += 1

    def render_chat(self, chat: Chat, preserve_scroll: bool = True) -> None:
        """记录一次完整聊天渲染。"""
        self.rendered_chat_ids.append(chat.chat_id)

    def append_message(
        self,
        message: Message,
        msg_index: int,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """记录追加显示的消息索引。"""
        self.appended_indices.append(msg_index)


class FakeLive2DPlayback:
    """记录 Live2D 前台播放停止请求。"""

    def __init__(self) -> None:
        """初始化停止次数。"""
        self.stop_count = 0

    def stop_playback(self) -> bool:
        """记录一次停止播放请求。"""
        self.stop_count += 1
        return True

    def start_thinking(self) -> bool:
        """兼容 controller 的 thinking 调度端口。"""
        return True

    def stop_thinking(self) -> bool:
        """兼容 controller 的 thinking 调度端口。"""
        return True

    def play_segment(self, *, audio_path: str, emotion: str, display_text: str = "") -> bool:
        """兼容 controller 的播放端口。"""
        return True

    def shutdown(self) -> None:
        """兼容 controller 的关闭端口。"""


class FakeLive2DOrchestrator(FakeLive2DPlayback):
    """记录 controller 对 Live2D thinking 和播放的调度。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        super().__init__()
        self.start_thinking_count = 0
        self.stop_thinking_count = 0
        self.play_calls: list[tuple[str, str, str]] = []

    def start_thinking(self) -> bool:
        """记录开始 thinking。"""
        self.start_thinking_count += 1
        return True

    def stop_thinking(self) -> bool:
        """记录停止 thinking。"""
        self.stop_thinking_count += 1
        return True

    def play_segment(self, *, audio_path: str, emotion: str, display_text: str = "") -> bool:
        """记录播放片段。"""
        self.play_calls.append((audio_path, emotion, display_text))
        return True


class FakeAudioCancellationScheduler:
    """记录 controller 请求取消语音队列的 chat。"""

    def __init__(self, cancelled_count: int = 0) -> None:
        """初始化取消返回值和调用记录。"""
        self.cancelled_count = cancelled_count
        self.cancelled_chat_ids: list[str] = []
        self.started = False
        self.shutdown_calls: list[tuple[bool, float]] = []
        self.queued_messages: list[tuple[str, str, int, str, object, ChatAudioSettings, str]] = []
        self.queued_historical_regenerations: list[tuple[str, str, int, object, ChatAudioSettings]] = []

    def start(self) -> None:
        """记录语音调度器启动。"""
        self.started = True

    def queue_message(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        message_index: int,
        text: str,
        character: object,
        settings: ChatAudioSettings,
        emotion: str = "LABEL_0",
    ) -> AudioTaskQueued:
        """记录被加入语音队列的消息。"""
        self.queued_messages.append((chat_id, turn_id, message_index, text, character, settings, emotion))
        return AudioTaskQueued(task=AudioTask(
            chat_id=chat_id,
            turn_id=turn_id,
            message_index=message_index,
            text=text,
            character=character,
            settings=settings,
            emotion=emotion,
        ))

    def queue_historical_regeneration(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        message_index: int,
        character: object,
        settings: ChatAudioSettings,
    ) -> HistoricalAudioRegenerationResult:
        """记录被加入语音队列的历史音频再生成请求。"""
        self.queued_historical_regenerations.append((chat_id, turn_id, message_index, character, settings))
        return HistoricalAudioRegenerationResult(
            queued=True,
            event=AudioTaskQueued(task=AudioTask(
                chat_id=chat_id,
                turn_id=turn_id,
                message_index=message_index,
                text=chat.message_list[message_index].text,
                character=character,
                settings=settings,
                emotion=chat.message_list[message_index].emotion.as_label(),
                priority=100,
                task_kind="historical_regeneration",
            )),
        )

    def cancel_queued_tasks_for_chat(self, chat_id: str) -> int:
        """记录被取消语音队列的 chat id。"""
        self.cancelled_chat_ids.append(chat_id)
        return self.cancelled_count

    def shutdown(self, *, cancel_background: bool = True, timeout_seconds: float = 0.0) -> int:
        """记录调度器关闭请求。"""
        self.shutdown_calls.append((cancel_background, timeout_seconds))
        return self.cancelled_count


class FakeGenerationSession:
    """记录 controller 提交的一轮前台文本生成。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[dict[str, object]] = []

    def run_foreground_turn(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """记录调用并返回完成事件。"""
        self.calls.append({
            "chat": chat,
            "chat_id": chat_id,
            "turn_id": turn_id,
            "user_text": user_text,
            "character_name": character_name,
            "settings": settings,
        })
        return [ChatTurnCompleted(chat_id=chat_id, turn_id=turn_id)]


class FakeChatManagerForShutdown:
    """记录 controller shutdown 过程中的保存动作。"""

    def __init__(self, chat_list: list[Chat]) -> None:
        """保存待管理的 chat 列表。"""
        self.chat_list = chat_list
        self.saved = False

    def save(self) -> None:
        """记录一次保存。"""
        self.saved = True


class FakeAudioWorkerForShutdown:
    """记录 controller 对语音 worker 的关闭请求。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.timeout_seconds: float | None = None

    def shutdown_worker(self, timeout_seconds: float = 0.0) -> None:
        """记录关闭语音 worker 的超时时间。"""
        self.timeout_seconds = timeout_seconds


class RecordingGenerationSession:
    """以可重入方式记录 controller 串行调度顺序。"""

    def __init__(self) -> None:
        """初始化调用记录和可选回调。"""
        self.calls: list[str] = []
        self.on_first_run: Callable[[], None] | None = None

    def run_foreground_turn(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """模拟一轮同步生成，并允许首轮运行中提交下一条消息。"""
        self.calls.append(user_text)
        if len(self.calls) == 1 and self.on_first_run is not None:
            self.on_first_run()
        with chat.runtime.lock:
            chat.add_message(Message("User", user_text, "", EmotionEnum.HAPPINESS, ""))
            chat.add_message(Message(character_name, f"回复：{user_text}", "", EmotionEnum.HAPPINESS, "NO_AUDIO"))
        return [ChatTurnCompleted(chat_id=chat_id, turn_id=turn_id)]


class CancellingGenerationSession:
    """在运行中触发 controller 取消当前可见 chat。"""

    def __init__(self, cancel_callback: Callable[[], None]) -> None:
        """保存取消回调。"""
        self._cancel_callback = cancel_callback
        self.observed_token: CancellationToken | None = None

    def run_foreground_turn(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """记录 token，触发取消，并返回取消事件。"""
        self.observed_token = settings.cancellation_token
        self._cancel_callback()
        return [ChatTurnCancelled(chat_id=chat_id, turn_id=turn_id)]


class ChatFlowControllerTestCase(unittest.TestCase):
    """验证 chat flow controller 的状态信号桥行为。"""

    def test_status_notifier_updates_attached_window_after_signal_delivery(self) -> None:
        """状态 notifier 应经由信号桥投递到窗口状态处理器。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(window=window, signal_bridge=bridge)

        controller.status_notifier.notify("正在合成语音...")

        self.assertEqual(window.messages, [])
        bridge.drain()
        self.assertEqual(window.messages, ["正在合成语音..."])

    def test_status_emitted_from_background_thread_reaches_window_on_main_delivery(self) -> None:
        """后台线程发出的状态应在主线程投递阶段更新窗口。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(window=window, signal_bridge=bridge)
        main_thread_id = threading.get_ident()
        background_thread_ids: list[int] = []

        def emit_from_background() -> None:
            """从非 UI 测试线程发出状态。"""
            background_thread_ids.append(threading.get_ident())
            controller.status_notifier.notify("整理语言...")

        worker = threading.Thread(target=emit_from_background)
        worker.start()
        worker.join()

        self.assertNotEqual(background_thread_ids, [main_thread_id])
        self.assertEqual(window.messages, [])
        bridge.drain()
        self.assertEqual(window.messages, ["整理语言..."])
        self.assertEqual(window.thread_ids, [main_thread_id])

    def test_audio_generator_initializes_with_status_callback(self) -> None:
        """语音生成器初始化时应可使用 status callback 接收进度。"""
        audio_generator = CallbackAudioGenerate()
        status_messages: list[str] = []

        audio_generator.initialize([], status_callback=status_messages.append)
        audio_generator.from_gptsovits_com_queue2.put({
            "type": "progress",
            "message": "整理语言...\n1/4...",
        })
        audio_generator._drain_progress_messages()

        self.assertTrue(audio_generator.fake_process.started)
        self.assertIsNone(audio_generator.message_queue)
        self.assertEqual(status_messages, ["整理语言...\n1/4..."])

    def test_temporary_status_restores_idle_text_when_scheduler_fires(self) -> None:
        """临时状态应在恢复调度触发后回到空闲文案。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        restore_scheduler = ManualStatusRestoreScheduler()
        controller = ChatFlowController(
            window=window,
            signal_bridge=bridge,
            status_restore_scheduler=restore_scheduler,
        )

        controller.notify_temporary_status("失败", restore_ms=3000)
        bridge.drain()
        restore_scheduler.run_next()
        bridge.drain()

        self.assertEqual(window.messages[0], "失败")
        self.assertIn(window.messages[1], IDLE_STATUS_MESSAGES)
        self.assertEqual(restore_scheduler.calls, [])

    def test_stale_temporary_status_restore_does_not_override_new_status(self) -> None:
        """旧临时状态的恢复回调不应覆盖后续新状态。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        restore_scheduler = ManualStatusRestoreScheduler()
        controller = ChatFlowController(
            window=window,
            signal_bridge=bridge,
            status_restore_scheduler=restore_scheduler,
        )

        controller.notify_temporary_status("失败", restore_ms=3000)
        controller.notify_status("祥子思考中...")
        bridge.drain()
        restore_scheduler.run_next()
        bridge.drain()

        self.assertEqual(window.messages, ["失败", "祥子思考中..."])

    def test_submit_foreground_text_turn_delegates_to_generation_session(self) -> None:
        """前台文本提交应通过 controller 进入 generation session。"""
        bridge = ManualQueuedStatusBridge()
        generation_session = FakeGenerationSession()
        controller = ChatFlowController(signal_bridge=bridge, generation_session=generation_session)
        chat = Chat()
        settings = ChatTurnSettings(audio_enabled=False, audio_language_choice="日英混合")

        events = controller.submit_foreground_text_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="你好",
            character_name="祥子",
            settings=settings,
        )

        self.assertEqual(events, [ChatTurnCompleted(chat_id=chat.chat_id, turn_id="turn-1")])
        self.assertEqual(generation_session.calls[0]["chat"], chat)
        self.assertEqual(generation_session.calls[0]["user_text"], "你好")

    def test_submit_to_running_chat_queues_pending_user_turn(self) -> None:
        """已有运行中轮次时，后续输入应进入非持久 pending state。"""
        bridge = ManualQueuedStatusBridge()
        generation_session = FakeGenerationSession()
        controller = ChatFlowController(signal_bridge=bridge, generation_session=generation_session)
        chat = Chat()
        chat.runtime.running_turn_ids.add("running-turn")

        events = controller.submit_foreground_text_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-2",
            user_text="第二句",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="日英混合"),
        )

        self.assertEqual(len(chat.runtime.running_turn_ids), 1)
        self.assertEqual(chat.runtime.pending_user_turns, [PendingUserTurn(turn_id="turn-2", text="第二句")])
        self.assertEqual(chat.message_list, [])
        self.assertEqual(generation_session.calls, [])
        self.assertEqual(events[0].text, "第二句")

    def test_pending_user_turn_event_rerenders_visible_chat(self) -> None:
        """可见 chat 收到 pending 输入事件时，应重渲染以显示非持久 pending 文本。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        display = FakeChatDisplay()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatPendingUserTurnQueued(chat_id=chat.chat_id, turn_id="turn-2", text="第二句")],
            chat=chat,
            display=display,
        )

        self.assertEqual(display.rendered_chat_ids, [chat.chat_id])
        self.assertEqual(display.appended_indices, [])

    def test_user_message_committed_event_rerenders_visible_chat(self) -> None:
        """pending 输入真正提交为历史消息时，应重渲染可见 chat。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat(message_list=[
            Message("User", "第二句", "", EmotionEnum.HAPPINESS, ""),
        ])
        display = FakeChatDisplay()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [
                ChatUserMessageCommitted(
                    chat_id=chat.chat_id,
                    turn_id="turn-2",
                    message_index=0,
                    text="第二句",
                )
            ],
            chat=chat,
            display=display,
        )

        self.assertEqual(display.rendered_chat_ids, [chat.chat_id])
        self.assertEqual(display.appended_indices, [])

    def test_pending_turn_starts_after_running_turn_completes(self) -> None:
        """运行中提交的 pending turn 应在当前轮结束后再写入历史。"""
        bridge = ManualQueuedStatusBridge()
        generation_session = RecordingGenerationSession()
        controller = ChatFlowController(signal_bridge=bridge, generation_session=generation_session)
        chat = Chat()
        settings = ChatTurnSettings(audio_enabled=False, audio_language_choice="日英混合")

        def queue_second_turn() -> None:
            """在首轮仍运行时提交第二句。"""
            controller.submit_foreground_text_turn(
                chat=chat,
                chat_id=chat.chat_id,
                turn_id="turn-2",
                user_text="B",
                character_name="祥子",
                settings=settings,
            )

        generation_session.on_first_run = queue_second_turn

        controller.submit_foreground_text_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="A",
            character_name="祥子",
            settings=settings,
        )

        self.assertEqual(generation_session.calls, ["A", "B"])
        self.assertEqual([message.text for message in chat.message_list], ["A", "回复：A", "B", "回复：B"])
        self.assertEqual(chat.runtime.pending_user_turns, [])
        self.assertEqual(chat.runtime.running_turn_ids, set())

    def test_cancel_single_pending_turn_restores_text(self) -> None:
        """取消单条未开始 pending 输入时，应返回可恢复到输入框的文本。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id="turn-1", text="待修改"))

        result = controller.cancel_pending_user_turns(chat)

        self.assertEqual(result.restored_text, "待修改")
        self.assertEqual(result.status_message, "")
        self.assertEqual(chat.runtime.pending_user_turns, [])

    def test_cancel_multiple_pending_turns_emits_status(self) -> None:
        """取消多条未开始 pending 输入时，应清空并给出状态消息。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id="turn-1", text="A"))
        chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id="turn-2", text="B"))

        result = controller.cancel_pending_user_turns(chat)

        self.assertEqual(result.restored_text, "")
        self.assertIn("已清除 2 条排队消息", result.status_message)
        self.assertEqual(chat.runtime.pending_user_turns, [])

    def test_switch_chat_while_running_finishes_stream_stops_playback_and_renders_new_chat(self) -> None:
        """切换 chat 时应允许旧 chat 继续运行，并渲染新 chat。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        old_chat = Chat()
        new_chat = Chat()
        old_chat.runtime.running_turn_ids.add("turn-1")
        display = FakeChatDisplay()
        live2d = FakeLive2DPlayback()

        controller.switch_visible_chat(
            current_chat=old_chat,
            next_chat=new_chat,
            display=display,
            live2d_client=live2d,
        )

        self.assertEqual(display.finished_stream_count, 1)
        self.assertEqual(live2d.stop_count, 1)
        self.assertEqual(controller.visible_chat_id, new_chat.chat_id)
        self.assertEqual(display.rendered_chat_ids, [new_chat.chat_id])
        self.assertEqual(old_chat.runtime.running_turn_ids, {"turn-1"})

    def test_background_turn_event_does_not_append_to_visible_display(self) -> None:
        """非当前可见 chat 的事件不应追加到当前显示框。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        visible_chat = Chat()
        background_chat = Chat(message_list=[
            Message("祥子", "后台完成", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        display = FakeChatDisplay()
        controller.set_visible_chat(visible_chat.chat_id)

        controller.handle_turn_events(
            [
                ChatAssistantSegmentCommitted(
                    chat_id=background_chat.chat_id,
                    turn_id="turn-bg",
                    message_index=0,
                    character_name="祥子",
                    text="后台完成",
                    translation="",
                    emotion="LABEL_1",
                    audio_path="NO_AUDIO",
                )
            ],
            chat=background_chat,
            display=display,
        )

        self.assertEqual(display.appended_indices, [])

    def test_switching_back_renders_completed_background_history_without_replay(self) -> None:
        """切回后台 chat 时应重渲染历史，而不是追加重播旧片段。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        old_chat = Chat()
        old_chat.message_list.append(Message("祥子", "后台完成", "", EmotionEnum.HAPPINESS, "NO_AUDIO"))
        current_chat = Chat()
        display = FakeChatDisplay()
        live2d = FakeLive2DPlayback()
        controller.set_visible_chat(current_chat.chat_id)

        controller.switch_visible_chat(
            current_chat=current_chat,
            next_chat=old_chat,
            display=display,
            live2d_client=live2d,
        )

        self.assertEqual(display.rendered_chat_ids, [old_chat.chat_id])
        self.assertEqual(display.appended_indices, [])

    def test_cancel_visible_chat_marks_running_turn_token_and_clears_pending(self) -> None:
        """取消当前可见 chat 应标记运行 token，并按 pending 规则清理排队输入。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        controller.set_visible_chat(chat.chat_id)

        def cancel_visible() -> None:
            """在 fake generation session 中模拟用户点击停止。"""
            chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id="pending-1", text="待取消"))
            controller.cancel_visible_chat(chat)

        generation_session = CancellingGenerationSession(cancel_visible)
        controller.attach_generation_session(generation_session)

        events = controller.submit_foreground_text_turn(
            chat=chat,
            chat_id=chat.chat_id,
            turn_id="turn-1",
            user_text="开始",
            character_name="祥子",
            settings=ChatTurnSettings(audio_enabled=False, audio_language_choice="中英混合"),
        )

        self.assertIsNotNone(generation_session.observed_token)
        self.assertTrue(generation_session.observed_token.is_cancelled())
        self.assertEqual(chat.runtime.pending_user_turns, [])
        self.assertIsInstance(events[-1], ChatTurnCancelled)

    def test_cancel_visible_chat_cancels_queued_audio_for_visible_chat(self) -> None:
        """取消当前可见 chat 应同时取消该 chat 尚未开始的语音任务。"""
        audio_scheduler = FakeAudioCancellationScheduler(cancelled_count=2)
        controller = ChatFlowController(
            signal_bridge=ManualQueuedStatusBridge(),
            audio_scheduler=audio_scheduler,
        )
        chat = Chat()
        controller.set_visible_chat(chat.chat_id)

        result = controller.cancel_visible_chat(chat)

        self.assertEqual(audio_scheduler.cancelled_chat_ids, [chat.chat_id])
        self.assertEqual(result.cancelled_audio_tasks, 2)

    def test_assistant_segment_with_pending_audio_is_queued_for_scheduler(self) -> None:
        """已提交的 pending audio 片段应只进语音队列，暂不展示。"""
        audio_scheduler = FakeAudioCancellationScheduler()
        controller = ChatFlowController(
            signal_bridge=ManualQueuedStatusBridge(),
            audio_scheduler=audio_scheduler,
        )
        chat = Chat(message_list=[
            Message("祥子", "こんにちは", "", EmotionEnum.HAPPINESS, PENDING_AUDIO)
        ])
        display = FakeChatDisplay()
        controller.set_visible_chat(chat.chat_id)
        settings = ChatTurnSettings(
            audio_enabled=True,
            audio_language_choice="日英混合",
            sakiko_state=False,
            character="祥子对象",
        )
        controller.track_running_turn(chat.chat_id, "turn-1", CancellationToken())
        controller._audio_turn_requests[(chat.chat_id, "turn-1")] = controller._build_audio_turn_request(
            character_name="祥子",
            settings=settings,
        )

        controller.handle_turn_events(
            [
                ChatAssistantSegmentCommitted(
                    chat_id=chat.chat_id,
                    turn_id="turn-1",
                    message_index=0,
                    character_name="祥子",
                    text="こんにちは",
                    translation="",
                    emotion="LABEL_1",
                    audio_path=PENDING_AUDIO,
                )
            ],
            chat=chat,
            display=display,
            live2d_client=FakeLive2DOrchestrator(),
        )

        self.assertTrue(audio_scheduler.started)
        self.assertEqual(display.appended_indices, [])
        self.assertEqual(
            audio_scheduler.queued_messages,
            [(
                chat.chat_id,
                "turn-1",
                0,
                "こんにちは",
                "祥子对象",
                ChatAudioSettings(True, False, "日英混合"),
                "LABEL_1",
            )],
        )

    def test_historical_audio_regeneration_delegates_to_scheduler(self) -> None:
        """历史音频再生成应通过 controller 委托给统一语音调度器。"""
        audio_scheduler = FakeAudioCancellationScheduler()
        controller = ChatFlowController(
            signal_bridge=ManualQueuedStatusBridge(),
            audio_scheduler=audio_scheduler,
        )
        chat = Chat(message_list=[
            Message("祥子", "历史消息", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])

        result = controller.queue_historical_audio_regeneration(
            chat=chat,
            chat_id=chat.chat_id,
            message_index=0,
            character="祥子对象",
            sakiko_state=True,
            audio_language_choice="日英混合",
        )

        self.assertTrue(result.queued)
        self.assertEqual(len(audio_scheduler.queued_historical_regenerations), 1)
        chat_id, turn_id, message_index, character, settings = audio_scheduler.queued_historical_regenerations[0]
        self.assertEqual(chat_id, chat.chat_id)
        self.assertTrue(turn_id.startswith("regen-"))
        self.assertEqual(message_index, 0)
        self.assertEqual(character, "祥子对象")
        self.assertEqual(settings, ChatAudioSettings(True, True, "日英混合"))

    def test_cancel_visible_chat_does_not_cancel_background_chat(self) -> None:
        """取消当前可见 chat 不应影响后台 chat 的运行 token。"""
        audio_scheduler = FakeAudioCancellationScheduler(cancelled_count=1)
        controller = ChatFlowController(
            signal_bridge=ManualQueuedStatusBridge(),
            audio_scheduler=audio_scheduler,
        )
        visible_chat = Chat()
        background_chat = Chat()
        visible_token = CancellationToken()
        background_token = CancellationToken()
        visible_chat.runtime.running_turn_ids.add("visible-turn")
        background_chat.runtime.running_turn_ids.add("background-turn")
        controller.track_running_turn(visible_chat.chat_id, "visible-turn", visible_token)
        controller.track_running_turn(background_chat.chat_id, "background-turn", background_token)
        controller.set_visible_chat(visible_chat.chat_id)

        controller.cancel_visible_chat(visible_chat)

        self.assertTrue(visible_token.is_cancelled())
        self.assertFalse(background_token.is_cancelled())
        self.assertEqual(audio_scheduler.cancelled_chat_ids, [visible_chat.chat_id])

        result = controller.cancel_visible_chat(background_chat)

        self.assertEqual(result.cancelled_audio_tasks, 0)
        self.assertEqual(audio_scheduler.cancelled_chat_ids, [visible_chat.chat_id])

    def test_shutdown_centralizes_audio_live2d_worker_and_final_save(self) -> None:
        """controller shutdown 应归一化 pending audio 并统一关闭依赖后保存。"""
        audio_scheduler = FakeAudioCancellationScheduler(cancelled_count=1)
        controller = ChatFlowController(
            signal_bridge=ManualQueuedStatusBridge(),
            audio_scheduler=audio_scheduler,
        )
        chat = Chat(message_list=[
            Message("祥子", "待清理", "", EmotionEnum.HAPPINESS, PENDING_AUDIO)
        ])
        manager = FakeChatManagerForShutdown([chat])
        live2d = FakeLive2DPlayback()
        audio_worker = FakeAudioWorkerForShutdown()

        controller.shutdown(
            chat_manager=manager,
            live2d_client=live2d,
            audio_worker=audio_worker,
            timeout_seconds=0.25,
        )

        self.assertEqual(chat.message_list[0].audio_path, "NO_AUDIO")
        self.assertEqual(audio_scheduler.shutdown_calls, [(True, 0.25)])
        self.assertEqual(audio_worker.timeout_seconds, 0.25)
        self.assertTrue(manager.saved)

    def test_visible_failure_updates_status_but_background_failure_does_not(self) -> None:
        """只有前台失败事件应更新可见状态。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(
            window=window,
            signal_bridge=bridge,
            status_restore_scheduler=ManualStatusRestoreScheduler(),
        )
        visible_chat = Chat()
        background_chat = Chat()
        display = FakeChatDisplay()
        controller.set_visible_chat(visible_chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnFailed(chat_id=background_chat.chat_id, turn_id="bg", message="后台失败")],
            chat=background_chat,
            display=display,
        )
        bridge.drain()
        self.assertEqual(window.messages, [])

        controller.handle_turn_events(
            [ChatTurnFailed(chat_id=visible_chat.chat_id, turn_id="fg", message="前台失败")],
            chat=visible_chat,
            display=display,
        )
        bridge.drain()
        self.assertEqual(window.messages, ["前台失败"])

    def test_foreground_llm_phase_updates_status_with_character_name(self) -> None:
        """前台 LLM 阶段应在状态栏显示角色思考中。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(window=window, signal_bridge=bridge)
        chat = Chat(name="祥子")
        display = FakeChatDisplay()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnPhaseChanged(chat_id=chat.chat_id, turn_id="turn-1", phase="llm")],
            chat=chat,
            display=display,
        )
        bridge.drain()

        self.assertEqual(window.messages, ["祥子思考中..."])

    def test_foreground_completion_restores_idle_status(self) -> None:
        """前台对话完成后应恢复顶部空闲文案。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(window=window, signal_bridge=bridge)
        chat = Chat()
        display = FakeChatDisplay()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnCompleted(chat_id=chat.chat_id, turn_id="turn-1")],
            chat=chat,
            display=display,
        )
        bridge.drain()

        self.assertEqual(len(window.messages), 1)
        self.assertIn(window.messages[0], IDLE_STATUS_MESSAGES)

    def test_foreground_llm_phase_starts_and_segment_stops_live2d_thinking(self) -> None:
        """前台 LLM 阶段应开始 thinking，首个可见片段应停止 thinking。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat(message_list=[
            Message("祥子", "可见片段", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        display = FakeChatDisplay()
        live2d = FakeLive2DOrchestrator()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnPhaseChanged(chat_id=chat.chat_id, turn_id="turn-1", phase="llm")],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )
        controller.handle_turn_events(
            [
                ChatAssistantSegmentCommitted(
                    chat_id=chat.chat_id,
                    turn_id="turn-1",
                    message_index=0,
                    character_name="祥子",
                    text="可见片段",
                    translation="",
                    emotion="LABEL_0",
                    audio_path="NO_AUDIO",
                )
            ],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )

        self.assertEqual(live2d.start_thinking_count, 1)
        self.assertEqual(live2d.stop_thinking_count, 1)

    def test_foreground_completion_stops_live2d_thinking(self) -> None:
        """前台 LLM 阶段直接完成时也应停止 thinking。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        display = FakeChatDisplay()
        live2d = FakeLive2DOrchestrator()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnPhaseChanged(chat_id=chat.chat_id, turn_id="turn-1", phase="llm")],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )
        controller.handle_turn_events(
            [ChatTurnCompleted(chat_id=chat.chat_id, turn_id="turn-1")],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )

        self.assertEqual(live2d.start_thinking_count, 1)
        self.assertEqual(live2d.stop_thinking_count, 1)

    def test_foreground_failure_stops_live2d_thinking(self) -> None:
        """前台 LLM 阶段失败时应停止 thinking 并通知状态。"""
        bridge = ManualQueuedStatusBridge()
        window = FakeStatusWindow()
        controller = ChatFlowController(
            window=window,
            signal_bridge=bridge,
            status_restore_scheduler=ManualStatusRestoreScheduler(),
        )
        chat = Chat()
        display = FakeChatDisplay()
        live2d = FakeLive2DOrchestrator()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_turn_events(
            [ChatTurnPhaseChanged(chat_id=chat.chat_id, turn_id="turn-1", phase="llm")],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )
        controller.handle_turn_events(
            [ChatTurnFailed(chat_id=chat.chat_id, turn_id="turn-1", message="失败")],
            chat=chat,
            display=display,
            live2d_client=live2d,
        )
        bridge.drain()

        self.assertEqual(live2d.start_thinking_count, 1)
        self.assertEqual(live2d.stop_thinking_count, 1)
        self.assertEqual(window.messages, ["角色思考中...", "失败"])

    def test_switching_to_chat_in_llm_phase_starts_thinking_and_switching_away_stops(self) -> None:
        """切到 LLM 阶段 chat 应开始 thinking，切走应停止。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        old_chat = Chat()
        next_chat = Chat()
        display = FakeChatDisplay()
        live2d = FakeLive2DOrchestrator()
        controller.mark_turn_phase(next_chat.chat_id, "turn-1", "llm")

        controller.switch_visible_chat(
            current_chat=old_chat,
            next_chat=next_chat,
            display=display,
            live2d_client=live2d,
        )
        controller.switch_visible_chat(
            current_chat=next_chat,
            next_chat=old_chat,
            display=display,
            live2d_client=live2d,
        )

        self.assertEqual(live2d.start_thinking_count, 1)
        self.assertGreaterEqual(live2d.stop_thinking_count, 1)

    def test_foreground_audio_completion_drives_live2d_playback(self) -> None:
        """前台音频完成应驱动 Live2D 文本、音频和情绪播放。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        chat = Chat()
        live2d = FakeLive2DOrchestrator()
        controller.set_visible_chat(chat.chat_id)

        controller.handle_audio_completed(
            chat_id=chat.chat_id,
            audio_path="/tmp/voice.wav",
            emotion="LABEL_0",
            display_text="こんにちは",
            live2d_client=live2d,
        )

        self.assertEqual(live2d.play_calls, [("/tmp/voice.wav", "LABEL_0", "こんにちは")])

    def test_background_text_and_audio_do_not_touch_live2d(self) -> None:
        """后台文本或音频事件不应更新 Live2D。"""
        controller = ChatFlowController(signal_bridge=ManualQueuedStatusBridge())
        visible_chat = Chat()
        background_chat = Chat(message_list=[
            Message("祥子", "后台片段", "", EmotionEnum.HAPPINESS, "NO_AUDIO")
        ])
        display = FakeChatDisplay()
        live2d = FakeLive2DOrchestrator()
        controller.set_visible_chat(visible_chat.chat_id)

        controller.handle_turn_events(
            [
                ChatTurnPhaseChanged(chat_id=background_chat.chat_id, turn_id="bg", phase="llm"),
                ChatAssistantSegmentCommitted(
                    chat_id=background_chat.chat_id,
                    turn_id="bg",
                    message_index=0,
                    character_name="祥子",
                    text="后台片段",
                    translation="",
                    emotion="LABEL_0",
                    audio_path="NO_AUDIO",
                ),
            ],
            chat=background_chat,
            display=display,
            live2d_client=live2d,
        )
        controller.handle_audio_completed(
            chat_id=background_chat.chat_id,
            audio_path="/tmp/bg.wav",
            emotion="LABEL_0",
            display_text="后台",
            live2d_client=live2d,
        )

        self.assertEqual(live2d.start_thinking_count, 0)
        self.assertEqual(live2d.stop_thinking_count, 0)
        self.assertEqual(live2d.play_calls, [])


if __name__ == "__main__":
    unittest.main()
