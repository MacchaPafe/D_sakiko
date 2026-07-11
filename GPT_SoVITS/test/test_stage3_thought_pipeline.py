"""Stage 3 Character Thought 链接、时间线和风险测试。"""

from __future__ import annotations

from pathlib import Path
import json
import unittest
from unittest.mock import patch

from rag.models import CharacterId, CharacterThoughtDocument, CharacterThoughtQuery, RetrievalContext
from rag.pipeline.llm_client import LiteLLMConfig, LiteLLMJsonClient, LiteLLMJsonResponse
from rag.pipeline.schemas import (
    CharacterThoughtUpdateCandidate,
    EventFactCandidate,
    SceneThoughtExtractionPass2B,
    Stage2BAnnotationArtifact,
    Stage2BSceneAnnotationResult,
)
from rag.pipeline.stage2_input_builder import load_stage2_input_artifact
from rag.pipeline.stage3_rag_import import load_stage3_normalized_import_artifact
from rag.pipeline.stage3_thought_pipeline import (
    build_character_thought_point_records,
    build_stage3_thought_import_artifact,
)
from rag.services import QdrantRagService, RagServiceConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data"


def _fake_link_complete_json(
    client: LiteLLMJsonClient,
    prompt: str,
    stream: bool = False,
    status_callback: object | None = None,
    stream_callback: object | None = None,
) -> LiteLLMJsonResponse:
    """把测试中的未解析观点判定为无需事件来源的独立主题。"""

    del client, stream, status_callback, stream_callback
    self_marker = "source_local_id: `"
    marker_index = prompt.index(self_marker) + len(self_marker)
    source_local_id = prompt[marker_index : prompt.index("`", marker_index)]
    payload: dict[str, object] = {
        "source_local_id": source_local_id,
        "link_status": "standalone",
        "target_kind": "standalone_topic",
        "target_id": None,
        "thought_aspect": "退出原因的个人判断",
        "link_confidence": 0.82,
        "reason_brief": "该观点未明确指向候选事件。",
    }
    return LiteLLMJsonResponse(
        raw_content=json.dumps(payload, ensure_ascii=False),
        parsed_payload=payload,
    )


def _thought_update(
    scene_id: str,
    local_id: str,
    thought_text: str,
    subject_kind: str,
    subject_text: str,
    evidence_u_id: str,
    about_event_local_id: str | None = None,
    about_fact_local_id: str | None = None,
) -> CharacterThoughtUpdateCandidate:
    """构造测试用的 Stage 2B 观点更新。"""

    return CharacterThoughtUpdateCandidate(
        scene_id=scene_id,
        update_local_id=local_id,
        character_name="素世",
        thought_text=thought_text,
        subject_kind=subject_kind,  # type: ignore[arg-type]
        subject_text=subject_text,
        epistemic_status="believes",
        provisional_update_type="unspecified",
        evidence_strength="explicit",
        evidence_u_ids=[evidence_u_id],
        about_event_local_id=about_event_local_id,
        about_fact_local_id=about_fact_local_id,
        extraction_confidence=0.9,
    )


