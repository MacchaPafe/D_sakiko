"""角色关系观察与长期状态聚合的单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
import unittest
from unittest.mock import patch

from rag.pipeline.llm_client import LiteLLMConfig
from rag.pipeline.schemas import (
    RelationObservationCandidate,
    SceneAnnotationPass2,
    Stage2AnnotationArtifact,
    Stage2InputArtifact,
    Stage2SceneAnnotationResult,
    Stage3RelationAggregationArtifact,
)
from rag.pipeline.stage2_document_extraction import load_stage2_annotation_artifact
from rag.pipeline.stage2_input_builder import load_stage2_input_artifact
from rag.pipeline.stage3_rag_import import (
    backfill_existing_stage3_character_relations,
    build_stage3_normalized_import_artifact,
)
from rag.pipeline.stage3_relation_aggregation import (
    build_stage3_relation_aggregation_artifact,
    build_stage3_relation_aggregation_artifact_from_many,
    render_relation_aggregation_prompt,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_STAGE2_INPUT_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json"
SAMPLE_PASS2_RAW_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json"


@dataclass(frozen=True, slots=True)
class _FakeCompletionPayload:
    """保存测试替身返回的 JSON 解析结果。"""

    parsed_payload: dict[str, object]


class Stage3RelationAggregationTest(unittest.TestCase):
    """验证场景关系观察迁移、聚合和入库投影。"""

    def test_legacy_character_relations_migrate_to_observations(self) -> None:
        """旧版 Stage 2 JSON 应自动迁移且新序列化只写新字段。"""

        artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        first_annotation = artifact.results[0].annotation
        self.assertIsNotNone(first_annotation)
        assert first_annotation is not None
        self.assertGreater(len(first_annotation.relation_observations), 0)
        observation = first_annotation.relation_observations[0]
        self.assertEqual(observation.observation_local_id, "rel_01")
        self.assertEqual(observation.evidence_strength, "inferred")
        dumped = first_annotation.model_dump(mode="json")
        self.assertIn("relation_observations", dumped)
        self.assertNotIn("character_relations", dumped)

    def test_render_prompt_uses_observations_and_long_term_rules(self) -> None:
        """聚合 Prompt 应明确局部观察与长期状态的边界。"""

        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        observation = self._build_annotation_artifact(input_artifact).results[0].annotation
        assert observation is not None
        candidate = observation.relation_observations[0]
        review_artifact = self._build_review_artifact_with_fake_llm(input_artifact)
        prompt = render_relation_aggregation_prompt(
            subject_character_name=candidate.subject_character_name,
            object_character_name=candidate.object_character_name,
            observations=review_artifact.observations,
        )
        self.assertIn("Observation 是局部证据，不等于长期关系", prompt)
        self.assertIn("relation_type_key", prompt)
        self.assertIn("unmerged_observations", prompt)

    def test_aggregate_states_backfills_windows_and_projects_import(self) -> None:
        """同类型状态应按首次证据时间回填，并能投影到现有导入结构。"""

        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = self._build_annotation_artifact(input_artifact)
        relation_artifact = self._build_review_artifact_with_fake_llm(
            input_artifact,
            annotation_artifact,
        )

        states = relation_artifact.character_relation_states
        self.assertEqual(len(states), 2)
        first_state = states[0].document
        second_state = states[1].document
        self.assertIsNotNone(first_state)
        self.assertIsNotNone(second_state)
        assert first_state is not None
        assert second_state is not None
        self.assertEqual(first_state.visible_from, 4000)
        self.assertEqual(first_state.visible_to, 4001)
        self.assertEqual(second_state.visible_from, 4002)
        self.assertEqual(second_state.visible_to, 999999)
        self.assertEqual(len(relation_artifact.unmerged_observations), 1)
        self.assertEqual(relation_artifact.unmerged_observations[0].risk_level, "low")

        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
            relation_aggregation_artifact=relation_artifact,
        )
        self.assertEqual(len(normalized.character_relations), 2)
        first_record = normalized.character_relations[0]
        self.assertEqual(first_record.relation_type_key, "general_bond")
        self.assertEqual(first_record.document.relation_label, "general_bond")
        self.assertEqual(first_record.supporting_observation_ids, states[0].supporting_observation_ids)

        independent_record = first_record.model_copy(deep=True)
        independent_record.point_id = "independent-speech-style"
        independent_record.relation_type_key = "speech_style"
        independent_record.document.relation_label = "speech_style"
        independent_record.document.visible_to = 999999
        first_record.document.visible_to = 999999
        normalized.character_relations.append(independent_record)
        backfill_existing_stage3_character_relations(normalized)
        self.assertEqual(first_record.document.visible_to, 4001)
        self.assertEqual(independent_record.document.visible_to, 999999)

    def test_multi_episode_build_rejects_mixed_scope(self) -> None:
        """跨集全量构建不得混入其他季度或剧情分支。"""

        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        other_scope = input_artifact.model_copy(deep=True)
        other_scope.metadata.season_id = input_artifact.metadata.season_id + 1
        annotation_artifact = self._build_annotation_artifact(input_artifact)
        with self.assertRaisesRegex(ValueError, "同一系列、季度与剧情分支"):
            build_stage3_relation_aggregation_artifact_from_many(
                input_artifacts=[input_artifact, other_scope],
                annotation_artifacts=[annotation_artifact, annotation_artifact],
                llm_config=LiteLLMConfig(model="test-model"),
            )

    def _build_review_artifact_with_fake_llm(
        self,
        input_artifact: Stage2InputArtifact,
        annotation_artifact: Stage2AnnotationArtifact | None = None,
    ) -> Stage3RelationAggregationArtifact:
        """使用可预测的 LLM 替身构造关系聚合审查产物。"""

        if annotation_artifact is None:
            annotation_artifact = self._build_annotation_artifact(input_artifact)

        def fake_complete(prompt: str) -> _FakeCompletionPayload:
            """根据 Prompt 中的稳定 Observation ID 返回两段长期状态。"""

            observation_ids = list(dict.fromkeys(re.findall(r"relation_observation:[0-9a-f]+", prompt)))
            self.assertEqual(len(observation_ids), 3)
            return _FakeCompletionPayload(
                parsed_payload={
                    "subject_character_name": "素世",
                    "object_character_name": "祥子",
                    "states": [
                        {
                            "relation_type_key": "general_bond",
                            "summary": "素世仍然关心祥子，并试图维持亲近联系。",
                            "speech_hint": "温和并带有挽留意味",
                            "object_character_nickname": "小祥",
                            "supporting_observation_ids": [observation_ids[0]],
                            "confidence": 0.9,
                            "ambiguity_notes": "",
                        },
                        {
                            "relation_type_key": "general_bond",
                            "summary": "素世对祥子转为谨慎疏远，不再主动挽留。",
                            "speech_hint": "克制而疏离",
                            "object_character_nickname": "祥子",
                            "supporting_observation_ids": [observation_ids[2]],
                            "confidence": 0.9,
                            "ambiguity_notes": "",
                        },
                    ],
                    "unmerged_observations": [
                        {
                            "observation_id": observation_ids[1],
                            "reason": "只针对当前事件的短暂情绪。",
                        }
                    ],
                }
            )

        with patch(
            "rag.pipeline.stage3_relation_aggregation.LiteLLMJsonClient.complete_json",
            side_effect=fake_complete,
        ):
            return build_stage3_relation_aggregation_artifact(
                input_artifact=input_artifact,
                annotation_artifact=annotation_artifact,
                llm_config=LiteLLMConfig(model="test-model"),
            )

    def _build_annotation_artifact(
        self,
        input_artifact: Stage2InputArtifact,
    ) -> Stage2AnnotationArtifact:
        """为三个连续场景构造同一有向角色对的观察。"""

        scenes = input_artifact.scenes[:3]
        results: list[Stage2SceneAnnotationResult] = []
        observation_texts = [
            "素世担心祥子并试图挽留她。",
            "素世因祥子拒绝沟通而短暂生气。",
            "素世面对祥子时变得克制疏离。",
        ]
        for index, scene in enumerate(scenes, start=1):
            observation = RelationObservationCandidate(
                scene_id=scene.scene_id,
                observation_local_id=f"rel_obs_{index:02d}",
                subject_character_name="素世",
                object_character_name="祥子",
                observation_text=observation_texts[index - 1],
                speech_hint="",
                object_character_nickname="",
                evidence_u_ids=[scene.utterances[0].u_id],
                evidence_strength="explicit",
                confidence=0.9,
                ambiguity_notes="",
            )
            results.append(
                Stage2SceneAnnotationResult(
                    scene_id=scene.scene_id,
                    annotation=SceneAnnotationPass2(
                        scene_id=scene.scene_id,
                        relation_observations=[observation],
                    ),
                )
            )
        return Stage2AnnotationArtifact(
            metadata=input_artifact.metadata,
            model="stage2-test-model",
            template_path="test-template",
            results=results,
        )


if __name__ == "__main__":
    unittest.main()
