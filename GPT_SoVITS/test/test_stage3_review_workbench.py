"""Stage 3 审核工作台表单控件测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from rag.models import ScopeType
from rag.pipeline.schemas import RelationObservationReviewRecord
from rag.pipeline.stage3_relation_models import (
    RelationTypeContentDraft,
    RelationTypeReviewRecord,
)
from rag.pipeline.stage3_review_workbench import (
    LORE_DEDUP_ACTION_OPTIONS,
    _lore_candidate_badge,
    _lore_dedup_action_description,
    _review_risk_explanations,
    _widget_text,
)
from rag.pipeline.stage3_thought_models import (
    ThoughtThreadContentDraft,
    ThoughtThreadReviewRecord,
    ThoughtUpdateEvidence,
    UnassignedThoughtUpdateDecision,
)


class TestStage3ReviewWorkbench(unittest.TestCase):
    """验证审核工作台控件值的规范化行为。"""

    def test_widget_text_uses_enum_value(self) -> None:
        """枚举控件值应转换为数据值而不是枚举限定名。"""

        widget = SimpleNamespace(value=ScopeType.SERIES)
        self.assertEqual(_widget_text(widget), "series")

    def test_lore_dedup_actions_have_explicit_chinese_labels(self) -> None:
        """Lore 去重动作应明确说明最终发布范围。"""

        self.assertEqual(
            LORE_DEDUP_ACTION_OPTIONS,
            {
                "keep_separate": "分别保留（全部发布）",
                "merge": "合并为一条（仅保留所选候选）",
                "drop": "整组丢弃（全部不发布）",
            },
        )
        self.assertIn(
            "全部 3 条候选都不会发布",
            _lore_dedup_action_description("drop", 3),
        )
        self.assertIn("不会自动综合", _lore_dedup_action_description("merge", 3))

    def test_lore_candidate_badges_reflect_merge_and_drop(self) -> None:
        """候选状态标签应区分最终保留与整组丢弃。"""

        self.assertEqual(
            _lore_candidate_badge("merge", "candidate-a", "candidate-a"),
            ("最终保留", "positive"),
        )
        self.assertEqual(
            _lore_candidate_badge("merge", "candidate-a", "candidate-b"),
            ("合并后移除", "warning"),
        )
        self.assertEqual(
            _lore_candidate_badge("drop", "candidate-a", "candidate-a"),
            ("整组丢弃", "negative"),
        )

    def test_thought_thread_risk_explains_inferred_update(self) -> None:
        """高风险 Thought Thread 应指出触发判定的推断性 Update。"""

        update = ThoughtUpdateEvidence(
            update_id="thought_update:test",
            source_scene_id="scene:test",
            source_local_id="thought:test",
            character_id="taki",
            thought_text="立希推测灯可能会加入乐队。",
            subject_kind="standalone_topic",
            subject_text="灯是否加入乐队",
            epistemic_status="suspects",
            provisional_update_type="acquired",
            evidence_strength="inferred",
            extraction_confidence=0.8,
            evidence_time=10,
        )
        thread = ThoughtThreadReviewRecord(
            thought_thread_id="thought_thread:00000000-0000-0000-0000-000000000001",
            character_id="taki",
            series_id="its_mygo",
            timeline_id="main",
            canon_branch="main",
            covered_update_ids=[update.update_id],
            generated_content=ThoughtThreadContentDraft(
                canonical_subject="灯是否加入乐队",
                thought_aspect="预测",
            ),
            risk_level="high",
            review_basis_sha256="0" * 64,
        )

        reasons = _review_risk_explanations(thread, [update])

        self.assertEqual(len(reasons), 1)
        self.assertIn("推断性 Thought Update", reasons[0])
        self.assertIn(update.thought_text, reasons[0])

    def test_unresolved_update_risk_explains_manual_choice(self) -> None:
        """未解决 Update 应说明高风险来自模型无法归线。"""

        decision = UnassignedThoughtUpdateDecision(
            update_id="thought_update:test",
            kind="unresolved",
            generated_reason="模型无法确定所属 Thread。",
            risk_level="high",
            review_basis_sha256="0" * 64,
        )

        reasons = _review_risk_explanations(decision, [])

        self.assertIn("未能把该 Update 归入", reasons[0])

    def test_relation_risk_appends_source_observation_ambiguity(self) -> None:
        """Relation 风险说明应追加支持 Observation 的具体歧义。"""

        observation = RelationObservationReviewRecord(
            observation_id="relation_observation:test",
            scene_id="scene:test",
            time_order=10,
            subject_character_id="anon",
            object_character_id="taki",
            observation_text="爱音与立希发生争执。",
            evidence_strength="explicit",
            confidence=0.9,
            ambiguity_notes="冲突可能只针对当前组乐队事件。",
        )
        relation_type = RelationTypeReviewRecord(
            relation_type_id="relation_type:00000000-0000-0000-0000-000000000001",
            subject_character_id="anon",
            object_character_id="taki",
            series_id="its_mygo",
            timeline_id="main",
            canon_branch="main",
            covered_observation_ids=[observation.observation_id],
            generated_content=RelationTypeContentDraft(
                semantic_label="ongoing_friction",
            ),
            risk_level="high",
            risk_reasons=["聚合结果包含歧义说明。"],
            review_basis_sha256="0" * 64,
        )

        reasons = _review_risk_explanations(relation_type, [], [observation])

        self.assertTrue(
            any("冲突可能只针对当前组乐队事件" in reason for reason in reasons)
        )


if __name__ == "__main__":
    unittest.main()