class TestStage3ThoughtPipeline(unittest.TestCase):
    """验证保守链接、跨场景状态投影和入库门禁。"""

    def setUp(self) -> None:
        """载入现有第 1 集 Stage 2 与 Stage 3 样例。"""

        self.stage2 = load_stage2_input_artifact(
            DATA_ROOT / "annotations_stage2" / "ep01_stage2_input.json"
        )
        self.stage3 = load_stage3_normalized_import_artifact(
            DATA_ROOT / "annotations_stage3" / "ep01_rag_ready.json"
        )

    def _build_stage2b(self) -> Stage2BAnnotationArtifact:
        """构造同时含精确链接、独立主题、修正和未解析链接的产物。"""

        first_scene = self.stage2.scenes[0]
        second_scene = self.stage2.scenes[1]
        first_u_id = first_scene.utterances[0].u_id
        second_u_id = second_scene.utterances[0].u_id
        first_annotation = SceneThoughtExtractionPass2B(
            scene_id=first_scene.scene_id,
            event_facts=[
                EventFactCandidate(
                    scene_id=first_scene.scene_id,
                    fact_local_id="fact_01",
                    event_local_id="event_01",
                    fact_text="祥子宣布退出CRYCHIC。",
                    evidence_u_ids=[first_u_id],
                    confidence=0.95,
                )
            ],
            character_thought_updates=[
                _thought_update(
                    first_scene.scene_id,
                    "update_fact",
                    "素世知道祥子宣布退出CRYCHIC。",
                    "event_fact",
                    "祥子退出CRYCHIC",
                    first_u_id,
                    about_event_local_id="event_01",
                    about_fact_local_id="fact_01",
                ),
                _thought_update(
                    first_scene.scene_id,
                    "update_self_1",
                    "素世相信CRYCHIC仍有挽回余地。",
                    "standalone_topic",
                    "CRYCHIC是否还能挽回",
                    first_u_id,
                ),
                _thought_update(
                    first_scene.scene_id,
                    "update_unresolved",
                    "素世认为某件事另有原因。",
                    "event",
                    "无法确定的历史事件",
                    first_u_id,
                ),
            ],
        )
        second_annotation = SceneThoughtExtractionPass2B(
            scene_id=second_scene.scene_id,
            character_thought_updates=[
                _thought_update(
                    second_scene.scene_id,
                    "update_self_2",
                    "素世相信必须重组CRYCHIC。",
                    "standalone_topic",
                    "CRYCHIC是否还能挽回",
                    second_u_id,
                )
            ],
        )
        return Stage2BAnnotationArtifact(
            metadata=self.stage2.metadata,
            source_stage2a_model="test-stage2a",
            model="test-stage2b",
            template_path="thought_extraction_pass.jinja",
            results=[
                Stage2BSceneAnnotationResult(scene_id=first_scene.scene_id, annotation=first_annotation),
                Stage2BSceneAnnotationResult(scene_id=second_scene.scene_id, annotation=second_annotation),
            ],
        )

    def test_link_timeline_risk_and_import_gate(self) -> None:
        """精确引用应链接，修正应关闭旧状态，未解析记录不得入库。"""

        artifact = build_stage3_thought_import_artifact(
            self.stage2,
            self._build_stage2b(),
            self.stage3,
        )
        self.assertEqual(len(artifact.event_facts), 1)
        self.assertIsNotNone(artifact.event_facts[0].about_event_id)
        unresolved = [record for record in artifact.character_thoughts if record.link_status == "unresolved"]
        self.assertEqual(len(unresolved), 1)
        self.assertIsNone(unresolved[0].document)
        self.assertEqual(unresolved[0].risk_level, "high")

        standalone = [
            record
            for record in artifact.character_thoughts
            if record.document is not None and record.document.subject_kind == "standalone_topic"
        ]
        standalone.sort(key=lambda record: record.document.valid_from if record.document is not None else 0)
        self.assertEqual(len(standalone), 2)
        assert standalone[0].document is not None
        assert standalone[1].document is not None
        self.assertLess(standalone[0].document.valid_to, standalone[1].document.valid_from)
        self.assertEqual(standalone[1].resolved_update_type, "revised")

        self.assertEqual(build_character_thought_point_records(artifact), [])
        standalone[1].review_status = "approved"
        records = build_character_thought_point_records(artifact)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].document.character_id, CharacterId.SOYO)

    def test_runtime_constraint_rejects_other_character_and_future_state(self) -> None:
        """运行时补充约束应同时隔离角色和观点有效期。"""

        document = CharacterThoughtDocument(
            character_id=CharacterId.SOYO,
            series_id=self.stage3.metadata.series_id,
            season_id=self.stage3.metadata.season_id,
            canon_branch=self.stage3.metadata.canon_branch,
            thought_thread_key="soyo:standalone:test",
            subject_kind="standalone_topic",
            about_event_id=None,
            about_fact_id=None,
            standalone_topic_key="test",
            thought_text="素世认为这是测试观点。",
            epistemic_status="believes",
            valid_from=10,
            valid_to=20,
            tags=[],
            retrieval_text="素世认为这是测试观点",
            source_scene_ids=["ep01_s001"],
            evidence_u_ids=["ep01_u0001"],
            evidence_strength="explicit",
            extraction_confidence=0.9,
            link_confidence=1.0,
        )
        service = QdrantRagService(RagServiceConfig(qdrant_connect_type="memory"))
        self.assertTrue(
            service._character_thought_matches(  # pylint: disable=protected-access
                document,
                RetrievalContext(current_time=15, current_character_id=CharacterId.SOYO),
                CharacterThoughtQuery(),
            )
        )
        self.assertFalse(
            service._character_thought_matches(  # pylint: disable=protected-access
                document,
                RetrievalContext(current_time=15, current_character_id=CharacterId.TOMORI),
                CharacterThoughtQuery(),
            )
        )
        self.assertFalse(
            service._character_thought_matches(  # pylint: disable=protected-access
                document,
                RetrievalContext(current_time=21, current_character_id=CharacterId.SOYO),
                CharacterThoughtQuery(),
            )
        )

    def test_llm_linker_can_mark_update_as_standalone(self) -> None:
        """受限候选链接器应允许模型明确选择与具体剧情无关的独立主题。"""

        with patch.object(LiteLLMJsonClient, "complete_json", new=_fake_link_complete_json):
            artifact = build_stage3_thought_import_artifact(
                self.stage2,
                self._build_stage2b(),
                self.stage3,
                linker_llm_config=LiteLLMConfig(model="test-linker"),
            )
        resolved = [
            update
            for update in artifact.linked_updates
            if update.source_local_id == "update_unresolved"
        ]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].link_status, "standalone")
        self.assertEqual(resolved[0].subject_kind, "standalone_topic")
        self.assertEqual(resolved[0].link_confidence, 0.82)


if __name__ == "__main__":
    unittest.main()
