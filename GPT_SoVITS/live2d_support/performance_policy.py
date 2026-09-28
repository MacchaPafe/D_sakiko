"""演出资源解析和状态差异计算，与渲染和音频解耦。"""

from __future__ import annotations

from dataclasses import dataclass

from live2d_support.expression_policy import SEMANTIC_EXPRESSION_CANDIDATES
from live2d_support.motion_semantics import motion_group_for_emotion
from live2d_support.performance_catalog import PerformanceCatalog
from performance_types import PerformanceSelection


@dataclass(frozen=True)
class PerformanceState:
    """当前模型实例上成功应用的演出资源。"""

    motion_id: str | None = None
    motion_file: str | None = None
    expression: str | None = None
    emotion: str = ""


@dataclass(frozen=True)
class PerformancePlan:
    """本段所需的通道变化及待执行成功后提交的状态。"""

    state: PerformanceState
    change_motion: bool
    change_expression: bool
    legacy_group: str | None = None
    fallback_reasons: tuple[str, ...] = ()


def resolve_performance(
    catalog: PerformanceCatalog,
    selection: PerformanceSelection | None,
    emotion: str,
    direction: str = "C",
    current: PerformanceState | None = None,
) -> PerformancePlan:
    """按照实际模型执行四格兼容；缺失资源仅回退对应通道。"""
    previous = current or PerformanceState()
    group = motion_group_for_emotion(emotion, default="happiness")
    if catalog.version != "v3":
        return PerformancePlan(PerformanceState(emotion=group), True, False, group)
    chosen = selection or PerformanceSelection()
    reasons: list[str] = []
    motion_id = chosen.motion if catalog.motion(chosen.motion, direction) else None
    if motion_id is None:
        if chosen.motion != "auto":
            reasons.append("motion_unavailable")
        pools = (catalog.groups.get(f"{group}_{direction}", []), catalog.groups.get(group, []),
                 catalog.groups.get("idle_motion", []), catalog.groups.get("IDLE", []), list(catalog.motions))
        candidates = next((available for pool in pools
                           if (available := [key for key in pool if catalog.motion(key, direction)])), [])
        motion_id = previous.motion_id if previous.emotion == group and previous.motion_id in candidates else next(iter(candidates), None)
    asset = catalog.motion(motion_id, direction) if motion_id is not None else None
    expression = chosen.expression if chosen.expression in catalog.expressions else None
    if expression is None:
        if chosen.expression != "auto":
            reasons.append("expression_unavailable")
        candidates = SEMANTIC_EXPRESSION_CANDIDATES.get(group, ()) + SEMANTIC_EXPRESSION_CANDIDATES["idle"]
        expression = next((key for key in candidates if key in catalog.expressions), None)
        if expression is None and previous.expression in catalog.expressions:
            expression = previous.expression
    state = PerformanceState(motion_id, asset.file if asset else None, expression, group)
    return PerformancePlan(state, bool(state.motion_file and state.motion_file != previous.motion_file),
                           bool(expression and expression != previous.expression), fallback_reasons=tuple(reasons))
