"""按角色跨集 Character Thought 聚合测试。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from rag.models import CharacterId
from rag.pipeline.llm_client import LiteLLMConfig, LiteLLMJsonClient, LiteLLMJsonResponse
from rag.pipeline.review_models import ReviewMigrationReport, SourceFingerprint, complete_review
from rag.pipeline.schemas import (
    CharacterThoughtUpdateCandidate,
    SceneThoughtExtractionPass2B,
    Stage2BAnnotationArtifact,
    Stage2BSceneAnnotationResult,
)
from rag.pipeline.stage2_input_builder import load_stage2_input_artifact
from rag.pipeline.stage3_document_review import build_stage3_document_review_artifact
from rag.pipeline.stage3_rag_import import load_stage3_normalized_import_artifact
from rag.pipeline.stage3_thought_aggregation import build_stage3_thought_review_artifact
from rag.pipeline.stage3_thought_models import Stage3ThoughtReviewArtifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data"


def _fake_complete_json(
    client: LiteLLMJsonClient,
    prompt: str,
    stream: bool = False,
    status_callback: object | None = None,
    stream_callback: object | None = None,
) -> LiteLLMJsonResponse:
    """根据 Prompt 中的两个 Update ID 返回一条跨集 Thread。"""

    del client, stream, status_callback, stream_callback
    update_ids = list(dict.fromkeys(re.findall(r'"update_id": "([^"]+)"', prompt)))
    payload: dict[str, object] = {
        "character_id": CharacterId.SOYO.value,
        "threads": [
            {
                "canonical_subject": "CRYCHIC能否恢复",
                "thought_aspect": "恢复可能性判断",
                "states": [
                    {
                        "transition": "acquired",
                        "supporting_update_ids": [update_ids[0]],
                        "thought_text": "素世相信CRYCHIC仍然有恢复的可能。",
                        "epistemic_status": "believes",
                        "story_event_candidate_ids": [],
                        "event_fact_ids": [],
                        "tags": ["CRYCHIC"],
                        "retrieval_text": "素世相信CRYCHIC仍然可能恢复。",
                    },
                    {
                        "transition": "revised",
                        "supporting_update_ids": [update_ids[1]],
                        "thought_text": "素世相信必须主动重组CRYCHIC才能恢复。",
                        "epistemic_status": "believes",
                        "story_event_candidate_ids": [],
                        "event_fact_ids": [],
                        "tags": ["CRYCHIC"],
                        "retrieval_text": "素世认为恢复CRYCHIC需要主动重组。",
                    },
                ],
            }
        ],
        "unassigned_updates": [],
    }
    return LiteLLMJsonResponse(
        raw_content=json.dumps(payload, ensure_ascii=False),
        parsed_payload=payload,
    )


class TestStage3ThoughtAggregation(unittest.TestCase):
    """验证按角色完整上下文生成 Thread、时间线和审核迁移。"""

    def setUp(self) -> None:
        """构造同一角色前后修正观点的两条 Update。"""

        self.stage2 = load_stage2_input_artifact(DATA_ROOT / "annotations_stage2" / "ep01_stage2_input.json")
        normalized = load_stage3_normalized_import_artifact(DATA_ROOT / "annotations_stage3" / "ep01_rag_ready.json")
        self.rag_review, _ = build_stage3_document_review_artifact(
            normalized,
            [
                SourceFingerprint(role="stage2_input", episode=1, sha256="6" * 64),
                SourceFingerprint(role="stage2a_annotation", episode=1, sha256="7" * 64),
            ],
        )
        first_scene, second_scene = self.stage2.scenes[:2]
        first_update = CharacterThoughtUpdateCandidate(
            scene_id=first_scene.scene_id,
            update_local_id="restore_01",
            character_name="素世",
            thought_text="素世相信CRYCHIC仍有挽回余地。",
            subject_kind="standalone_topic",
            subject_text="CRYCHIC能否恢复",
            epistemic_status="believes",
            provisional_update_type="acquired",
            evidence_strength="explicit",
            evidence_u_ids=[first_scene.utterances[0].u_id],
            extraction_confidence=0.9,
        )
        second_update = CharacterThoughtUpdateCandidate(
            scene_id=second_scene.scene_id,
            update_local_id="restore_02",
            character_name="素世",
            thought_text="素世相信必须主动重组CRYCHIC。",
            subject_kind="standalone_topic",
            subject_text="CRYCHIC能否恢复",
            epistemic_status="believes",
            provisional_update_type="revised",
            evidence_strength="explicit",
            evidence_u_ids=[second_scene.utterances[0].u_id],
            extraction_confidence=0.9,
        )
        self.stage2b = Stage2BAnnotationArtifact(
            metadata=self.stage2.metadata,
            source_stage2a_model="test-stage2a",
            model="test-stage2b",
            template_path="test",
            results=[
                Stage2BSceneAnnotationResult(
                    scene_id=first_scene.scene_id,
                    annotation=SceneThoughtExtractionPass2B(
                        scene_id=first_scene.scene_id,
                        character_thought_updates=[first_update],
                    ),
                ),
                Stage2BSceneAnnotationResult(
                    scene_id=second_scene.scene_id,
                    annotation=SceneThoughtExtractionPass2B(
                        scene_id=second_scene.scene_id,
                        character_thought_updates=[second_update],
                    ),
                ),
            ],
        )
        self.sources = [
            SourceFingerprint(role="stage2_input", episode=1, sha256="8" * 64),
            SourceFingerprint(role="stage2b_annotation", episode=1, sha256="9" * 64),
            SourceFingerprint(role="stage3_rag", episode=1, sha256="a" * 64),
        ]

    def _build(
        self,
        previous: Stage3ThoughtReviewArtifact | None = None,
    ) -> tuple[Stage3ThoughtReviewArtifact, ReviewMigrationReport]:
        """使用固定 LLM response 构建 Thought Review。"""

        with patch.object(LiteLLMJsonClient, "complete_json", new=_fake_complete_json):
            return build_stage3_thought_review_artifact(
                [self.stage2],
                [self.stage2b],
                [self.rag_review],
                self.sources,
                LiteLLMConfig(model="test-model"),
                previous=previous,
            )

    def test_builds_one_thread_with_non_overlapping_states(self) -> None:
        """同一长期主题的修正应形成一个 Thread 中的两个连续状态。"""

        artifact, _ = self._build()
        self.assertEqual(len(artifact.threads), 1)
        thread = artifact.threads[0]
        self.assertEqual(len(thread.generated_sequence), 2)
        self.assertLess(
            thread.generated_sequence[0].visible_to,
            thread.generated_sequence[1].visible_from,
        )
        self.assertEqual(set(thread.covered_update_ids), {item.update_id for item in artifact.updates})

    def test_same_basis_migrates_thread_and_state_identity(self) -> None:
        """完整机器序列不变时应迁移 Thread、State 和人工处置。"""

        first, _ = self._build()
        complete_review(first.threads[0], "publish")
        previous_thread_id = first.threads[0].thought_thread_id
        previous_state_ids = [item.thought_state_id for item in first.threads[0].generated_sequence]
        second, report = self._build(previous=first)
        self.assertEqual(second.threads[0].thought_thread_id, previous_thread_id)
        self.assertEqual(
            [item.thought_state_id for item in second.threads[0].generated_sequence],
            previous_state_ids,
        )
        self.assertEqual(second.threads[0].disposition, "publish")
        self.assertIn(previous_thread_id, report.migrated_ids)


if __name__ == "__main__":
    unittest.main()
