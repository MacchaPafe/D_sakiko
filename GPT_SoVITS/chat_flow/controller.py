from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
import random
import uuid
from typing import Protocol
from typing import cast

from PyQt5.QtCore import QObject
from PyQt5.QtCore import Qt
from PyQt5.QtCore import QTimer
from PyQt5.QtCore import pyqtSignal

from chat.chat import Chat
from chat.chat import Message
from chat.chat import PendingUserTurn
from chat_flow.audio_scheduler import AudioTaskQueued
from chat_flow.audio_scheduler import HistoricalAudioRegenerationResult
from chat_flow.audio_scheduler import PENDING_AUDIO
from chat_flow.audio_scheduler import normalize_pending_audio_on_startup
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.turn_runner import ChatAssistantSegmentCommitted
from chat_flow.turn_runner import ChatPendingUserTurnQueued
from chat_flow.turn_runner import ChatToolCallRecordsUpdated
from chat_flow.turn_runner import ChatTurnCancelled
from chat_flow.turn_runner import ChatTurnCompleted
from chat_flow.turn_runner import ChatTurnEvent
from chat_flow.turn_runner import ChatTurnFailed
from chat_flow.turn_runner import ChatTurnPhaseChanged
from chat_flow.turn_runner import ChatTurnSettings
from chat_flow.turn_runner import ChatUserMessageCommitted
from chat_flow.turn_runner import CancellationToken


class StatusMessageWindow(Protocol):
    """描述可接收顶部状态消息的窗口接口。"""

    def handle_messages(self, message: object) -> None:
        """处理一条 UI 消息。"""


class StatusSignalBridge(Protocol):
    """描述 controller 使用的线程安全状态信号桥。"""

    def connect(self, handler: Callable[[str], None]) -> None:
        """连接 Qt 主线程上的状态处理器。"""

    def emit(self, text: str) -> None:
        """从任意线程发出一条状态文本。"""


class ForegroundGenerationSession(Protocol):
    """描述 controller 调用的前台文本生成会话接口。"""

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
        """执行一轮前台文本生成。"""


class ChatDisplayPort(Protocol):
    """描述 controller 需要的聊天显示端口。"""

    def finish_stream_now(self) -> None:
        """立即补完当前流式文本。"""

    def render_chat(self, chat: Chat, preserve_scroll: bool = True) -> None:
        """完整渲染一个 chat。"""

    def append_message(
        self,
        message: Message,
        msg_index: int,
        *,
        stream: bool = False,
        interval_ms: int = 30,
    ) -> None:
        """追加渲染一条消息。"""


class Live2DPlaybackPort(Protocol):
    """描述切换前台 chat 时需要的 Live2D 播放控制端口。"""

    def stop_playback(self) -> bool:
        """停止当前前台播放。"""

    def start_thinking(self) -> bool:
        """开始前台 thinking 动画。"""

    def stop_thinking(self) -> bool:
        """停止前台 thinking 动画。"""

    def play_segment(self, *, audio_path: str, emotion: str, display_text: str = "") -> bool:
        """播放前台音频片段。"""


class Live2DShutdownPort(Protocol):
    """描述 controller 关闭 Live2D 展示通道所需的端口。"""

    def stop_playback(self) -> bool:
        """停止当前前台播放。"""

    def stop_thinking(self) -> bool:
        """停止当前 thinking 动画。"""

    def shutdown(self) -> None:
        """关闭 Live2D 通道。"""


class AudioSchedulerPort(Protocol):
    """描述 controller 所需的语音调度器端口。"""

    def start(self) -> None:
        """启动后台语音调度。"""

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
        """把一条已提交消息加入语音调度队列。"""

    def cancel_queued_tasks_for_chat(self, chat_id: str) -> int:
        """取消指定 chat 尚未开始的语音任务。"""

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
        """把一条历史消息的音频再生成加入语音调度队列。"""

    def shutdown(self, *, cancel_background: bool = True, timeout_seconds: float = 0.0) -> int:
        """关闭调度器并归一化未完成语音任务。"""


