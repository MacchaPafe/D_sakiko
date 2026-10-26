"""消息演出选择的存储契约，不依赖模型或图形运行时。"""

from __future__ import annotations

import logging
from copy import deepcopy
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


def timeline_tracks(value: object) -> dict:
    """返回可解释的角色轨道；原始数据由消息保留，播放不修写损坏存档。"""
    if not isinstance(value, dict):
        return {}
    characters = value.get("characters")
    if not isinstance(characters, dict):
        return {}
    return {name: tracks for name, tracks in characters.items()
            if isinstance(name, str) and isinstance(tracks, dict)}


def valid_timeline_events(events: object, kind: str) -> list:
    """筛选合法起点和资源引用，同轨重复起点只执行首个事件。"""
    if not isinstance(events, list):
        return []
    result = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        at = event.get("at_ms")
        if type(at) is not int or at < 0 or at in seen or "resource" not in event:
            logging.getLogger(__name__).warning("跳过损坏或重复的时间轴事件：%s", event)
            continue
        resource = event["resource"]
        reset = kind == "expressions" and resource is None
        reference = isinstance(resource, dict) and any(
            isinstance(resource.get(key), str) and resource[key]
            for key in ("logical_id", "file_name")
        )
        if not reset and not reference:
            logging.getLogger(__name__).warning("跳过无资源的时间轴事件：%s", event)
            continue
        seen.add(at)
        result.append(event)
    return sorted(result, key=lambda event: event["at_ms"])


def trim_timeline(value: object, duration_ms: int) -> tuple:
    """复制编排并只删除起点越界的事件，涵盖禁用或不在场角色。"""
    result = deepcopy(value)
    removed = 0
    for tracks in timeline_tracks(result).values():
        for kind in ("motions", "expressions"):
            events = tracks.get(kind)
            if not isinstance(events, list):
                continue
            retained = []
            for event in events:
                at = event.get("at_ms") if isinstance(event, dict) else None
                if type(at) is int and at >= duration_ms:
                    removed += 1
                else:
                    retained.append(event)
            tracks[kind] = retained
    return result, removed


def sort_timeline_events(events: list) -> None:
    """排序可解释事件，损坏记录保留在末尾供修复，不在编辑时丢弃。"""
    def key(event):
        """合法整数起点优先，未知记录保持原有相对顺序。"""
        at = event.get("at_ms") if isinstance(event, dict) else None
        return (0, at) if type(at) is int else (1, 0)

    events.sort(key=key)
