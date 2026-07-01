from __future__ import annotations

import random
from collections.abc import Container, Mapping
from typing import Literal


# 动作的方向：C 代表角色面向屏幕，L 代表角色位于左侧（向右倾身），R 代表角色位于右侧（向左倾身）。
MotionPosition = Literal["C", "L", "R"]


def resolve_positioned_motion_group(
        group_name: str,
        position: MotionPosition | None,
        motion_groups: Container[str],
) -> str:
    """根据位置参数选择动作组，缺少位置组时回退到基础组。"""
    if position is None:
        return group_name
    positioned_group_name = f"{group_name}_{position}"
    if positioned_group_name in motion_groups:
        return positioned_group_name
    return group_name


def select_random_motion(
        group_name: str | None,
        position: MotionPosition | None,
        motion_groups: Container[str],
        motion_files_by_group: Mapping[str, tuple[str, ...]],
) -> tuple[str, int] | None:
    """
    motion_groups: 所有动作组名称的集合
    motion_files_by_group: 动作组名称到动作文件名列表的映射，例如 {"happiness": ("happy_01.mtn", "happy_02.mtn"), "happines_L": ("happy_left_01.mtn",)}

    按如下方法返回一个角色动作：
    - 如果 group_name 存在，只从 group_name 动作组中的动作中选择，否则从所有动作组随机
    - 如果 position 存在，则只返回对应朝向（结尾是 L/R/C）的动作，否则无限制
    """
    if group_name:
        resolved_group_name = resolve_positioned_motion_group(group_name, position, motion_groups)
        motion_files = motion_files_by_group.get(resolved_group_name, ())
        if not motion_files:
            return None
        return resolved_group_name, random.randrange(len(motion_files))

    non_empty_groups = [
        name
        for name, motion_files in motion_files_by_group.items()
        if motion_files
    ]
    if not non_empty_groups:
        return None
    resolved_group_name = random.choice(non_empty_groups)
    motion_files = motion_files_by_group[resolved_group_name]
    return resolved_group_name, random.randrange(len(motion_files))
