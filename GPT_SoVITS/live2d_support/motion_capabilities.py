from __future__ import annotations

import json
from collections.abc import Container, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from live2d_support.motion_semantics import MotionPosition, POSITION_MOTION_SUFFIXES, standard_motion_group_ids


@dataclass(frozen=True)
class Live2DMotionCapabilities:
    """描述 Live2D 模型持久动作组声明出的方向动作能力。"""

    supported_positions_by_group: dict[str, frozenset[MotionPosition]]

    def supports_position(self, position: MotionPosition) -> bool:
        """判断模型是否存在任意非空的指定方向变体动作组。"""
        return any(position in positions for positions in self.supported_positions_by_group.values())

    def supports_group_position(self, group_name: str, position: MotionPosition) -> bool:
        """判断指定项目标准动作组是否存在非空的指定方向变体。"""
        return position in self.supported_positions_by_group.get(group_name, frozenset())


def motion_capabilities_from_motion_files_by_group(
        motion_files_by_group: Mapping[str, tuple[str, ...]],
        *,
        ignored_groups: Container[str] = frozenset(),
) -> Live2DMotionCapabilities:
    """根据动作组到动作文件的映射计算方向动作能力。"""
    standard_groups = set(standard_motion_group_ids())
    supported_positions: dict[str, set[MotionPosition]] = {}

    for group_name, motion_files in motion_files_by_group.items():
        if group_name in ignored_groups or not motion_files:
            continue
        base_group_name, separator, suffix = group_name.rpartition("_")
        if not separator or suffix not in POSITION_MOTION_SUFFIXES:
            continue
        if base_group_name not in standard_groups:
            continue
        position = cast(MotionPosition, suffix)
        supported_positions.setdefault(base_group_name, set()).add(position)

    frozen_positions = {
        group_name: frozenset(positions)
        for group_name, positions in supported_positions.items()
    }
    return Live2DMotionCapabilities(supported_positions_by_group=frozen_positions)


def get_live2d_motion_capabilities(model_json_path: str) -> Live2DMotionCapabilities:
    """读取模型 JSON 并计算其持久动作组声明出的方向动作能力。"""
    model_data = _read_model_json_object(model_json_path)
    motion_files_by_group = _collect_motion_files_by_group(model_data, Path(model_json_path))
    return motion_capabilities_from_motion_files_by_group(motion_files_by_group)


def _read_model_json_object(model_json_path: str) -> dict[str, object]:
    """读取 Live2D 模型 JSON 顶层对象。"""
    with open(model_json_path, "r", encoding="utf-8") as file:
        loaded_data = json.load(file)
    if not isinstance(loaded_data, dict):
        raise ValueError(f"Live2D 模型 JSON 顶层不是对象：{model_json_path}")
    return cast(dict[str, object], loaded_data)


def _collect_motion_files_by_group(
        model_data: Mapping[str, object],
        model_json_path: Path,
) -> dict[str, tuple[str, ...]]:
    """从 Live2D V2/V3 模型 JSON 中收集持久动作组文件。"""
    if model_json_path.name.endswith(".model3.json"):
        file_references = model_data.get("FileReferences")
        if not isinstance(file_references, dict):
            return {}
        motions = file_references.get("Motions")
    else:
        motions = model_data.get("motions")

    if not isinstance(motions, dict):
        return {}

    motion_files_by_group: dict[str, tuple[str, ...]] = {}
    for group_name, entries in cast(dict[object, object], motions).items():
        if not isinstance(group_name, str) or not isinstance(entries, list):
            continue
        motion_files = tuple(_motion_files_from_entries(entries))
        motion_files_by_group[group_name] = motion_files
    return motion_files_by_group


def _motion_files_from_entries(entries: list[object]) -> list[str]:
    """从一个动作组条目列表中提取有效动作文件名。"""
    motion_files: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry:
            motion_files.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        motion_file = entry.get("File")
        if not isinstance(motion_file, str):
            motion_file = entry.get("file")
        if isinstance(motion_file, str) and motion_file:
            motion_files.append(motion_file)
    return motion_files
