"""消息演出选择的存储契约，不依赖模型或图形运行时。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict


class PerformancePayload(TypedDict):
    """可跨进程和持久保存的独立演出选择。"""

    motion: str
    expression: str


@dataclass(frozen=True)
class PerformanceSelection:
    """使用逻辑资源标识选择动作与表情；auto 由播放时解析。"""

    motion: str = "auto"
    expression: str = "auto"

    @classmethod
    def from_value(cls, value: object) -> PerformanceSelection | None:
        """容忍损坏的视觉字段，不因此丢弃有效台词。"""
        if value is None or isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            logging.getLogger(__name__).warning("忽略非对象的 performance 字段")
            return None
        return cls(_selection_id(value.get("motion")), _selection_id(value.get("expression")))

    def as_dict(self) -> PerformancePayload:
        """返回稳定且与运行时无关的存储字典。"""
        return {"motion": self.motion, "expression": self.expression}


def _selection_id(value: object) -> str:
    """缺失、空或非字符串选择交给运行时自动决定。"""
    return value.strip() if isinstance(value, str) and value.strip() else "auto"


def performance_payload(value: object) -> PerformancePayload | None:
    """在消息、工具和传输边界复用一致的规范化规则。"""
    selection = PerformanceSelection.from_value(value)
    return selection.as_dict() if selection is not None else None
