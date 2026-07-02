from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from emotion_enum import EmotionEnum


MotionPosition = Literal["C", "L", "R"]

POSITION_MOTION_SUFFIXES: tuple[MotionPosition, ...] = ("C", "L", "R")

_POSITION_DISPLAY_SUFFIXES: dict[MotionPosition, str] = {
    "C": "中",
    "L": "左",
    "R": "右",
}


@dataclass(frozen=True)
class StandardMotionGroup:
    """描述项目标准动作组的显示名称和语义规则。"""

    group_id: str
    display_title: str
    direct_keywords: frozenset[str]
    weak_keywords: frozenset[str] = frozenset()
    fallback_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadedV2MotionRule:
    """描述下载 V2 模型动作文件名前缀到标准动作组的归组规则。"""

    group_id: str
    prefixes: tuple[str, ...]
    target_stop_count: int


_STANDARD_MOTION_GROUPS: tuple[StandardMotionGroup, ...] = (
    StandardMotionGroup(
        "happiness",
        "1. 开心",
        frozenset(("smile", "kime", "wink")),
        frozenset(("play01", "play02", "play", "action")),
        ("like", "IDLE"),
    ),
    StandardMotionGroup(
        "sadness",
        "2. 伤心",
        frozenset(("sad", "cry")),
        frozenset(("serious", "denial")),
        ("fear", "IDLE"),
    ),
    StandardMotionGroup(
        "anger",
        "3. 生气",
        frozenset(("angry",)),
        frozenset(("serious", "denial", "confrict")),
        ("disgust", "sadness", "IDLE"),
    ),
    StandardMotionGroup(
        "disgust",
        "4. 反感",
        frozenset(("denial", "confrict")),
        frozenset(("serious", "angry")),
        ("anger", "sadness", "IDLE"),
    ),
    StandardMotionGroup(
        "like",
        "5. 喜欢",
        frozenset(("smile", "kime", "wink")),
        frozenset(("play01", "play02", "play")),
        ("happiness", "IDLE"),
    ),
    StandardMotionGroup(
        "surprise",
        "6. 惊讶",
        frozenset(("surprised", "question")),
        frozenset(("action", "play02")),
        ("happiness", "IDLE"),
    ),
    StandardMotionGroup(
        "fear",
        "7. 害怕",
        frozenset(("nervous",)),
        frozenset(("cry", "serious", "surprised", "confrict")),
        ("sadness", "surprise", "IDLE"),
    ),
    StandardMotionGroup(
        "IDLE",
        "8. 待机时随机",
        frozenset(("idle",)),
        fallback_groups=("idle_motion", "happiness"),
    ),
    StandardMotionGroup(
        "text_generating",
        "9. 思考时",
        frozenset(("thinking", "check")),
        frozenset(("nod", "serious")),
        ("talking_motion", "IDLE"),
    ),
    StandardMotionGroup(
        "bye",
        "10. 退出程序",
        frozenset(("bye", "finish")),
        fallback_groups=("change_character", "happiness", "IDLE"),
    ),
    StandardMotionGroup(
        "change_character",
        "11. 登场动作",
        frozenset(("bye", "join", "kime", "smile", "action", "spin", "maskoff")),
        frozenset(("play01", "play02")),
        ("bye", "happiness", "IDLE"),
    ),
    StandardMotionGroup(
        "idle_motion",
        "12. 待机",
        frozenset(("idle",)),
        fallback_groups=("IDLE",),
    ),
    StandardMotionGroup(
        "talking_motion",
        "13. 按下语音按钮",
        frozenset(("nod", "sing")),
        frozenset(("play01", "play02")),
        ("text_generating", "IDLE"),
    ),
)

_STANDARD_GROUPS_BY_ID: dict[str, StandardMotionGroup] = {
    group.group_id: group
    for group in _STANDARD_MOTION_GROUPS
}

_EMOTION_TO_MOTION_GROUP: dict[str, str] = {
    "label_0": "happiness",
    "label_1": "sadness",
    "label_2": "anger",
    "label_3": "disgust",
    "label_4": "like",
    "label_5": "surprise",
    "label_6": "fear",
    "happiness": "happiness",
    "sadness": "sadness",
    "anger": "anger",
    "disgust": "disgust",
    "like": "like",
    "surprise": "surprise",
    "fear": "fear",
}

_RANA_MOTION_SLICES: tuple[tuple[str, int, int], ...] = (
    ("happiness", 0, 6),
    ("sadness", 6, 12),
    ("anger", 12, 18),
    ("disgust", 18, 24),
    ("like", 24, 30),
    ("surprise", 30, 36),
    ("fear", 36, 42),
    ("IDLE", 42, 51),
    ("text_generating", 51, 54),
    ("bye", 54, 56),
    ("change_character", 56, 59),
    ("idle_motion", 59, 60),
    ("talking_motion", 60, 61),
)

