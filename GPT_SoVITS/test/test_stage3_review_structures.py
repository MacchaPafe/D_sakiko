"""Stage 3 Relation/Thought 结构审核操作测试。"""

from __future__ import annotations

import unittest

from rag.models import CharacterId
from rag.pipeline.schemas import (
    RelationObservationReviewRecord,
    Stage3RelationAggregationMetadata,
)
from rag.pipeline.stage3_relation_models import (
    RelationStateDraft,
    RelationTypeContentDraft,
    RelationTypeReviewRecord,
    Stage3RelationReviewArtifact,
    UnmergedRelationObservationDecision,
)
from rag.pipeline.stage3_review_operations import (
    AssignRelationObservationCommand,
    AssignThoughtUpdateCommand,
    CompleteItemReviewCommand,
    MergeRelationTypesCommand,
    MergeThoughtThreadsCommand,
    SplitRelationTypeCommand,
    SplitThoughtThreadCommand,
    apply_review_command,
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


_TYPE_ONE = "relation_type:11111111-1111-4111-8111-111111111111"
_TYPE_TWO = "relation_type:22222222-2222-4222-8222-222222222222"
_STATE_ONE = "relation_state:11111111-1111-4111-8111-111111111111"
_STATE_TWO = "relation_state:22222222-2222-4222-8222-222222222222"
_OBS_ONE = "relation_observation:one"
_OBS_TWO = "relation_observation:two"
_THREAD_ONE = "thought_thread:11111111-1111-4111-8111-111111111111"
_THREAD_TWO = "thought_thread:22222222-2222-4222-8222-222222222222"
_THOUGHT_STATE_ONE = "thought_state:11111111-1111-4111-8111-111111111111"
_THOUGHT_STATE_TWO = "thought_state:22222222-2222-4222-8222-222222222222"
_UPDATE_ONE = "thought_update:one"
_UPDATE_TWO = "thought_update:two"


def _relation_artifact(unmerged_second: bool = False) -> Stage3RelationReviewArtifact:
    """构造包含两个同向 Observation 的最小 Relation Review。"""

    observations = [
        RelationObservationReviewRecord(
            observation_id=_OBS_ONE,
            scene_id="scene_1",
            time_order=10,
            subject_character_id=CharacterId.SOYO,
            object_character_id=CharacterId.SAKIKO,
            observation_text="素世仍想联系祥子。",
            evidence_strength="explicit",
            confidence=0.9,
        ),
        RelationObservationReviewRecord(
            observation_id=_OBS_TWO,
            scene_id="scene_2",
            time_order=20,
            subject_character_id=CharacterId.SOYO,
            object_character_id=CharacterId.SAKIKO,
            observation_text="素世再次尝试联系祥子。",
            evidence_strength="explicit",
            confidence=0.9,
        ),
    ]
    states = [
        RelationStateDraft(
            relation_state_id=_STATE_ONE,
            supporting_observation_ids=[_OBS_ONE],
            state_summary="尝试恢复联系。",
            visible_from=10,
            visible_to=19,
            retrieval_text="素世尝试恢复与祥子的联系。",
        )
    ]
    if not unmerged_second:
        states.append(
            RelationStateDraft(
                relation_state_id=_STATE_TWO,
                supporting_observation_ids=[_OBS_TWO],
                state_summary="持续寻求联系。",
                visible_from=20,
                visible_to=999999,
                retrieval_text="素世持续寻求与祥子联系。",
            )
        )
    return Stage3RelationReviewArtifact(
        metadata=Stage3RelationAggregationMetadata(
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            story_year=3,
            canon_branch="main",
            episodes=[1, 2],
        ),
        aggregation_model="test",
        observations=observations,
        relation_types=[
            RelationTypeReviewRecord(
                relation_type_id=_TYPE_ONE,
                subject_character_id=CharacterId.SOYO,
                object_character_id=CharacterId.SAKIKO,
                series_id="its_mygo",
                timeline_id="bang_dream_original",
                canon_branch="main",
                covered_observation_ids=[_OBS_ONE, _OBS_TWO],
                generated_content=RelationTypeContentDraft(
                    semantic_label="seeking_contact",
                    states=states,
                ),
                review_basis_sha256="1" * 64,
            )
        ],
        unmerged_observations=(
            [
                UnmergedRelationObservationDecision(
                    observation_id=_OBS_TWO,
                    generated_reason="尚未归线",
                    review_basis_sha256="2" * 64,
                )
            ]
            if unmerged_second
            else []
        ),
    )


def _thought_artifact(unassigned_second: bool = False) -> Stage3ThoughtReviewArtifact:
    """构造包含两个同角色 Update 的最小 Thought Review。"""

    updates = [
        ThoughtUpdateEvidence(
            update_id=_UPDATE_ONE,
            source_scene_id="scene_1",
            source_local_id="thought_1",
            character_id=CharacterId.SOYO,
            thought_text="CRYCHIC 仍可能恢复。",
            subject_kind="standalone_topic",
            subject_text="CRYCHIC 能否恢复",
            epistemic_status="believes",
            provisional_update_type="acquired",
            evidence_strength="explicit",
            extraction_confidence=0.9,
            evidence_time=10,
        ),
        ThoughtUpdateEvidence(
            update_id=_UPDATE_TWO,
            source_scene_id="scene_2",
            source_local_id="thought_2",
            character_id=CharacterId.SOYO,
            thought_text="必须主动重组 CRYCHIC。",
            subject_kind="standalone_topic",
            subject_text="CRYCHIC 能否恢复",
            epistemic_status="believes",
            provisional_update_type="revised",
            evidence_strength="explicit",
            extraction_confidence=0.9,
            evidence_time=20,
        ),
    ]
    states = [
        ThoughtStateDraft(
            thought_state_id=_THOUGHT_STATE_ONE,
            transition="acquired",
            supporting_update_ids=[_UPDATE_ONE],
            thought_text="CRYCHIC 仍可能恢复。",
            epistemic_status="believes",
            visible_from=10,
            visible_to=19,
            retrieval_text="素世相信 CRYCHIC 仍可能恢复。",
        )
    ]
    if not unassigned_second:
        states.append(
            ThoughtStateDraft(
                thought_state_id=_THOUGHT_STATE_TWO,
                transition="revised",
                supporting_update_ids=[_UPDATE_TWO],
                thought_text="必须主动重组 CRYCHIC。",
                epistemic_status="believes",
                visible_from=20,
                visible_to=999999,
                retrieval_text="素世相信必须主动重组 CRYCHIC。",
            )
        )
    return Stage3ThoughtReviewArtifact(
        metadata=ThoughtAggregationMetadata(
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            timeline_id="bang_dream_original",
            story_year=3,
            canon_branch="main",
            episodes=[1, 2],
        ),
        aggregation_model="test",
        updates=updates,
        threads=[
            ThoughtThreadReviewRecord(
                thought_thread_id=_THREAD_ONE,
                character_id=CharacterId.SOYO,
                series_id="its_mygo",
                timeline_id="bang_dream_original",
                canon_branch="main",
                covered_update_ids=[_UPDATE_ONE, _UPDATE_TWO],
                generated_content=ThoughtThreadContentDraft(
                    canonical_subject="CRYCHIC 能否恢复",
                    thought_aspect="恢复可能性判断",
                    states=states,
                ),
                review_basis_sha256="3" * 64,
            )
        ],
        unassigned_updates=(
            [
                UnassignedThoughtUpdateDecision(
                    update_id=_UPDATE_TWO,
                    kind="unresolved",
                    generated_reason="尚未归线",
                    review_basis_sha256="4" * 64,
                )
            ]
            if unassigned_second
            else []
        ),
    )


class TestStage3ReviewStructures(unittest.TestCase):
    """验证长期 Relation/Thought 的归线、拆分、合并和发布门槛。"""

    def test_assigns_unmerged_observation_once(self) -> None:
        """未归属 Observation 应原子移入目标 State 并删除旧决定。"""

        artifact = _relation_artifact(unmerged_second=True)
        updated = apply_review_command(
            artifact,
            AssignRelationObservationCommand(_OBS_TWO, _TYPE_ONE, 0),
        )
        self.assertFalse(updated.unmerged_observations)
        self.assertEqual(
            set(updated.relation_types[0].effective_sequence()[0].supporting_observation_ids),
            {_OBS_ONE, _OBS_TWO},
        )

    def test_splits_and_merges_relation_type_with_stable_primary_identity(self) -> None:
        """拆分后原 Type 保留身份，合并后被合并 Type 明确拒绝。"""

        split = apply_review_command(
            _relation_artifact(),
            SplitRelationTypeCommand(_TYPE_ONE, 1, _TYPE_TWO, "seeking_contact"),
        )
        self.assertEqual(len(split.relation_types), 2)
        merged = apply_review_command(
            split,
            MergeRelationTypesCommand(_TYPE_ONE, _TYPE_TWO, "seeking_contact"),
        )
        primary = next(item for item in merged.relation_types if item.relation_type_id == _TYPE_ONE)
        secondary = next(item for item in merged.relation_types if item.relation_type_id == _TYPE_TWO)
        self.assertEqual(len(primary.effective_sequence()), 2)
        self.assertEqual(secondary.disposition, "reject")

    def test_assigns_unassigned_update_once(self) -> None:
        """未归属 Update 应原子移入同角色 Thread 并删除旧决定。"""

        updated = apply_review_command(
            _thought_artifact(unassigned_second=True),
            AssignThoughtUpdateCommand(_UPDATE_TWO, _THREAD_ONE, 0),
        )
        self.assertFalse(updated.unassigned_updates)
        self.assertEqual(
            set(updated.threads[0].effective_sequence()[0].supporting_update_ids),
            {_UPDATE_ONE, _UPDATE_TWO},
        )

    def test_splits_and_merges_thought_thread_with_stable_primary_identity(self) -> None:
        """拆分后原 Thread 保留身份，合并后被合并 Thread 明确拒绝。"""

        split = apply_review_command(
            _thought_artifact(),
            SplitThoughtThreadCommand(
                _THREAD_ONE,
                1,
                _THREAD_TWO,
                "CRYCHIC 能否恢复",
                "恢复方法判断",
            ),
        )
        self.assertEqual(len(split.threads), 2)
        merged = apply_review_command(
            split,
            MergeThoughtThreadsCommand(
                _THREAD_ONE,
                _THREAD_TWO,
                "CRYCHIC 能否恢复",
                "恢复可能性判断",
            ),
        )
        primary = next(item for item in merged.threads if item.thought_thread_id == _THREAD_ONE)
        secondary = next(item for item in merged.threads if item.thought_thread_id == _THREAD_TWO)
        self.assertEqual(len(primary.effective_sequence()), 2)
        self.assertEqual(secondary.disposition, "reject")

    def test_unassigned_items_cannot_be_published(self) -> None:
        """未归属 Observation/Update 必须拒绝或排除，不能直接发布。"""

        with self.assertRaises(ValueError):
            apply_review_command(
                _relation_artifact(unmerged_second=True),
                CompleteItemReviewCommand(_OBS_TWO, "publish"),
            )
        with self.assertRaises(ValueError):
            apply_review_command(
                _thought_artifact(unassigned_second=True),
                CompleteItemReviewCommand(_UPDATE_TWO, "publish"),
            )


if __name__ == "__main__":
    unittest.main()
