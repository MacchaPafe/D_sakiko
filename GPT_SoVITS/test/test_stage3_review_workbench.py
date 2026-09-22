"""Stage 3 审核工作台表单控件测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from rag.models import ScopeType
from rag.pipeline.schemas import (
    LoreEntryPayload,
    RelationObservationReviewRecord,
    Stage3ImportMetadata,
)
from rag.pipeline.stage3_document_models import (
    LoreEntryReviewRecord,
    Stage3DocumentReviewArtifact,
)
from rag.pipeline.stage3_relation_models import (
    RelationTypeContentDraft,
    RelationTypeReviewRecord,
)
from rag.pipeline.stage3_review_workbench import (
    LORE_DEDUP_ACTION_OPTIONS,
    ReviewListItem,
    _lore_candidate_badge,
    _lore_dedup_action_description,
    _review_list_sort_key,
    _review_risk_explanations,
    _widget_text,
    published_lore_match_index,
    story_thought_links,
)
from rag.pipeline.stage3_thought_models import (
    Stage3ThoughtReviewArtifact,
    ThoughtAggregationMetadata,
    ThoughtStateDraft,
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

    def test_story_queue_sorts_by_status_risk_time_order_and_id(self) -> None:
        """剧情队列应依次按审核状态、风险、发生顺序和稳定 ID 排列。"""

        items = [
            self._story_list_item("completed", "high", 1, "story:d"),
            self._story_list_item("unreviewed", "low", 5, "story:c"),
            self._story_list_item("unreviewed", "high", 20, "story:b"),
            self._story_list_item("unreviewed", "high", 10, "story:a"),
        ]

        ordered = sorted(items, key=_review_list_sort_key)

        self.assertEqual(
            [item.item_id for item in ordered],
            ["story:a", "story:b", "story:c", "story:d"],
        )

    @staticmethod
    def _story_list_item(
        review_status: str,
        risk_level: str,
        time_order: int,
        item_id: str,
    ) -> ReviewListItem:
        """构造用于验证剧情队列排序的条目。"""

        return ReviewListItem(
            slot_key="document:1",
            item_id=item_id,
            kind="story",
            title="不参与剧情排序的标题",
            review_status=review_status,
            disposition=None,
            risk_level=risk_level,
            identity_pending=False,
            human_edited=False,
            time_order=time_order,
        )

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

    def test_lore_matches_only_published_same_name_and_scope(self) -> None:
        """同名提示应只引用已收录且适用范围完全一致的 Lore。"""

        published = self._lore_record(
            "11111111-1111-4111-8111-111111111111",
            title="羽丘 女子学园",
            disposition="publish",
        )
        current = self._lore_record(
            "22222222-2222-4222-8222-222222222222",
            title="羽丘女子学园",
        )
        rejected = self._lore_record(
            "33333333-3333-4333-8333-333333333333",
            title="羽丘女子学园",
            disposition="reject",
        )
        other_timeline = self._lore_record(
            "44444444-4444-4444-8444-444444444444",
            title="羽丘女子学园",
            disposition="publish",
            timeline_id="another_timeline",
        )
        first = self._document_artifact(1, [published])
        second = self._document_artifact(2, [current, rejected, other_timeline])

        matches = published_lore_match_index(
            [("document:1", first), ("document:2", second)]
        )

        self.assertEqual(len(matches[current.candidate_id]), 1)
        self.assertEqual(
            matches[current.candidate_id][0].candidate_id,
            published.candidate_id,
        )
        self.assertNotIn(other_timeline.candidate_id, matches)

    @staticmethod
    def _lore_record(
        uuid_text: str,
        *,
        title: str,
        disposition: str | None = None,
        timeline_id: str = "bang_dream_original",
    ) -> LoreEntryReviewRecord:
        """构造工作台同名 Lore 测试记录。"""

        completed = disposition is not None
        reason_code = "duplicate" if disposition == "reject" else None
        return LoreEntryReviewRecord(
            candidate_id=f"lore_candidate:{uuid_text}",
            source_scene_id="scene:test",
            source_local_id=f"lore:{uuid_text}",
            confidence=0.9,
            generated_document=LoreEntryPayload(
                scope_type="series",
                series_ids=["its_mygo"],
                timeline_id=timeline_id,
                applicable_story_years=[3],
                canon_branch="main",
                title=title,
                content=f"{title}的设定说明。",
                retrieval_text=f"{title}的设定说明。",
            ),
            review_status="completed" if completed else "unreviewed",
            disposition=disposition,
            disposition_reason_code=reason_code,
            review_basis_sha256="0" * 64,
        )

    @staticmethod
    def _document_artifact(
        episode: int,
        lore_entries: list[LoreEntryReviewRecord],
    ) -> Stage3DocumentReviewArtifact:
        """构造包含指定 Lore 的逐集审核产物。"""

        return Stage3DocumentReviewArtifact(
            metadata=Stage3ImportMetadata(
                subtitle_path=f"ep{episode:02d}.ass",
                anime_title="It's MyGO!!!!!",
                series_id="its_mygo",
                timeline_id="bang_dream_original",
                story_year=3,
                canon_branch="main",
                episode=episode,
                source_stage2_model="test",
                source_stage2_template_path="test.jinja",
            ),
            lore_entries=lore_entries,
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

    def test_story_reverse_links_use_effective_thought_and_show_status(self) -> None:
        """Story 提示应扫描 effective Thought 并携带审核与 stale 状态。"""

        candidate_id = "story_candidate:11111111-1111-4111-8111-111111111111"
        state = ThoughtStateDraft(
            thought_state_id="thought_state:00000000-0000-0000-0000-000000000002",
            transition="acquired",
            thought_text="爱音记得灯递来的创可贴。",
            epistemic_status="knows",
            visible_from=4002,
            visible_to=999999,
            story_event_candidate_ids=[candidate_id],
            retrieval_text="爱音记得初遇灯时的创可贴。",
        )
        thread = ThoughtThreadReviewRecord(
            thought_thread_id="thought_thread:00000000-0000-0000-0000-000000000001",
            character_id="anon",
            series_id="its_mygo",
            timeline_id="main",
            canon_branch="main",
            generated_content=ThoughtThreadContentDraft(
                canonical_subject="初遇灯",
                thought_aspect="长期印象",
            ),
            reviewed_content=ThoughtThreadContentDraft(
                canonical_subject="初遇灯",
                thought_aspect="记忆",
                states=[state],
            ),
            review_status="completed",
            disposition="publish",
            risk_level="low",
            review_basis_sha256="0" * 64,
        )
        artifact = Stage3ThoughtReviewArtifact(
            metadata=ThoughtAggregationMetadata(
                anime_title="It's MyGO!!!!!",
                series_id="its_mygo",
                timeline_id="main",
                canon_branch="main",
                episodes=[1],
            ),
            aggregation_model="test",
            threads=[thread],
        )

        links = story_thought_links(artifact, candidate_id, stale=True)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].character_name, "爱音")
        self.assertEqual(links[0].thought_aspect, "记忆")
        self.assertEqual(links[0].review_status, "completed")
        self.assertEqual(links[0].disposition, "publish")
        self.assertTrue(links[0].stale)


if __name__ == "__main__":
    unittest.main()