_DOWNLOADED_V2_MOTION_RULES: tuple[DownloadedV2MotionRule, ...] = (
    DownloadedV2MotionRule("happiness", ("smile", "kime", "nnf03", "nnf04"), 5),
    DownloadedV2MotionRule("sadness", ("sad", "cry", "serious"), 11),
    DownloadedV2MotionRule("anger", ("angry", "serious"), 17),
    DownloadedV2MotionRule("disgust", ("angry", "serious", "scared"), 23),
    DownloadedV2MotionRule("like", ("smile", "kime", "jaan", "gattsu", "oowarai", "nnf04"), 29),
    DownloadedV2MotionRule("surprise", ("surprised", "scared", "jaan", "oowarai", "odoodo"), 35),
    DownloadedV2MotionRule("fear", ("scared", "cry", "serious", "sneeze", "odoodo"), 41),
    DownloadedV2MotionRule("IDLE", ("kime", "nnf", "smile01", "wink", "sleep", "niyaniya", "nf_left", "nf_right"), 50),
    DownloadedV2MotionRule("text_generating", ("thinking", "eeto"), 53),
    DownloadedV2MotionRule("bye", ("bye", "wink", "smile"), 55),
    DownloadedV2MotionRule("change_character", ("bye", "smile", "kime", "shame", "gattsu", "jaan"), 58),
    DownloadedV2MotionRule("idle_motion", ("idle", "smile"), 59),
    DownloadedV2MotionRule("talking_motion", ("nnf02", "nod"), 60),
)


def standard_motion_group_ids() -> tuple[str, ...]:
    """返回项目标准动作组 ID，顺序与导入和展示规则一致。"""
    return tuple(group.group_id for group in _STANDARD_MOTION_GROUPS)


def motion_group_display_title(group_name: str) -> str:
    """返回动作组的中文显示名，未知动作组回退为原始 ID。"""
    group = _STANDARD_GROUPS_BY_ID.get(group_name)
    if group is not None:
        return group.display_title

    base_name, separator, suffix = group_name.rpartition("_")
    if separator and suffix in _POSITION_DISPLAY_SUFFIXES:
        base_group = _STANDARD_GROUPS_BY_ID.get(base_name)
        if base_group is not None:
            position = _position_suffix(suffix)
            return f"{base_group.display_title}（{_POSITION_DISPLAY_SUFFIXES[position]}）"
    return group_name


def motion_group_display_titles(include_position_variants: bool = False) -> dict[str, str]:
    """返回动作组 ID 到中文显示名的映射，可选择包含位置变体。"""
    titles = {
        group.group_id: group.display_title
        for group in _STANDARD_MOTION_GROUPS
    }
    if include_position_variants:
        for group in _STANDARD_MOTION_GROUPS:
            for position in POSITION_MOTION_SUFFIXES:
                titles[f"{group.group_id}_{position}"] = (
                    f"{group.display_title}（{_POSITION_DISPLAY_SUFFIXES[position]}）"
                )
    return titles


def motion_group_for_emotion(emotion: str | EmotionEnum, default: str = "IDLE") -> str:
    """将旧情绪标签或新情绪名转换为项目标准动作组。"""
    if isinstance(emotion, EmotionEnum):
        return _EMOTION_TO_MOTION_GROUP.get(emotion.as_string(), default)
    return _EMOTION_TO_MOTION_GROUP.get(str(emotion).strip().lower(), default)


def rana_motion_slices() -> tuple[tuple[str, int, int], ...]:
    """返回旧版 V2 `rana` 动作数组到标准动作组的切片范围。"""
    return _RANA_MOTION_SLICES


def direct_motion_keywords(group_name: str) -> frozenset[str]:
    """返回动作文件名直接归入指定动作组时使用的关键词。"""
    group = _STANDARD_GROUPS_BY_ID.get(group_name)
    if group is None:
        return frozenset()
    return group.direct_keywords


def weak_motion_keywords(group_name: str) -> frozenset[str]:
    """返回动作组缺少直接匹配时使用的弱关键词。"""
    group = _STANDARD_GROUPS_BY_ID.get(group_name)
    if group is None:
        return frozenset()
    return group.weak_keywords


def fallback_motion_groups(group_name: str) -> tuple[str, ...]:
    """返回动作组为空时可复用的 fallback 动作组 ID。"""
    group = _STANDARD_GROUPS_BY_ID.get(group_name)
    if group is None:
        return ()
    return group.fallback_groups


def downloaded_v2_motion_prefixes(group_name: str) -> tuple[str, ...]:
    """返回下载 V2 模型时指定动作组使用的文件名前缀规则。"""
    for rule in _DOWNLOADED_V2_MOTION_RULES:
        if rule.group_id == group_name:
            return rule.prefixes
    return ()


def build_downloaded_v2_standard_motions(
        motion_file_names: Iterable[str],
        *,
        fallback_file_name: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    """根据下载 V2 模型的动作文件名生成可写入 model.json 的标准 motions 字段。"""
    motion_names = tuple(Path(name).name for name in motion_file_names)
    fallback_name = Path(fallback_file_name).name if fallback_file_name else None
    motions: dict[str, list[dict[str, str]]] = {}
    count = 0

    for rule in _DOWNLOADED_V2_MOTION_RULES:
        group_entries: list[dict[str, str]] = []
        motions[rule.group_id] = group_entries

        for prefix in rule.prefixes:
            for motion_name in motion_names:
                if motion_name.startswith(prefix):
                    group_entries.append({"name": f"{rule.group_id}_{count}", "file": motion_name})
                    count += 1
                    if count == rule.target_stop_count:
                        break
            if count == rule.target_stop_count:
                break

        if count != rule.target_stop_count:
            count = rule.target_stop_count
            if not group_entries and fallback_name is not None:
                group_entries.append({"name": f"{rule.group_id}_{count}", "file": fallback_name})

    return motions


def _position_suffix(value: str) -> MotionPosition:
    """将字符串位置后缀收窄为 MotionPosition 类型。"""
    if value == "C":
        return "C"
    if value == "L":
        return "L"
    return "R"
