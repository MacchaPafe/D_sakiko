from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
from typing import Protocol

from chat.chat import Chat
from chat_flow.audio_synthesizer import ChatAudioSynthesisResult
from chat_flow.audio_synthesizer import ChatAudioSettings
from chat_flow.audio_synthesizer import NO_AUDIO
from chat_flow.events import AssistantSegmentDraft


# 正在等待生成的音频的占位符号
PENDING_AUDIO = "PENDING_AUDIO"
# 无法点击播放的音频路径集合（包括无音频和正在等待生成的占位符）
NON_PLAYABLE_AUDIO_PATHS = frozenset({NO_AUDIO, PENDING_AUDIO})
AUDIO_PRIORITY_NORMAL = 0
AUDIO_PRIORITY_HIGH = 100
AUDIO_TASK_KIND_MESSAGE = "assistant_segment"
AUDIO_TASK_KIND_HISTORICAL_REGENERATION = "historical_regeneration"


def is_playable_audio_path(audio_path: str) -> bool:
    """判断音频路径是否代表可点击播放的真实音频。"""
    return bool(audio_path) and audio_path not in NON_PLAYABLE_AUDIO_PATHS


@dataclass(frozen=True)
class AudioTask:
    """保存单个语音任务的归因信息和设置快照。"""

    chat_id: str
    turn_id: str
    message_index: int
    text: str
    character: object
    settings: ChatAudioSettings
    emotion: str = "LABEL_0"
    priority: int = AUDIO_PRIORITY_NORMAL
    task_kind: str = AUDIO_TASK_KIND_MESSAGE


@dataclass(frozen=True)
class AudioTaskQueued:
    """表示语音任务已进入调度队列。"""

    task: AudioTask


@dataclass(frozen=True)
class HistoricalAudioRegenerationResult:
    """描述历史音频再生成是否成功进入调度队列。"""

    queued: bool
    status_message: str = ""
    event: AudioTaskQueued | None = None


@dataclass(frozen=True)
class AudioTaskCompleted:
    """表示语音任务已完成并回填到聊天历史。"""

    task: AudioTask
    audio_path: str
    playback_audio_path: str
    real_audio_generated: bool
    foreground_at_completion: bool
    success: bool = True
    error_message: str = ""


@dataclass(frozen=True)
class _QueuedAudioWork:
    """保存队列内部需要的 chat 引用和对外任务快照。"""

    chat: Chat
    task: AudioTask


class SegmentSynthesizer(Protocol):
    """描述调度器调用的单段语音合成接口。"""

    def synthesize_segment(
        self,
        *,
        draft: AssistantSegmentDraft,
        character: object,
        settings: ChatAudioSettings,
        status_callback: Callable[[str], None],
    ) -> ChatAudioSynthesisResult:
        """合成一个片段并返回展示和播放路径。"""


