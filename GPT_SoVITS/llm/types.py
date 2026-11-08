"""统一请求、完整响应与取消信号。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class LLMConfig:
    """一次请求使用的显式连接快照；凭据不参与存档。"""

    provider: str
    model: str
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    protocol: str = "chat-completions"
    timeout: float = 30.0
    sdk_chat: bool = False
    route: str = "standard"
    account_id: str = ""
    connection_identity: str = ""


@dataclass
class LLMRequest:
    """调用方准备的完整上下文；模块不裁剪、不依赖服务端历史。"""

    messages: list[dict[str, object]]
    tools: list[dict[str, object]] = field(default_factory=list)
    tool_choice: object = "auto"
    options: dict[str, object] = field(default_factory=dict)
    conversation_id: str = ""


@dataclass
class LLMResult:
    """已完成的模型响应；保留有序内容及可回传材料。"""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    content: list[dict[str, object]] = field(default_factory=list)
    usage: dict[str, object] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = "stop"
    protocol: dict[str, object] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


class CancellationToken:
    """只传递取消状态，不向外部暴露传输事件。"""

    def __init__(self, check: Callable[[], bool] | None = None) -> None:
        """创建独立取消信号，可同时观察运行时已有的取消标记。"""
        self._event = threading.Event()
        self._check = check

    def cancel(self) -> None:
        """请求结束本地网络等待。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """返回当前是否已经取消。"""
        return self._event.is_set() or bool(self._check and self._check())