StatusRestoreScheduler = Callable[[int, Callable[[], None]], None]
IDLE_STATUS_MESSAGES: tuple[str, ...] = ("...", "...", "就绪", "...")


class AudioWorkerShutdownPort(Protocol):
    """描述 controller 关闭语音 worker 所需的端口。"""

    def shutdown_worker(self, timeout_seconds: float = 0.0) -> None:
        """关闭语音 worker。"""


class ChatManagerSavePort(Protocol):
    """描述 controller 保存聊天记录所需的管理器端口。"""

    chat_list: list[Chat]

    def save(self) -> None:
        """保存聊天记录。"""


class _BoundStatusSignal(Protocol):
    """描述 PyQt bound signal 的最小接口。"""

    def connect(self, handler: Callable[[str], None], connection_type: object | None = None) -> object:
        """连接一个 signal slot。"""

    def emit(self, text: str) -> None:
        """发出一条字符串信号。"""


@dataclass(frozen=True)
class PendingTurnRequest:
    """保存一条排队用户输入恢复执行所需的快照。"""

    character_name: str
    settings: ChatTurnSettings


@dataclass(frozen=True)
class AudioTurnRequest:
    """保存一轮文本生成对应的语音调度快照。"""

    character: object
    settings: ChatAudioSettings


@dataclass(frozen=True)
class PendingTurnCancellationResult:
    """描述取消 pending 用户输入后的 UI 恢复建议。"""

    restored_text: str = ""
    status_message: str = ""
    cancelled_audio_tasks: int = 0


class QtStatusSignalBridge:
    """用 PyQt signal 把后台状态投递回 Qt 事件循环。"""

    def __init__(self, parent: object | None = None) -> None:
        """创建承载状态 signal 的 Qt 对象。"""
        class _QtStatusEmitter(QObject):
            """承载状态文本 signal 的 Qt 对象。"""
            status_signal = pyqtSignal(str)

        if parent is None:
            self._emitter = _QtStatusEmitter()
        else:
            self._emitter = _QtStatusEmitter(parent)

    def connect(self, handler: Callable[[str], None]) -> None:
        """以 queued connection 连接状态处理器。"""
        signal = cast(_BoundStatusSignal, self._emitter.status_signal)
        try:
            signal.connect(handler, Qt.QueuedConnection)
        except TypeError:
            signal.connect(handler)

    def emit(self, text: str) -> None:
        """发出一条跨线程安全状态文本。"""
        signal = cast(_BoundStatusSignal, self._emitter.status_signal)
        signal.emit(text)


class ChatFlowStatusNotifier:
    """提供给后台组件调用的状态通知器。"""

    def __init__(self, emit_status: Callable[[str], None]) -> None:
        """保存状态投递函数。"""
        self._emit_status = emit_status

    def notify(self, text: str) -> None:
        """投递一条状态文本。"""
        self._emit_status(text)

    def __call__(self, text: str) -> None:
        """以回调形式投递一条状态文本。"""
        self.notify(text)


