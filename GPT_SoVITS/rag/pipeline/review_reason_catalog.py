"""集中定义 Stage 3 不收录原因的中文说明与处置映射。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .review_models import Disposition, DispositionReasonCode


ReviewItemKind = Literal["story", "lore", "relation", "thought"]

_ALL_KINDS: frozenset[ReviewItemKind] = frozenset(
    {"story", "lore", "relation", "thought"}
)
_LONG_TERM_KINDS: frozenset[ReviewItemKind] = frozenset(
    {"lore", "relation", "thought"}
)
_STATE_KINDS: frozenset[ReviewItemKind] = frozenset({"relation", "thought"})


@dataclass(frozen=True, slots=True)
class ReviewReasonOption:
    """描述一个面向用户的不收录原因及其内部处置语义。"""

    key: str
    disposition: Literal["reject", "exclude"]
    reason_code: DispositionReasonCode
    label: str
    description: str
    applicable_kinds: frozenset[ReviewItemKind]

    @property
    def select_label(self) -> str:
        """返回下拉选项使用的完整中文说明。"""

        group = "Reject · 候选无效" if self.disposition == "reject" else "Exclude · 有效但不收录"
        return f"{group}｜{self.label} — {self.description}"


_REASON_OPTIONS: tuple[ReviewReasonOption, ...] = (
    ReviewReasonOption(
        key="reject:unsupported",
        disposition="reject",
        reason_code="unsupported",
        label="缺少证据支持",
        description="现有字幕、画面或上下文不足以确认这条候选。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="reject:extraction_error",
        disposition="reject",
        reason_code="extraction_error",
        label="模型抽取错误",
        description="内容、角色、时间或语义被错误提取，候选本身不成立。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="reject:duplicate",
        disposition="reject",
        reason_code="duplicate",
        label="与其他候选重复",
        description="同一事实或状态已经由另一条候选完整覆盖。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="reject:other",
        disposition="reject",
        reason_code="other",
        label="其他候选错误",
        description="候选不成立，但原因不属于上述类型；必须补充说明。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="exclude:transient_state",
        disposition="exclude",
        reason_code="transient_state",
        label="仅为短期或瞬时状态",
        description="内容可能真实，但只是短暂的关系或想法状态，不作为长期知识收录。",
        applicable_kinds=_STATE_KINDS,
    ),
    ReviewReasonOption(
        key="exclude:too_trivial",
        disposition="exclude",
        reason_code="too_trivial",
        label="内容过于琐碎",
        description="内容可能真实，但对当前世界书的长期检索价值不足。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="exclude:wrong_package",
        disposition="exclude",
        reason_code="wrong_package",
        label="属于其他世界书",
        description="内容有效，但其正式归属范围不是当前世界书包。",
        applicable_kinds=_ALL_KINDS,
    ),
    ReviewReasonOption(
        key="exclude:not_long_term_knowledge",
        disposition="exclude",
        reason_code="not_long_term_knowledge",
        label="不属于长期知识",
        description="内容有效，但不适合作为 Lore、Relation 或 Thought 的长期状态保存。",
        applicable_kinds=_LONG_TERM_KINDS,
    ),
    ReviewReasonOption(
        key="exclude:other",
        disposition="exclude",
        reason_code="other",
        label="其他暂不收录原因",
        description="内容可能有效，当前暂不收录；必须补充说明，可供后续复查。",
        applicable_kinds=_ALL_KINDS,
    ),
)


def review_reason_options(item_kind: ReviewItemKind) -> tuple[ReviewReasonOption, ...]:
    """返回适用于指定审核内容类型的全部不收录原因。"""

    return tuple(
        option for option in _REASON_OPTIONS if item_kind in option.applicable_kinds
    )


def review_reason_by_key(
    key: str,
    item_kind: ReviewItemKind,
) -> ReviewReasonOption:
    """按界面键查找原因，并拒绝不适用于当前内容类型的选择。"""

    for option in review_reason_options(item_kind):
        if option.key == key:
            return option
    raise ValueError(f"不适用于 {item_kind} 的不收录原因: {key}")


def disposition_badge_label(disposition: Disposition) -> str:
    """返回最终处置在审核队列中使用的中文标签。"""

    if disposition == "publish":
        return "Publish · 已收录"
    if disposition == "reject":
        return "Reject · 候选无效"
    return "Exclude · 有效但未收录"


def disposition_description(disposition: Disposition) -> str:
    """返回最终处置在详情页使用的中文解释。"""

    if disposition == "publish":
        return "候选审核成立，并会进入正式世界书。"
    if disposition == "reject":
        return "候选本身不成立，不应继续作为有效标注使用。"
    return "内容可能有效，但当前不进入正式世界书；保留区别以便统计、复查或未来重新利用。"
