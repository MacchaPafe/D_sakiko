"""独立 Stage2 数据集编辑器的轻量回归测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PIPELINE_ROOT = (
    Path(__file__).resolve().parent.parent
    / "GPT_SoVITS"
    / "rag"
    / "pipeline"
)
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from stage2_dataset_editor import (  # noqa: E402
    Stage2DatasetEditor,
    discover_input_path,
)
from stage2_editor_schema import Stage2InputArtifact  # noqa: E402


def sample_payload() -> dict[str, object]:
    """生成覆盖全部可编辑字段的最小 Stage2 输入。"""

    return {
        "metadata": {
            "subtitle_path": "ep01.ass",
            "anime_title": "测试动画",
            "series_id": "test_series",
            "timeline_id": "test_timeline",
            "story_year": None,
            "canon_branch": "main",
            "episode": 1,
            "scene_gap_ms": 4000,
            "source_stage1_model": "test-model",
            "source_stage1_template_path": "prompt.txt",
            "source_stage1_output_path": "ep01_pass1_raw.json",
        },
        "scenes": [
            {
                "anime_title": "测试动画",
                "series_id": "test_series",
                "timeline_id": "test_timeline",
                "story_year": None,
                "episode": 1,
                "scene_id": "ep01_s001",
                "start_ms": 1000,
                "end_ms": 3000,
                "scene_start_text": "0:00:01.00",
                "scene_end_text": "0:00:03.00",
                "scene_summary_hint": None,
                "present_characters": ["甲", "乙"],
                "screen_texts": [],
                "utterances": [
                    {
                        "u_id": "ep01_u0001",
                        "start_ms": 1000,
                        "end_ms": 3000,
                        "start_text": "0:00:01.00",
                        "end_text": "0:00:03.00",
                        "speaker_name": "甲",
                        "speaker_confidence": 0.6,
                        "is_inner_monologue": False,
                        "addressee_candidates": ["乙"],
                        "mentioned_characters": [],
                        "emotion_hint": "平静",
                        "zh_text": "你好。",
                        "jp_text": "こんにちは。",
                    }
                ],
                "global_notes": [],
            }
        ],
        "skipped_scenes": [],
    }


class StandaloneEditorTest(unittest.TestCase):
    """验证加载、字段修改、校验、备份和保存闭环。"""

    def test_discovery_prefers_stage2_input_name(self) -> None:
        """优先发现标准命名的 Stage2 输入文件。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "misc.json").write_text("{}", encoding="utf-8")
            preferred = data_dir / "ep01_stage2_input.json"
            preferred.write_text("{}", encoding="utf-8")
            self.assertEqual(discover_input_path(data_dir), preferred.resolve())

    def test_edit_and_save_round_trip(self) -> None:
        """修改全部新增控件对应字段并确认保存结果可再次校验。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "ep01_stage2_input.json"
            input_path.write_text(
                json.dumps(sample_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            editor = Stage2DatasetEditor(input_path)
            editor._update_string_field("speaker_name", "乙")
            editor._update_bool_field("is_inner_monologue", True)
            editor._update_confidence(1.4)
            editor._update_list_field("mentioned_characters", ["甲", "甲"])

            with patch("stage2_dataset_editor.ui.notify"):
                editor.save()

            saved_payload = json.loads(input_path.read_text(encoding="utf-8"))
            Stage2InputArtifact.model_validate(saved_payload)
            utterance = saved_payload["scenes"][0]["utterances"][0]
            self.assertEqual(utterance["speaker_name"], "乙")
            self.assertTrue(utterance["is_inner_monologue"])
            self.assertEqual(utterance["speaker_confidence"], 1.0)
            self.assertEqual(utterance["mentioned_characters"], ["甲"])
            self.assertTrue(input_path.with_suffix(".json.bak").is_file())


if __name__ == "__main__":
    unittest.main()
