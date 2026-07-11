"""Stage 2B 角色观点抽取测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from rag.pipeline.llm_client import LiteLLMConfig, LiteLLMJsonClient, LiteLLMJsonResponse
from rag.pipeline.schemas import CharacterThoughtUpdateCandidate
from rag.pipeline.stage1_speaker_annotation import (
    load_stage1_annotation_artifact,
    load_stage1_prepared_artifact,
)
from rag.pipeline.stage2_document_extraction import load_stage2_annotation_artifact
from rag.pipeline.stage2_input_builder import build_stage2_input_artifact, load_stage2_input_artifact
from rag.pipeline.stage2b_thought_extraction import (
    annotate_stage2b_artifact,
    load_stage2b_annotation_artifact,
    render_stage2b_prompt,
    save_stage2b_annotation_artifact,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DATA = PROJECT_ROOT / "GPT_SoVITS" / "rag" / "pipeline" / "data"


def _fake_complete_json(
    client: LiteLLMJsonClient,
    prompt: str,
    stream: bool = False,
    status_callback: object | None = None,
    stream_callback: object | None = None,
) -> LiteLLMJsonResponse:
    """返回一条结构合法的固定 Stage 2B 响应。"""

    del client, stream, status_callback, stream_callback
    scene_marker = '"scene_id": "'
    marker_index = prompt.index(scene_marker) + len(scene_marker)
    scene_id = prompt[marker_index : prompt.index('"', marker_index)]
    payload: dict[str, object] = {
        "scene_id": scene_id,
        "event_facts": [
            {
                "scene_id": scene_id,
                "fact_local_id": "fact_01",
                "event_local_id": "event_01",
                "fact_text": "祥子表示自己要退出CRYCHIC。",
                "tags": ["退出", "CRYCHIC"],
                "evidence_u_ids": ["ep01_u0006"],
                "evidence_s_ids": [],
                "confidence": 0.98,
            }
        ],
        "character_thought_updates": [
            {
                "scene_id": scene_id,
                "update_local_id": "thought_01",
                "character_name": "素世",
                "thought_text": "素世知道祥子决定退出CRYCHIC，并希望挽留她。",
                "subject_kind": "event_fact",
                "subject_text": "祥子退出CRYCHIC",
                "epistemic_status": "knows",
                "provisional_update_type": "acquired",
                "evidence_strength": "explicit",
                "evidence_u_ids": ["ep01_u0006"],
                "about_event_local_id": "event_01",
                "about_fact_local_id": "fact_01",
                "effective_from_hint": "current_scene",
                "inference_note": None,
                "ambiguity_notes": [],
                "extraction_confidence": 0.95,
            }
        ],
    }
    raw_content = json.dumps(payload, ensure_ascii=False)
    return LiteLLMJsonResponse(raw_content=raw_content, parsed_payload=payload)


class TestStage2BThoughtExtraction(unittest.TestCase):
    """验证 Stage 2B 输入保真、提示词和批量产物。"""

    def test_stage2_input_preserves_speaker_evidence_fields(self) -> None:
        """Stage 1 的说话人置信度与内心独白标记应进入 Stage 2。"""

        prepared = load_stage1_prepared_artifact(
            PIPELINE_DATA / "annotations_stage1" / "ep01_prepared.json"
        )
        annotation = load_stage1_annotation_artifact(
            PIPELINE_DATA / "annotations_stage1" / "ep01_pass1_raw.json"
        )
        stage2 = build_stage2_input_artifact(prepared, annotation)
        source_by_id = {
            item.u_id: item
            for result in annotation.results
            if result.annotation is not None
            for item in result.annotation.utterance_annotations
        }

        for scene in stage2.scenes:
            for utterance in scene.utterances:
                source = source_by_id[utterance.u_id]
                self.assertEqual(utterance.speaker_confidence, source.speaker_confidence)
                self.assertEqual(utterance.is_inner_monologue, source.is_inner_monologue)

    def test_prompt_contains_scene_and_stage2a_events(self) -> None:
        """Stage 2B prompt 应同时包含完整场景和 Stage 2A 事件候选。"""

        stage2 = load_stage2_input_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_stage2_input.json"
        )
        stage2a = load_stage2_annotation_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_pass2_raw.json"
        )
        first_result = stage2a.results[0]
        self.assertIsNotNone(first_result.annotation)
        assert first_result.annotation is not None
        prompt = render_stage2b_prompt(stage2.scenes[0], first_result.annotation.story_events)

        self.assertIn(stage2.scenes[0].scene_id, prompt)
        self.assertIn(first_result.annotation.story_events[0].title, prompt)
        self.assertIn("speaker_confidence", prompt)
        self.assertIn("inner_monologue", prompt)

    def test_batch_artifact_round_trip(self) -> None:
        """批量抽取结果应通过 schema 校验并可保存后重新读取。"""

        stage2 = load_stage2_input_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_stage2_input.json"
        )
        stage2a = load_stage2_annotation_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_pass2_raw.json"
        )
        with patch.object(LiteLLMJsonClient, "complete_json", new=_fake_complete_json):
            artifact = annotate_stage2b_artifact(
                input_artifact=stage2,
                stage2a_artifact=stage2a,
                llm_config=LiteLLMConfig(model="test-model"),
                max_scenes=1,
            )

        self.assertEqual(len(artifact.results), 1)
        self.assertIsNotNone(artifact.results[0].annotation)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "pass2b.json"
            save_stage2b_annotation_artifact(artifact, output_path)
            reloaded = load_stage2b_annotation_artifact(output_path)
        self.assertEqual(reloaded, artifact)

    def test_inferred_thought_requires_inference_note(self) -> None:
        """推断型观点缺少推断说明时应被 schema 拒绝。"""

        with self.assertRaises(ValidationError):
            CharacterThoughtUpdateCandidate(
                scene_id="ep01_s001",
                update_local_id="thought_01",
                character_name="素世",
                thought_text="素世可能认为祥子另有隐情。",
                subject_kind="standalone_topic",
                subject_text="祥子退出的原因",
                epistemic_status="suspects",
                provisional_update_type="acquired",
                evidence_strength="inferred",
                evidence_u_ids=["ep01_u0001"],
                extraction_confidence=0.5,
            )

    def test_long_scene_uses_overlapping_windows_and_deduplicates(self) -> None:
        """超过 prompt 限制时应切分重叠窗口并合并重复抽取结果。"""

        stage2 = load_stage2_input_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_stage2_input.json"
        )
        stage2a = load_stage2_annotation_artifact(
            PIPELINE_DATA / "annotations_stage2" / "ep01_pass2_raw.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(LiteLLMJsonClient, "complete_json", new=_fake_complete_json):
                artifact = annotate_stage2b_artifact(
                    input_artifact=stage2,
                    stage2a_artifact=stage2a,
                    llm_config=LiteLLMConfig(model="test-model"),
                    max_scenes=1,
                    prompts_dir=directory,
                    max_prompt_chars=1,
                    window_utterances=10,
                    window_overlap=2,
                )
        result = artifact.results[0]
        self.assertGreater(len(result.prompt_paths), 1)
        self.assertIsNotNone(result.annotation)
        assert result.annotation is not None
        self.assertEqual(len(result.annotation.event_facts), 1)
        self.assertEqual(len(result.annotation.character_thought_updates), 1)


if __name__ == "__main__":
    unittest.main()