class AudioScheduler:
    """按当前前台 chat 动态优先级调度语音合成任务。"""

    def __init__(
        self,
        *,
        synthesizer: SegmentSynthesizer,
        foreground_resolver: Callable[[str], bool],
        status_callback: Callable[[str], None] | None = None,
        completion_callback: Callable[[AudioTaskCompleted], None] | None = None,
        foreground_completion_callback: Callable[[AudioTaskCompleted], None] | None = None,
    ) -> None:
        """保存语音合成器和动态前台判断函数。"""
        self._synthesizer = synthesizer
        self._foreground_resolver = foreground_resolver
        self._status_callback = status_callback or self._ignore_status
        self._completion_callback = completion_callback
        self._foreground_completion_callback = foreground_completion_callback
        self._queued_tasks: list[_QueuedAudioWork] = []
        self._running_tasks: list[_QueuedAudioWork] = []
        self._known_chats: dict[str, Chat] = {}
        self._condition = threading.Condition()
        self._worker_thread: threading.Thread | None = None
        self._shutdown_requested = False

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
        """把已提交 assistant 消息标记为等待语音并加入调度队列。"""
        task = AudioTask(
            chat_id=chat_id,
            turn_id=turn_id,
            message_index=message_index,
            text=text,
            character=character,
            settings=settings,
            emotion=emotion,
        )
        with chat.runtime.lock:
            self._mark_message_pending_locked(chat, task)
        return self._append_task(chat=chat, task=task)

    def _append_task(self, *, chat: Chat, task: AudioTask) -> AudioTaskQueued:
        """把准备好的语音任务加入调度队列。"""
        with chat.runtime.lock:
            if not 0 <= task.message_index < len(chat.message_list):
                raise IndexError("audio task message_index is out of range")
        with self._condition:
            self._known_chats[task.chat_id] = chat
            self._queued_tasks.append(_QueuedAudioWork(chat=chat, task=task))
            self._condition.notify()
        return AudioTaskQueued(task=task)

    def _mark_message_pending_locked(self, chat: Chat, task: AudioTask) -> None:
        """在调用方持有 runtime lock 时把目标消息标记为等待语音。"""
        if not 0 <= task.message_index < len(chat.message_list):
            raise IndexError("audio task message_index is out of range")
        chat.message_list[task.message_index].audio_path = PENDING_AUDIO
        chat.runtime.queued_audio_tasks += 1

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
        """将历史音频再生成作为高优先级前台任务加入调度器。"""
        with chat.runtime.lock:
            if chat.runtime.has_unfinished_work():
                return HistoricalAudioRegenerationResult(
                    queued=False,
                    status_message="请先终止生成或等待语音任务完成后再重新生成音频。",
                )
            if not 0 <= message_index < len(chat.message_list):
                raise IndexError("historical audio message_index is out of range")
            message = chat.message_list[message_index]
            text = message.text
            task = AudioTask(
                chat_id=chat_id,
                turn_id=turn_id,
                message_index=message_index,
                text=text,
                character=character,
                settings=settings,
                emotion=message.emotion.as_label(),
                priority=AUDIO_PRIORITY_HIGH,
                task_kind=AUDIO_TASK_KIND_HISTORICAL_REGENERATION,
            )
            self._mark_message_pending_locked(chat, task)
        event = self._append_task(
            chat=chat,
            task=task,
        )
        return HistoricalAudioRegenerationResult(queued=True, event=event)

    def run_next(self) -> AudioTaskCompleted | None:
        """按动态前台优先级执行下一个排队语音任务。"""
        with self._condition:
            if not self._queued_tasks:
                return None
            work = self._pop_next_work_locked()
            self._running_tasks.append(work)
        task = work.task
        with work.chat.runtime.lock:
            work.chat.runtime.queued_audio_tasks = max(0, work.chat.runtime.queued_audio_tasks - 1)
            work.chat.runtime.running_audio_tasks += 1

        try:
            cancelled_by_shutdown = False
            draft = AssistantSegmentDraft(
                message_index=task.message_index,
                text=task.text,
                translation="",
                emotion=task.emotion,
                force_no_audio=False,
            )
            try:
                result = self._synthesizer.synthesize_segment(
                    draft=draft,
                    character=task.character,
                    settings=task.settings,
                    status_callback=self._status_callback,
                )
                audio_path = result.display_audio_path or NO_AUDIO
                playback_audio_path = result.playback_audio_path
                real_audio_generated = result.real_audio_generated
                success = True
                error_message = ""
            except Exception as exc:
                audio_path = NO_AUDIO
                playback_audio_path = NO_AUDIO
                real_audio_generated = False
                success = False
                error_message = str(exc)
            with self._condition:
                cancelled_by_shutdown = self._shutdown_requested
            if cancelled_by_shutdown:
                audio_path = NO_AUDIO
                playback_audio_path = NO_AUDIO
                real_audio_generated = False
                success = False
                error_message = error_message or "audio scheduler shutdown"
            with work.chat.runtime.lock:
                if 0 <= task.message_index < len(work.chat.message_list):
                    if work.chat.message_list[task.message_index].audio_path == PENDING_AUDIO:
                        work.chat.message_list[task.message_index].audio_path = audio_path
            foreground_at_completion = self._foreground_resolver(task.chat_id)
            event = AudioTaskCompleted(
                task=task,
                audio_path=audio_path,
                playback_audio_path=playback_audio_path,
                real_audio_generated=real_audio_generated,
                foreground_at_completion=foreground_at_completion,
                success=success,
                error_message=error_message,
            )
            if (
                foreground_at_completion
                and not cancelled_by_shutdown
                and self._foreground_completion_callback is not None
            ):
                self._foreground_completion_callback(event)
            if not cancelled_by_shutdown and self._completion_callback is not None:
                self._completion_callback(event)
            return event
        finally:
            with self._condition:
                if work in self._running_tasks:
                    self._running_tasks.remove(work)
            with work.chat.runtime.lock:
                work.chat.runtime.running_audio_tasks = max(0, work.chat.runtime.running_audio_tasks - 1)

    def start(self) -> None:
        """启动后台语音调度线程。"""
        with self._condition:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._shutdown_requested = False
            self._worker_thread = threading.Thread(
                target=self._run_worker_loop,
                name="chat-audio-scheduler",
                daemon=True,
            )
            self._worker_thread.start()

    def _run_worker_loop(self) -> None:
        """持续消费语音任务，直到调度器关闭。"""
        while True:
            with self._condition:
                while not self._queued_tasks and not self._shutdown_requested:
                    self._condition.wait()
                if self._shutdown_requested and not self._queued_tasks:
                    return
            self.run_next()

    def _pop_next_work_locked(self) -> _QueuedAudioWork:
        """按当前前台状态选择下一条队列任务。"""
        best_index = 0
        best_score = self._work_priority_score(self._queued_tasks[0])
        for index, work in enumerate(self._queued_tasks[1:], start=1):
            score = self._work_priority_score(work)
            if score > best_score:
                best_index = index
                best_score = score
        return self._queued_tasks.pop(best_index)

    def _work_priority_score(self, work: _QueuedAudioWork) -> tuple[int, int]:
        """计算调度优先级：前台 chat 优先，其次高优先级任务优先。"""
        foreground_score = 1 if self._foreground_resolver(work.task.chat_id) else 0
        return (foreground_score, work.task.priority)

    def cancel_queued_tasks_for_chat(self, chat_id: str) -> int:
        """取消指定 chat 尚未开始的语音任务并回填 NO_AUDIO。"""
        with self._condition:
            remaining: list[_QueuedAudioWork] = []
            cancelled: list[_QueuedAudioWork] = []
            for work in self._queued_tasks:
                if work.task.chat_id == chat_id:
                    cancelled.append(work)
                else:
                    remaining.append(work)
            self._queued_tasks = remaining
        for work in cancelled:
            self._mark_work_no_audio(work, decrement_queued=True, decrement_running=False)
        return len(cancelled)

    def shutdown(self, *, cancel_background: bool = True, timeout_seconds: float = 0.0) -> int:
        """关闭调度器，取消未开始任务并归一化 pending audio。"""
        if not cancel_background:
            return 0
        with self._condition:
            self._shutdown_requested = True
            cancelled = list(self._queued_tasks)
            running = list(self._running_tasks)
            self._queued_tasks.clear()
            self._condition.notify_all()
        for work in cancelled:
            self._mark_work_no_audio(work, decrement_queued=True, decrement_running=False)
        for work in running:
            self._mark_work_no_audio(work, decrement_queued=False, decrement_running=True)
        self.normalize_known_pending_audio()
        worker_thread = self._worker_thread
        if worker_thread is not None and worker_thread.is_alive():
            worker_thread.join(timeout=max(0.0, timeout_seconds))
        return len(cancelled) + len(running)

    def normalize_known_pending_audio(self) -> int:
        """把调度器见过的 chat 中残留的 pending audio 归一化为 NO_AUDIO。"""
        with self._condition:
            chats = list(self._known_chats.values())
        return normalize_pending_audio_on_startup(chats)

    def _mark_work_no_audio(
        self,
        work: _QueuedAudioWork,
        *,
        decrement_queued: bool,
        decrement_running: bool,
    ) -> None:
        """把一条尚未开始的语音任务回填为 NO_AUDIO。"""
        with work.chat.runtime.lock:
            if decrement_queued:
                work.chat.runtime.queued_audio_tasks = max(0, work.chat.runtime.queued_audio_tasks - 1)
            if decrement_running:
                work.chat.runtime.running_audio_tasks = max(0, work.chat.runtime.running_audio_tasks - 1)
            if 0 <= work.task.message_index < len(work.chat.message_list):
                if work.chat.message_list[work.task.message_index].audio_path == PENDING_AUDIO:
                    work.chat.message_list[work.task.message_index].audio_path = NO_AUDIO

    @staticmethod
    def _ignore_status(text: str) -> None:
        """忽略未接线的语音进度状态。"""


def normalize_pending_audio_on_startup(chats: list[Chat]) -> int:
    """启动时把旧存档中残留的 PENDING_AUDIO 归一化为 NO_AUDIO。"""
    changed = 0
    for chat in chats:
        with chat.runtime.lock:
            for message in chat.message_list:
                if message.audio_path == PENDING_AUDIO:
                    message.audio_path = NO_AUDIO
                    changed += 1
    return changed
