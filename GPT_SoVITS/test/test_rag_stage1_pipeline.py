import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.pipeline.cli import main as pipeline_cli_main
from rag.pipeline.llm_client import LiteLLMConfig, LiteLLMJsonClient
from rag.pipeline.scene_segmenter import segment_scenes
from rag.pipeline.stage1_speaker_annotation import (
    annotate_prepared_stage1_artifact,
    load_stage1_annotation_artifact,
    load_stage1_prepared_artifact,
    prepare_stage1_artifact,
    prepare_stage1_scenes,
    render_stage1_prompt,
)
from rag.pipeline.stage2_input_builder import (
    build_stage2_input_artifact,
    derive_stage2_input_output_path,
    load_stage2_input_artifact,
)
from rag.pipeline.stage2_document_extraction import (
    annotate_stage2_input_artifact,
    load_stage2_annotation_artifact,
    render_stage2_prompt,
)
from rag.pipeline.stage3_rag_import import (
    backfill_existing_stage3_character_relations,
    build_stage3_normalized_import_artifact,
    load_stage3_normalized_import_artifact,
    save_stage3_normalized_import_artifact,
)
from rag.pipeline.subtitle_loader import (
    build_screen_text_units,
    build_utterance_units,
    clean_ass_text,
    extract_episode_number,
    load_relevant_subtitle_lines,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_SUBTITLE_PATH = ROOT_DIR / "[Nekomoe kissaten] BanG Dream! It’s MyGO!!!!! [01][BDRip].JPSC.ass"
SAMPLE_PREPARED_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json"
SAMPLE_PASS1_RAW_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json"
SAMPLE_STAGE2_INPUT_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json"
SAMPLE_PASS2_RAW_PATH = ROOT_DIR / "GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json"


class RagStage1PipelineTest(unittest.TestCase):
    def test_extract_episode_number(self):
        self.assertEqual(extract_episode_number(SAMPLE_SUBTITLE_PATH), 1)

    def test_clean_ass_text(self):
        raw = r"{\an8\c&H5D5954&}内心冰冷僵硬\N我在这世界孤单一人"
        self.assertEqual(clean_ass_text(raw), "内心冰冷僵硬 我在这世界孤单一人")

    def test_load_relevant_subtitle_lines(self):
        lines = load_relevant_subtitle_lines(SAMPLE_SUBTITLE_PATH)
        self.assertGreater(len(lines), 700)
        self.assertEqual(lines[0].style, "Dial_JP")
        self.assertEqual(lines[0].clean_text, "祥ちゃん")

    def test_build_utterance_units(self):
        lines = load_relevant_subtitle_lines(SAMPLE_SUBTITLE_PATH)
        utterances = build_utterance_units(lines, episode=1)
        self.assertGreaterEqual(len(utterances), 395)
        first = utterances[0]
        self.assertEqual(first.jp_text, "祥ちゃん")
        self.assertEqual(first.zh_text, "小祥")
        self.assertEqual(first.u_id, "ep01_u0001")

    def test_build_screen_text_units_deduplicates_layers(self):
        lines = load_relevant_subtitle_lines(SAMPLE_SUBTITLE_PATH)
        screen_texts = build_screen_text_units(lines, episode=1)
        texts = [item.text for item in screen_texts]
        self.assertIn("田径部", texts)
        self.assertEqual(texts.count("田径部"), 1)
        self.assertIn("月之森女子学园", texts)

    def test_segment_scenes_and_candidate_characters(self):
        lines = load_relevant_subtitle_lines(SAMPLE_SUBTITLE_PATH)
        utterances = build_utterance_units(lines, episode=1)
        screen_texts = build_screen_text_units(lines, episode=1)
        scenes = segment_scenes(
            utterances=utterances,
            screen_texts=screen_texts,
            anime_title="It's MyGO!!!!!",
            series_id="its_mygo",
            season_id=3,
        )
        self.assertGreater(len(scenes), 5)
        first_scene = scenes[0]
        candidate_names = {candidate.display_name for candidate in first_scene.candidate_characters}
        self.assertTrue({"素世", "祥子", "灯", "立希", "若叶睦"}.issubset(candidate_names))
        self.assertEqual(first_scene.utterances[0].zh_text, "小祥")

    def test_prepare_stage1_scenes(self):
        scenes = prepare_stage1_scenes(SAMPLE_SUBTITLE_PATH)
        self.assertGreater(len(scenes), 5)
        first_scene = scenes[0]
        self.assertEqual(first_scene.scene_id, "ep01_s001")
        self.assertEqual(first_scene.series_id, "its_mygo")
        self.assertEqual(first_scene.season_id, 3)

    def test_render_stage1_prompt(self):
        scenes = prepare_stage1_scenes(SAMPLE_SUBTITLE_PATH)
        prompt = render_stage1_prompt(scenes[0])
        self.assertIn("scene_id", prompt)
        self.assertIn("候选角色列表", prompt)
        self.assertIn("小祥", prompt)
        self.assertIn("候选角色列表：\n\n- `灯`", prompt)
        self.assertIn("台词列表：\n\n- `ep01_u0001`", prompt)
        self.assertNotIn("备注：", prompt)

    def test_prepare_artifact_roundtrip_and_cli(self):
        artifact = prepare_stage1_artifact(SAMPLE_SUBTITLE_PATH)
        self.assertGreater(len(artifact.scenes), 5)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "prepared.json"
            exit_code = pipeline_cli_main(
                [
                    "prepare-stage1",
                    "--subtitle",
                    str(SAMPLE_SUBTITLE_PATH),
                    "--output",
                    str(output_path),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())

            loaded = load_stage1_prepared_artifact(output_path)
            self.assertEqual(loaded.metadata.series_id, "its_mygo")
            self.assertEqual(loaded.metadata.season_id, 3)
            self.assertGreater(len(loaded.scenes), 5)

    def test_build_stage2_input_artifact_from_real_pass1(self):
        prepared = load_stage1_prepared_artifact(SAMPLE_PREPARED_PATH)
        annotation = load_stage1_annotation_artifact(SAMPLE_PASS1_RAW_PATH)
        stage2_artifact = build_stage2_input_artifact(
            prepared_artifact=prepared,
            annotation_artifact=annotation,
            source_stage1_output_path=str(SAMPLE_PASS1_RAW_PATH),
        )

        self.assertGreaterEqual(len(stage2_artifact.scenes), 1)
        first_scene = stage2_artifact.scenes[0]
        self.assertEqual(first_scene.scene_id, "ep01_s001")
        self.assertEqual(first_scene.scene_start_text, "0:00:31.24")
        self.assertTrue({"素世", "祥子", "灯", "立希", "若叶睦"}.issubset(set(first_scene.present_characters)))
        self.assertEqual(first_scene.utterances[0].u_id, "ep01_u0001")
        self.assertEqual(first_scene.utterances[0].speaker_name, "素世")
        self.assertEqual(first_scene.utterances[0].zh_text, "小祥")
        self.assertEqual(first_scene.utterances[0].jp_text, "祥ちゃん")
        self.assertEqual(stage2_artifact.metadata.source_stage1_model, annotation.model)

    def test_render_stage2_prompt(self):
        artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        prompt = render_stage2_prompt(artifact.scenes[0])
        self.assertIn("story_events", prompt)
        self.assertIn("character_relations", prompt)
        self.assertIn("lore_entries", prompt)
        self.assertIn("ep01_s001", prompt)
        self.assertIn("speaker_name", prompt)
        self.assertIn("小祥", prompt)

    def test_build_stage3_normalized_import_artifact_from_real_pass2(self):
        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )

        self.assertGreater(len(normalized.story_events), 0)
        self.assertGreater(len(normalized.character_relations), 0)
        self.assertGreater(len(normalized.lore_entries), 0)
        self.assertEqual(normalized.story_events[0].document.time_order, 4000)
        self.assertEqual(normalized.story_events[0].document.participants[0].value, "tomori")
        self.assertEqual(normalized.character_relations[0].document.subject_character_id.value, "soyo")
        self.assertEqual(normalized.lore_entries[0].document.scope_type.value, "series")

        pair_windows = {}
        for record in normalized.character_relations:
            key = (
                record.document.subject_character_id.value,
                record.document.object_character_id.value,
                record.source_scene_id,
            )
            pair_windows[key] = (record.document.visible_from, record.document.visible_to)

        self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s001")], (4000, 4006))
        self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s008")], (4007, 999999))
        self.assertEqual(pair_windows[("anon", "tomori", "ep01_s007")], (4006, 4008))
        self.assertEqual(pair_windows[("anon", "tomori", "ep01_s010")], (4009, 999999))
        self.assertEqual(pair_windows[("tomori", "anon", "ep01_s007")], (4006, 4008))
        self.assertEqual(pair_windows[("tomori", "anon", "ep01_s010")], (4009, 999999))

    def test_backfill_existing_stage3_character_relations(self):
        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )

        for record in normalized.character_relations:
            record.document.visible_to = 999999

        backfill_existing_stage3_character_relations(normalized)

        pair_windows = {}
        for record in normalized.character_relations:
            key = (
                record.document.subject_character_id.value,
                record.document.object_character_id.value,
                record.source_scene_id,
            )
            pair_windows[key] = (record.document.visible_from, record.document.visible_to)

        self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s001")], (4000, 4006))
        self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s008")], (4007, 999999))
        self.assertEqual(pair_windows[("anon", "tomori", "ep01_s007")], (4006, 4008))
        self.assertEqual(pair_windows[("anon", "tomori", "ep01_s010")], (4009, 999999))

    def test_litellm_client_streaming_callbacks(self):
        fake_module = types.SimpleNamespace()

        def fake_completion(**kwargs):
            self.assertTrue(kwargs["stream"])
            return iter(
                [
                    {"choices": [{"delta": {"content": '{"scene_id":"ep01_s001",'}}]},
                    {"choices": [{"delta": {"content": '"episode":1,'}}]},
                    {"choices": [{"delta": {"content": '"present_characters":[],'}}]},
                    {"choices": [{"delta": {"content": '"utterance_annotations":[],'}}]},
                    {"choices": [{"delta": {"content": '"global_notes":[]}'}}]},
                ]
            )

        fake_module.completion = fake_completion
        status_messages: list[str] = []
        streamed_chunks: list[str] = []
        with patch.dict(sys.modules, {"litellm": fake_module}):
            client = LiteLLMJsonClient(LiteLLMConfig(model="fake-model"))
            response = client.complete_json(
                "test prompt",
                stream=True,
                status_callback=status_messages.append,
                stream_callback=streamed_chunks.append,
            )

        self.assertEqual(response.parsed_payload["scene_id"], "ep01_s001")
        self.assertTrue(any("正在向 LLM 发送请求" in message for message in status_messages))
        self.assertTrue(any("开始解析 JSON" in message for message in status_messages))
        self.assertIn('"episode":1,', "".join(streamed_chunks))

    def test_annotate_prepared_artifact_emits_status(self):
        artifact = prepare_stage1_artifact(SAMPLE_SUBTITLE_PATH)
        fake_raw = (
            '{"scene_id":"ep01_s001","episode":1,"present_characters":["素世"],'
            '"utterance_annotations":[],"global_notes":[]}'
        )
        status_messages: list[str] = []
        with patch(
            "rag.pipeline.stage1_speaker_annotation.LiteLLMJsonClient.complete_json",
            return_value=types.SimpleNamespace(
                raw_content=fake_raw,
                parsed_payload={
                    "scene_id": "ep01_s001",
                    "episode": 1,
                    "present_characters": ["素世"],
                    "utterance_annotations": [],
                    "global_notes": [],
                },
            ),
        ):
            result = annotate_prepared_stage1_artifact(
                artifact=artifact,
                llm_config=LiteLLMConfig(model="fake-model"),
                max_scenes=1,
                status_callback=status_messages.append,
            )

        self.assertEqual(len(result.results), 1)
        self.assertIsNotNone(result.results[0].annotation)
        joined = "\n".join(status_messages)
        self.assertIn("正在处理第 1 话的 ep01_s001 场景", joined)
        self.assertIn("开始请求 LLM", joined)
        self.assertIn("标注成功", joined)

    def test_annotate_stage2_artifact_emits_status(self):
        artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        fake_raw = (
            '{"scene_id":"ep01_s001","story_events":[],"character_relations":[],"lore_entries":[]}'
        )
        status_messages: list[str] = []
        with patch(
            "rag.pipeline.stage2_document_extraction.LiteLLMJsonClient.complete_json",
            return_value=types.SimpleNamespace(
                raw_content=fake_raw,
                parsed_payload={
                    "scene_id": "ep01_s001",
                    "story_events": [],
                    "character_relations": [],
                    "lore_entries": [],
                },
            ),
        ):
            result = annotate_stage2_input_artifact(
                artifact=artifact,
                llm_config=LiteLLMConfig(model="fake-model"),
                max_scenes=1,
                status_callback=status_messages.append,
            )

        self.assertEqual(len(result.results), 1)
        self.assertIsNotNone(result.results[0].annotation)
        joined = "\n".join(status_messages)
        self.assertIn("正在处理第 1 话的 ep01_s001 场景（第二阶段）", joined)
        self.assertIn("开始请求第二阶段 LLM", joined)
        self.assertIn("第二阶段抽取成功", joined)

    def test_cli_annotate_stage1_writes_stage2_input(self):
        artifact = prepare_stage1_artifact(SAMPLE_SUBTITLE_PATH)

        with tempfile.TemporaryDirectory() as temp_dir:
            prepared_path = Path(temp_dir) / "prepared.json"
            raw_output_path = Path(temp_dir) / "ep01_pass1_raw.json"
            stage2_output_path = derive_stage2_input_output_path(raw_output_path)

            from rag.pipeline.stage1_speaker_annotation import save_stage1_prepared_artifact

            save_stage1_prepared_artifact(artifact, prepared_path)
            fake_payload = {
                "scene_id": "ep01_s001",
                "episode": 1,
                "present_characters": ["素世", "祥子"],
                "utterance_annotations": [
                    {
                        "u_id": "ep01_u0001",
                        "speaker_name": "素世",
                        "speaker_confidence": 0.95,
                        "is_inner_monologue": False,
                        "addressee_candidates": ["祥子"],
                        "mentioned_characters": ["祥子"],
                        "emotion_hint": "呼唤",
                        "reason_brief": "测试数据",
                    }
                ],
                "global_notes": [],
            }
            with patch(
                "rag.pipeline.stage1_speaker_annotation.LiteLLMJsonClient.complete_json",
                return_value=types.SimpleNamespace(
                    raw_content="{}",
                    parsed_payload=fake_payload,
                ),
            ):
                exit_code = pipeline_cli_main(
                    [
                        "annotate-stage1",
                        "--prepared",
                        str(prepared_path),
                        "--output",
                        str(raw_output_path),
                        "--model",
                        "fake-model",
                        "--max-scenes",
                        "1",
                        "--quiet-status",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(raw_output_path.exists())
            self.assertTrue(stage2_output_path.exists())

            stage2_artifact = load_stage2_input_artifact(stage2_output_path)
            self.assertEqual(len(stage2_artifact.scenes), 1)
            self.assertEqual(stage2_artifact.scenes[0].scene_id, "ep01_s001")
            self.assertEqual(stage2_artifact.scenes[0].utterances[0].speaker_name, "素世")
            self.assertEqual(stage2_artifact.metadata.source_stage1_output_path, str(raw_output_path.resolve()))

    def test_cli_annotate_stage2_writes_raw_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "ep01_pass2_raw.json"
            fake_payload = {
                "scene_id": "ep01_s001",
                "story_events": [
                    {
                        "scene_id": "ep01_s001",
                        "event_local_id": "event_01",
                        "title": "测试事件",
                        "summary": "测试摘要",
                        "participants": ["素世", "祥子"],
                        "importance": 1,
                        "tags": ["测试"],
                        "retrieval_text": "测试检索文本",
                        "evidence_u_ids": ["ep01_u0001"],
                        "evidence_s_ids": [],
                        "confidence": 0.9,
                    }
                ],
                "character_relations": [],
                "lore_entries": [],
            }
            with patch(
                "rag.pipeline.stage2_document_extraction.LiteLLMJsonClient.complete_json",
                return_value=types.SimpleNamespace(
                    raw_content="{}",
                    parsed_payload=fake_payload,
                ),
            ):
                exit_code = pipeline_cli_main(
                    [
                        "annotate-stage2",
                        "--input",
                        str(SAMPLE_STAGE2_INPUT_PATH),
                        "--output",
                        str(output_path),
                        "--model",
                        "fake-model",
                        "--max-scenes",
                        "1",
                        "--quiet-status",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            artifact = load_stage2_annotation_artifact(output_path)
            self.assertEqual(len(artifact.results), 1)
            self.assertIsNotNone(artifact.results[0].annotation)
            self.assertEqual(artifact.results[0].annotation.story_events[0].title, "测试事件")

    def test_cli_normalize_and_import_stage3(self):
        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            normalized_path = Path(temp_dir) / "ep01_rag_ready.json"
            save_stage3_normalized_import_artifact(normalized, normalized_path)

            exit_code = pipeline_cli_main(
                [
                    "normalize-stage3-rag",
                    "--input",
                    str(SAMPLE_STAGE2_INPUT_PATH),
                    "--annotation",
                    str(SAMPLE_PASS2_RAW_PATH),
                    "--output",
                    str(normalized_path),
                ]
            )
            self.assertEqual(exit_code, 0)
            loaded = load_stage3_normalized_import_artifact(normalized_path)
            self.assertGreater(len(loaded.story_events), 0)

            class FakeService:
                def close(self):
                    return None

            fake_report = types.SimpleNamespace(
                requested_count=1,
                success_count=1,
                failure_count=0,
            )
            with patch(
                "rag.pipeline.cli.create_rag_service",
                return_value=FakeService(),
            ), patch(
                "rag.pipeline.cli.upsert_stage3_normalized_import_artifact",
                return_value={
                    "story_events": fake_report,
                    "character_relations": fake_report,
                    "lore_entries": fake_report,
                },
            ):
                exit_code = pipeline_cli_main(
                    [
                        "import-stage3-rag",
                        "--input",
                        str(normalized_path),
                        "--qdrant-connect-type",
                        "memory",
                    ]
                )

            self.assertEqual(exit_code, 0)

    def test_cli_backfill_stage3_relations(self):
        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )
        for record in normalized.character_relations:
            record.document.visible_to = 999999

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "rag_ready_input.json"
            output_path = Path(temp_dir) / "rag_ready_output.json"
            save_stage3_normalized_import_artifact(normalized, input_path)

            exit_code = pipeline_cli_main(
                [
                    "backfill-stage3-relations",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            updated = load_stage3_normalized_import_artifact(output_path)
            pair_windows = {}
            for record in updated.character_relations:
                key = (
                    record.document.subject_character_id.value,
                    record.document.object_character_id.value,
                    record.source_scene_id,
                )
                pair_windows[key] = (record.document.visible_from, record.document.visible_to)

            self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s001")], (4000, 4006))
            self.assertEqual(pair_windows[("soyo", "sakiko", "ep01_s008")], (4007, 999999))

    def test_cli_backfill_stage3_relations_across_directory(self):
        input_artifact = load_stage2_input_artifact(SAMPLE_STAGE2_INPUT_PATH)
        annotation_artifact = load_stage2_annotation_artifact(SAMPLE_PASS2_RAW_PATH)
        normalized = build_stage3_normalized_import_artifact(
            input_artifact=input_artifact,
            annotation_artifact=annotation_artifact,
        )

        first_file_artifact = normalized.model_copy(deep=True)
        second_file_artifact = normalized.model_copy(deep=True)
        first_file_artifact.story_events = []
        first_file_artifact.lore_entries = []
        first_file_artifact.issues = []
        second_file_artifact.story_events = []
        second_file_artifact.lore_entries = []
        second_file_artifact.issues = []
        first_file_artifact.character_relations = [
            record
            for record in first_file_artifact.character_relations
            if (
                record.document.subject_character_id.value,
                record.document.object_character_id.value,
                record.source_scene_id,
            )
            == ("soyo", "sakiko", "ep01_s001")
        ]
        second_file_artifact.character_relations = [
            record
            for record in second_file_artifact.character_relations
            if (
                record.document.subject_character_id.value,
                record.document.object_character_id.value,
                record.source_scene_id,
            )
            == ("soyo", "sakiko", "ep01_s008")
        ]
        first_file_artifact.character_relations[0].document.visible_to = 999999
        second_file_artifact.character_relations[0].document.visible_to = 999999

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            output_dir = Path(temp_dir) / "output"
            input_dir.mkdir()
            save_stage3_normalized_import_artifact(first_file_artifact, input_dir / "ep01_part1.json")
            save_stage3_normalized_import_artifact(second_file_artifact, input_dir / "ep01_part2.json")

            exit_code = pipeline_cli_main(
                [
                    "backfill-stage3-relations",
                    "--input",
                    str(input_dir),
                    "--output",
                    str(output_dir),
                ]
            )

            self.assertEqual(exit_code, 0)
            updated_first = load_stage3_normalized_import_artifact(output_dir / "ep01_part1.json")
            updated_second = load_stage3_normalized_import_artifact(output_dir / "ep01_part2.json")
            self.assertEqual(updated_first.character_relations[0].document.visible_to, 4006)
            self.assertEqual(updated_second.character_relations[0].document.visible_to, 999999)


if __name__ == "__main__":
    unittest.main()