class ChatFlowController:
    """作为 Qt 主线程侧的聊天流程协调入口。"""

    def __init__(
        self,
        window: StatusMessageWindow | None = None,
        *,
        signal_bridge: StatusSignalBridge | None = None,
        generation_session: ForegroundGenerationSession | None = None,
        audio_scheduler: AudioSchedulerPort | None = None,
        status_restore_scheduler: StatusRestoreScheduler | None = None,
        qt_parent: object | None = None,
    ) -> None:
        """创建 controller 并连接状态信号桥。"""
        self._window = window
        self._signal_bridge = signal_bridge or QtStatusSignalBridge(qt_parent)
        self._status_notifier = ChatFlowStatusNotifier(self.notify_status)
        self._generation_session = generation_session
        self._audio_scheduler = audio_scheduler
        self._pending_turn_requests: dict[tuple[str, str], PendingTurnRequest] = {}
        self._audio_turn_requests: dict[tuple[str, str], AudioTurnRequest] = {}
        self._running_turn_tokens: dict[tuple[str, str], CancellationToken] = {}
        self._turn_phases: dict[tuple[str, str], str] = {}
        self._visible_chat_id = ""
        self._accepting_turns = True
        self._status_revision = 0
        self._status_restore_scheduler = status_restore_scheduler or self._schedule_status_restore
        self._signal_bridge.connect(self._handle_status)
        if self._audio_scheduler is not None:
            self._audio_scheduler.start()

    @property
    def status_notifier(self) -> ChatFlowStatusNotifier:
        """返回可交给后台组件使用的状态通知器。"""
        return self._status_notifier

    @property
    def status_callback(self) -> Callable[[str], None]:
        """返回可交给旧式回调参数使用的状态函数。"""
        return self._status_notifier.notify

    @property
    def visible_chat_id(self) -> str:
        """返回当前 controller 认为可见的 chat id。"""
        return self._visible_chat_id

    def is_foreground_chat(self, chat_id: str) -> bool:
        """返回指定 chat 是否是当前前台 chat。"""
        return bool(chat_id) and chat_id == self._visible_chat_id

    def attach_window(self, window: StatusMessageWindow) -> None:
        """绑定接收顶部状态消息的窗口。"""
        self._window = window

    def attach_generation_session(self, generation_session: ForegroundGenerationSession) -> None:
        """绑定前台文本生成会话。"""
        self._generation_session = generation_session

    def attach_audio_scheduler(self, audio_scheduler: AudioSchedulerPort) -> None:
        """绑定由 controller 统一编排的语音调度器。"""
        self._audio_scheduler = audio_scheduler
        self._audio_scheduler.start()

    def notify_status(self, text: str) -> None:
        """从任意线程提交一条状态文本。"""
        self._status_revision += 1
        self._signal_bridge.emit(text)

    def notify_idle_status(self) -> None:
        """恢复顶部状态栏的空闲随机文案。"""
        self.notify_status(random.choice(IDLE_STATUS_MESSAGES))

    def notify_temporary_status(self, text: str, *, restore_ms: int = 3000) -> None:
        """显示临时状态，并在延迟后恢复空闲文案。"""
        self._status_revision += 1
        restore_revision = self._status_revision
        self._signal_bridge.emit(text)
        self._status_restore_scheduler(
            restore_ms,
            lambda: self._restore_idle_status_if_current(restore_revision),
        )

    def set_visible_chat(self, chat_id: str) -> None:
        """记录当前可见的 chat id。"""
        self._visible_chat_id = chat_id

    def track_running_turn(self, chat_id: str, turn_id: str, token: CancellationToken) -> None:
        """记录一轮运行中对话的取消 token。"""
        self._running_turn_tokens[(chat_id, turn_id)] = token

    def mark_turn_phase(self, chat_id: str, turn_id: str, phase: str) -> None:
        """记录一轮对话当前阶段。"""
        self._turn_phases[(chat_id, turn_id)] = phase

    def submit_foreground_text_turn(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        user_text: str,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> list[ChatTurnEvent]:
        """通过 controller 提交一轮前台文本生成。"""
        if not self._accepting_turns:
            raise RuntimeError("ChatFlowController is shutting down")
        if self._generation_session is None:
            raise RuntimeError("ChatFlowController has no generation session")
        with chat.runtime.lock:
            # 如果已有排队的对话，则增加一个新的生成轮次
            if chat.runtime.running_turn_ids:
                chat.runtime.pending_user_turns.append(PendingUserTurn(turn_id=turn_id, text=user_text))
                # 记录正在等待处理的对话轮次
                self._pending_turn_requests[(chat_id, turn_id)] = PendingTurnRequest(
                    character_name=character_name,
                    settings=settings,
                )
                return [ChatPendingUserTurnQueued(chat_id=chat_id, turn_id=turn_id, text=user_text)]
            chat.runtime.running_turn_ids.add(turn_id)
        cancellation_token = settings.cancellation_token or CancellationToken()
        self.track_running_turn(chat_id, turn_id, cancellation_token)
        effective_settings = replace(settings, cancellation_token=cancellation_token)
        self._audio_turn_requests[(chat_id, turn_id)] = self._build_audio_turn_request(
            character_name=character_name,
            settings=effective_settings,
        )

        try:
            events = self._generation_session.run_foreground_turn(
                chat=chat,
                chat_id=chat_id,
                turn_id=turn_id,
                user_text=user_text,
                character_name=character_name,
                settings=effective_settings,
            )
        finally:
            with chat.runtime.lock:
                chat.runtime.running_turn_ids.discard(turn_id)
            self._running_turn_tokens.pop((chat_id, turn_id), None)

        return events + self._start_next_pending_turn(chat=chat, chat_id=chat_id)

    def cancel_visible_chat(self, chat: Chat) -> PendingTurnCancellationResult:
        """取消当前可见 chat 的运行中和排队中工作。"""
        if self._visible_chat_id and chat.chat_id != self._visible_chat_id:
            return PendingTurnCancellationResult()
        with chat.runtime.lock:
            running_turn_ids = list(chat.runtime.running_turn_ids)
        for turn_id in running_turn_ids:
            token = self._running_turn_tokens.get((chat.chat_id, turn_id))
            if token is not None:
                token.cancel()
        pending_result = self.cancel_pending_user_turns(chat)
        cancelled_audio_tasks = 0
        if self._audio_scheduler is not None:
            cancelled_audio_tasks = self._audio_scheduler.cancel_queued_tasks_for_chat(chat.chat_id)
        return PendingTurnCancellationResult(
            restored_text=pending_result.restored_text,
            status_message=pending_result.status_message,
            cancelled_audio_tasks=cancelled_audio_tasks,
        )

    def cancel_pending_user_turns(self, chat: Chat) -> PendingTurnCancellationResult:
        """取消指定 chat 尚未开始的 pending 用户输入。"""
        with chat.runtime.lock:
            pending_turns = list(chat.runtime.pending_user_turns)
            chat.runtime.pending_user_turns.clear()

        for pending_turn in pending_turns:
            self._pending_turn_requests.pop((chat.chat_id, pending_turn.turn_id), None)

        if len(pending_turns) == 1:
            return PendingTurnCancellationResult(restored_text=pending_turns[0].text)
        if len(pending_turns) > 1:
            message = f"已清除 {len(pending_turns)} 条排队消息。"
            self.notify_status(message)
            return PendingTurnCancellationResult(status_message=message)
        return PendingTurnCancellationResult()

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
        """把手动历史音频再生成请求提交给统一语音调度器。"""
        if self._audio_scheduler is None:
            return HistoricalAudioRegenerationResult(
                queued=False,
                status_message="语音调度器尚未启动，无法重新生成音频。",
            )
        if not self._accepting_turns:
            return HistoricalAudioRegenerationResult(
                queued=False,
                status_message="程序正在关闭，无法重新生成音频。",
            )
        return self._audio_scheduler.queue_historical_regeneration(
            chat=chat,
            chat_id=chat_id,
            turn_id=f"regen-{uuid.uuid4().hex}",
            message_index=message_index,
            character=character,
            settings=ChatAudioSettings(
                audio_enabled=True,
                sakiko_state=sakiko_state,
                audio_language_choice=audio_language_choice,
            ),
        )

    def _start_next_pending_turn(self, *, chat: Chat, chat_id: str) -> list[ChatTurnEvent]:
        """当前轮结束后启动同一 chat 的下一条 pending 输入。"""
        with chat.runtime.lock:
            if chat.runtime.running_turn_ids or not chat.runtime.pending_user_turns:
                return []
            pending_turn = chat.runtime.pending_user_turns.pop(0)
            request = self._pending_turn_requests.pop((chat_id, pending_turn.turn_id), None)
        if request is None:
            return []
        return self.submit_foreground_text_turn(
            chat=chat,
            chat_id=chat_id,
            turn_id=pending_turn.turn_id,
            user_text=pending_turn.text,
            character_name=request.character_name,
            settings=request.settings,
        )

    def switch_visible_chat(
        self,
        *,
        current_chat: Chat,
        next_chat: Chat,
        display: ChatDisplayPort,
        live2d_client: Live2DPlaybackPort,
    ) -> None:
        """切换当前可见 chat，并停止旧前台展示/播放。"""
        display.finish_stream_now()
        live2d_client.stop_playback()
        live2d_client.stop_thinking()
        self.set_visible_chat(next_chat.chat_id)
        display.render_chat(next_chat)
        if self._chat_has_llm_phase(next_chat.chat_id):
            live2d_client.start_thinking()

    def handle_turn_events(
        self,
        events: list[ChatTurnEvent],
        *,
        chat: Chat,
        display: ChatDisplayPort,
        live2d_client: Live2DPlaybackPort | None = None,
    ) -> None:
        """按当前前后台状态处理一组 turn events。"""
        for event in events:
            if isinstance(event, ChatTurnPhaseChanged):
                self.mark_turn_phase(event.chat_id, event.turn_id, event.phase)
            if isinstance(event, ChatAssistantSegmentCommitted):
                self._queue_audio_for_segment(event, chat)
            if isinstance(event, ChatTurnCompleted | ChatTurnCancelled | ChatTurnFailed):
                self._audio_turn_requests.pop((event.chat_id, event.turn_id), None)
            if event.chat_id != self._visible_chat_id:
                continue
            if isinstance(event, ChatTurnPhaseChanged) and event.phase == "llm":
                self.notify_status(f"{self._chat_status_character_name(chat)}思考中...")
                if live2d_client is not None:
                    live2d_client.start_thinking()
            elif isinstance(event, ChatPendingUserTurnQueued | ChatUserMessageCommitted):
                display.render_chat(chat)
            elif isinstance(event, ChatToolCallRecordsUpdated):
                display.render_chat(chat)
            elif isinstance(event, ChatAssistantSegmentCommitted):
                self._turn_phases.pop((event.chat_id, event.turn_id), None)
                if live2d_client is not None:
                    live2d_client.stop_thinking()
                if event.audio_path == PENDING_AUDIO:
                    continue
                with chat.runtime.lock:
                    if not 0 <= event.message_index < len(chat.message_list):
                        continue
                    message = chat.message_list[event.message_index]
                display.append_message(message, event.message_index, stream=True, interval_ms=30)
            elif isinstance(event, ChatTurnFailed):
                self._turn_phases.pop((event.chat_id, event.turn_id), None)
                if live2d_client is not None:
                    live2d_client.stop_thinking()
                self.notify_temporary_status(event.message)
            elif isinstance(event, ChatTurnCancelled):
                self._turn_phases.pop((event.chat_id, event.turn_id), None)
                if live2d_client is not None:
                    live2d_client.stop_thinking()
                self.notify_temporary_status("已终止当前回复")
            elif isinstance(event, ChatTurnCompleted):
                self._turn_phases.pop((event.chat_id, event.turn_id), None)
                if live2d_client is not None:
                    live2d_client.stop_thinking()
                self.notify_idle_status()

    def handle_audio_completed(
        self,
        *,
        chat_id: str,
        audio_path: str,
        emotion: str,
        display_text: str,
        live2d_client: Live2DPlaybackPort,
    ) -> None:
        """处理语音完成事件，并仅在前台驱动 Live2D 播放。"""
        if chat_id != self._visible_chat_id:
            return
        live2d_client.play_segment(
            audio_path=audio_path,
            emotion=emotion,
            display_text=display_text,
        )

    def shutdown(
        self,
        *,
        chat_manager: ChatManagerSavePort,
        live2d_client: Live2DShutdownPort | None = None,
        audio_worker: AudioWorkerShutdownPort | None = None,
        timeout_seconds: float = 0.0,
    ) -> None:
        """集中关闭聊天流程相关后台工作并保存最终聊天记录。"""
        self._accepting_turns = False
        for token in list(self._running_turn_tokens.values()):
            token.cancel()
        self._running_turn_tokens.clear()
        for chat in chat_manager.chat_list:
            self.cancel_pending_user_turns(chat)
        if self._audio_scheduler is not None:
            self._audio_scheduler.shutdown(
                cancel_background=True,
                timeout_seconds=timeout_seconds,
            )
        normalize_pending_audio_on_startup(chat_manager.chat_list)
        if live2d_client is not None:
            live2d_client.stop_playback()
            live2d_client.stop_thinking()
            live2d_client.shutdown()
        try:
            if audio_worker is not None:
                audio_worker.shutdown_worker(timeout_seconds=timeout_seconds)
        finally:
            chat_manager.save()

    def _build_audio_turn_request(
        self,
        *,
        character_name: str,
        settings: ChatTurnSettings,
    ) -> AudioTurnRequest:
        """根据 turn 设置创建语音调度快照。"""
        character = settings.character if settings.character is not None else character_name
        audio_settings = ChatAudioSettings(
            audio_enabled=settings.audio_enabled,
            sakiko_state=settings.sakiko_state,
            audio_language_choice=settings.audio_language_choice,
        )
        return AudioTurnRequest(character=character, settings=audio_settings)

    def _queue_audio_for_segment(
        self,
        event: ChatAssistantSegmentCommitted,
        chat: Chat,
    ) -> None:
        """把需要语音的 assistant 片段交给语音调度器。"""
        if event.audio_path != PENDING_AUDIO:
            return
        request = self._audio_turn_requests.get((event.chat_id, event.turn_id))
        if request is None or self._audio_scheduler is None:
            with chat.runtime.lock:
                if 0 <= event.message_index < len(chat.message_list):
                    chat.message_list[event.message_index].audio_path = NO_AUDIO
            return
        self._audio_scheduler.queue_message(
            chat=chat,
            chat_id=event.chat_id,
            turn_id=event.turn_id,
            message_index=event.message_index,
            text=event.text,
            character=request.character,
            settings=request.settings,
            emotion=event.emotion,
        )

    def _chat_has_llm_phase(self, chat_id: str) -> bool:
        """判断某个 chat 是否存在处于 LLM 阶段的运行轮次。"""
        return any(
            one_chat_id == chat_id and phase == "llm"
            for (one_chat_id, _turn_id), phase in self._turn_phases.items()
        )

    def _handle_status(self, text: str) -> None:
        """在 Qt 主线程上把状态文本交给窗口处理。"""
        if self._window is None:
            return
        self._window.handle_messages(text)

    @staticmethod
    def _schedule_status_restore(delay_ms: int, callback: Callable[[], None]) -> None:
        """使用 Qt 定时器延迟恢复顶部状态。"""
        QTimer.singleShot(delay_ms, callback)

    def _restore_idle_status_if_current(self, restore_revision: int) -> None:
        """只在状态没有变化时恢复空闲文案，避免覆盖更新状态。"""
        if restore_revision != self._status_revision:
            return
        self.notify_idle_status()

    @staticmethod
    def _chat_status_character_name(chat: Chat) -> str:
        """获取状态栏显示思考中时使用的角色名。"""
        character_name = chat.get_character_name()
        if character_name:
            return character_name
        for message in reversed(chat.message_list):
            if message.character_name != "User":
                return message.character_name
        return "角色"
