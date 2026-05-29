from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Mapping
from typing import Protocol

from chat.chat import Chat
from chat.chat import Message
from chat.chat_meta import ToolCallRecordMeta
from emotion_enum import EmotionEnum


class ForegroundResolver(Protocol):
    """判断指定对话是否正在前台显示。"""

    def is_foreground_chat(self, chat_id: str) -> bool:
        """返回指定 chat 是否是当前前台 chat。"""


class LotterySideEffectPort(Protocol):
    """描述工具可使用的抽签 UI 副作用端口。"""

    def show_lottery(self, chat_id: str, turn_id: str, title: str, options: tuple[str, ...]) -> bool:
        """请求前台展示抽签窗口。"""


class Live2DSideEffectPort(Protocol):
    """描述工具可使用的 Live2D 副作用端口。"""

    def list_models(self, chat_id: str, turn_id: str, character_name: str) -> list[object]:
        """列出指定角色可用的 Live2D 模型名称。"""

    def change_model(self, chat_id: str, turn_id: str, character_name: str, model_name: str) -> bool:
        """请求切换当前前台角色的 Live2D 模型。"""


class ExportDocumentPort(Protocol):
    """描述工具可使用的文档导出副作用端口。"""

    def write_document(self, chat_id: str, turn_id: str, filename: str, content: str) -> str:
        """写出文档并返回生成的文件路径。"""

    def request_open_file(self, path: str) -> None:
        """请求前台 UI 打开文件。"""


@dataclass(frozen=True)
class NullForegroundResolver:
    """默认的前台解析器，表示没有 chat 被视为前台。"""

    def is_foreground_chat(self, chat_id: str) -> bool:
        """返回 False，供测试和后台路径使用。"""
        return False


@dataclass(frozen=True)
class ToolSideEffectPorts:
    """保存工具允许使用的受控副作用端口。"""

    # 抽签端口：调用时在前台展示抽签对话
    lottery: LotterySideEffectPort | None = None
    # live2d 设置端口：可以列出当前角色的 live2d 模型或切换当前角色的 live2d 模型为指定的文件
    live2d: Live2DSideEffectPort | None = None
    # 编写文档端口：可以写入文件或者要求前台的 UI 打开某个文件
    export_document: ExportDocumentPort | None = None


@dataclass(frozen=True)
class ToolTurnContext:
    """保存单轮工具执行所需的上下文信息。"""
    # 本轮次对话所属对话的 id
    chat_id: str
    # 本轮次对话的 id
    turn_id: str
    # 正在对话的角色的名称
    character_name: str
    # 回调函数：应当返回当前对话是否为前台对话
    foreground_resolver: ForegroundResolver
    # 具有副作用的工具应当统一使用 ToolSideEffectPorts 内的方法实现功能
    side_effect_ports: ToolSideEffectPorts


@dataclass(frozen=True)
class ToolCallSnapshot:
    """保存工具调用开始时需要持久化的最小快照。"""

    tool_call_id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ToolAssistantMessageSnapshot:
    """保存工具调用 interim/placeholder 消息的提交快照。"""

    message_index: int
    text: str
    translation: str
    emotion: str
    audio_path: str


class ToolCallRecordSink(Protocol):
    """描述工具循环写入持久化记录所需的接口。"""

    def start_tool_calls(
        self,
        *,
        interim_text: str,
        tool_calls: list[ToolCallSnapshot],
        is_placeholder: bool,
    ) -> int:
        """提交工具调用开始记录并返回绑定的消息索引。"""

    def complete_tool_call(self, execution_record: Mapping[str, object]) -> None:
        """根据工具执行结果更新工具调用记录。"""


class ChatToolCallRecordSink:
    """将工具调用消息和状态记录持久化到目标 Chat。"""

    def __init__(
        self,
        *,
        chat: Chat,
        chat_id: str,
        turn_id: str,
        character_name: str,
        audio_enabled: bool,
    ) -> None:
        """保存目标 chat 与本轮工具调用绑定信息。"""
        if chat.chat_id != chat_id:
            raise ValueError("tool call sink target chat_id does not match chat object")
        self._chat = chat
        self._chat_id = chat_id
        self._turn_id = turn_id
        self._character_name = character_name
        self._audio_enabled = audio_enabled
        self._message_snapshots: list[ToolAssistantMessageSnapshot] = []
        self._record_updates = False

    def start_tool_calls(
        self,
        *,
        interim_text: str,
        tool_calls: list[ToolCallSnapshot],
        is_placeholder: bool,
    ) -> int:
        """提交工具调用绑定的 assistant 消息，并写入 running 状态记录。"""
        text = interim_text.strip() or "..."
        audio_path = "NO_AUDIO" if is_placeholder or not self._audio_enabled else ""
        now_ts = int(time.time())
        with self._chat.runtime.lock:
            message = Message(
                character_name=self._character_name,
                text=text,
                translation="",
                emotion=EmotionEnum.HAPPINESS,
                audio_path=audio_path,
            )
            self._chat.add_message(message)
            message_index = len(self._chat.message_list) - 1
            self._message_snapshots.append(
                ToolAssistantMessageSnapshot(
                    message_index=message_index,
                    text=message.text,
                    translation=message.translation,
                    emotion=message.emotion.as_label(),
                    audio_path=message.audio_path,
                )
            )
            for tool_call in tool_calls:
                self._chat.append_tool_call_record(
                    ToolCallRecordMeta(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.name,
                        message_index=message_index,
                        status="running",
                        result_content="工具运行中...",
                        duration_sec=None,
                        started_at=now_ts,
                        updated_at=now_ts,
                    )
                )
            self._record_updates = True
        return message_index

    def complete_tool_call(self, execution_record: Mapping[str, object]) -> None:
        """根据工具执行结果更新 matching 的工具记录为 completed。"""
        tool_call_id = str(execution_record.get("tool_call_id") or "")
        if not tool_call_id:
            return
        with self._chat.runtime.lock:
            for record in reversed(self._chat.get_tool_call_records()):
                if record.tool_call_id != tool_call_id:
                    continue
                record.status = "completed"
                record.duration_sec = _as_float(execution_record.get("duration_sec"))
                record.result_content = str(execution_record.get("result_content") or "")
                record.ok = bool(execution_record.get("ok", True))
                record.updated_at = int(time.time())
                self._chat.update_tool_call_record(record)
                self._record_updates = True
                return

    def get_message_snapshots(self) -> list[ToolAssistantMessageSnapshot]:
        """返回工具调用期间提交的 assistant 消息快照。"""
        return list(self._message_snapshots)

    def has_record_updates(self) -> bool:
        """返回本轮工具调用是否写入或更新过工具记录。"""
        return self._record_updates


def _as_float(value: object) -> float:
    """将外部执行结果中的耗时字段安全转换为浮点数。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
