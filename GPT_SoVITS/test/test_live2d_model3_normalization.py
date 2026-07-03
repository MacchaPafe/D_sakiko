from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support.model_normalizer import normalize_live2d_model_for_project, normalize_model3_for_project


class Live2DModel3NormalizationTestCase(unittest.TestCase):
    """测试 Live2D V3 模型导入规范化逻辑。"""

    def _write_file(self, path: Path, content: str = "{}") -> None:
        """写入测试用资产文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_json_object(self, path: Path) -> dict[str, object]:
        """读取 JSON 对象测试文件。"""
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        return cast(dict[str, object], data)

    def test_normalize_model3_flattens_assets_and_builds_position_groups(self) -> None:
        """验证规范化会平铺资产并生成标准位置动作组。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "model"
            model_dir.mkdir()
            self._write_file(model_dir / "textures" / "texture_00.png", "texture")
            self._write_file(model_dir / "expressions" / "exp_smile.exp3.json")
            self._write_file(model_dir / "motions" / "mtn_smile01_C.motion3.json")
            self._write_file(model_dir / "motions" / "mtn_smile01_L.motion3.json")
            self._write_file(model_dir / "motions" / "mtn_smile01_R.motion3.json")
            self._write_file(model_dir / "motions" / "mtn_thinking01.motion3.json")
            self._write_file(model_dir / "model.moc3", "moc")

            model_json_path = model_dir / "sample.model3.json"
            model_json_path.write_text(
                json.dumps(
                    {
                        "Version": 3,
                        "FileReferences": {
                            "Moc": "model.moc3",
                            "Textures": ["textures/texture_00.png"],
                            "Expressions": [
                                {"Name": "smile", "File": "expressions/exp_smile.exp3.json"}
                            ],
                            "Motions": {
                                "mtn_smile01_C": [{"File": "motions/mtn_smile01_C.motion3.json"}],
                                "mtn_smile01_L": [{"File": "motions/mtn_smile01_L.motion3.json"}],
                                "mtn_smile01_R": [{"File": "motions/mtn_smile01_R.motion3.json"}],
                                "mtn_thinking01": [{"File": "motions/mtn_thinking01.motion3.json"}],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertTrue(normalize_model3_for_project(str(model_json_path)))
            normalized_data = self._read_json_object(model_json_path)
            file_references = cast(dict[str, object], normalized_data["FileReferences"])
            motions = cast(dict[str, object], file_references["Motions"])

            self.assertEqual(file_references["Textures"], ["texture_00.png"])
            expressions = cast(list[object], file_references["Expressions"])
            first_expression = cast(dict[str, object], expressions[0])
            self.assertEqual(first_expression["File"], "exp_smile.exp3.json")
            self.assertTrue((model_dir / "mtn_smile01_C.motion3.json").is_file())
            self.assertFalse((model_dir / "motions" / "mtn_smile01_C.motion3.json").exists())
            self.assertFalse((model_dir / "motions").exists())

            self.assertNotIn("mtn_smile01_C", motions)
            self.assertEqual(motions["happiness"], motions["happiness_C"])
            text_generating = cast(list[dict[str, object]], motions["text_generating"])
            self.assertEqual([entry["File"] for entry in text_generating], ["mtn_thinking01.motion3.json"])
            happiness_c = cast(list[dict[str, object]], motions["happiness_C"])
            happiness_l = cast(list[dict[str, object]], motions["happiness_L"])
            happiness_r = cast(list[dict[str, object]], motions["happiness_R"])
            self.assertEqual([entry["File"] for entry in happiness_c], ["mtn_smile01_C.motion3.json"])
            self.assertEqual([entry["File"] for entry in happiness_l], ["mtn_smile01_L.motion3.json"])
            self.assertEqual([entry["File"] for entry in happiness_r], ["mtn_smile01_R.motion3.json"])

            text_generating_l = cast(list[dict[str, object]], motions["text_generating_L"])
            self.assertEqual([entry["File"] for entry in text_generating_l], ["mtn_smile01_L.motion3.json"])

            metadata = cast(dict[str, object], normalized_data["DSakiko"])
            self.assertEqual(metadata["NormalizedModel3Version"], 1)
            self.assertFalse(normalize_model3_for_project(str(model_json_path)))

    def test_normalize_model3_keeps_suffixless_motions_as_default_only(self) -> None:
        """验证无方向后缀动作不会被广播为方向变体组。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "model"
            model_dir.mkdir()
            self._write_file(model_dir / "motions" / "mtn_smile01.motion3.json")

            model_json_path = model_dir / "suffixless.model3.json"
            model_json_path.write_text(
                json.dumps(
                    {
                        "Version": 3,
                        "FileReferences": {
                            "Motions": {
                                "mtn_smile01": [{"File": "motions/mtn_smile01.motion3.json"}],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertTrue(normalize_model3_for_project(str(model_json_path)))
            normalized_data = self._read_json_object(model_json_path)
            file_references = cast(dict[str, object], normalized_data["FileReferences"])
            motions = cast(dict[str, object], file_references["Motions"])

            happiness = cast(list[dict[str, object]], motions["happiness"])
            self.assertEqual([entry["File"] for entry in happiness], ["mtn_smile01.motion3.json"])
            self.assertNotIn("happiness_C", motions)
            self.assertNotIn("happiness_L", motions)
            self.assertNotIn("happiness_R", motions)

    def test_normalize_live2d_model_converts_old_model_json(self) -> None:
        """验证统一入口会转换旧版 Live2D V2 动作组。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            model_json_path = Path(temp_dir) / "old.model.json"
            model_json_path.write_text(
                json.dumps(
                    {
                        "controllers": [],
                        "hit_areas": [],
                        "motions": {
                            "rana": [{"file": f"{index}.mtn"} for index in range(61)]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = normalize_live2d_model_for_project(str(model_json_path))
            converted_data = self._read_json_object(model_json_path)
            motions = cast(dict[str, object], converted_data["motions"])

            self.assertTrue(result.ok)
            self.assertTrue(result.converted_old_model)
            self.assertFalse(result.normalized_model3)
            self.assertNotIn("controllers", converted_data)
            self.assertNotIn("hit_areas", converted_data)
            self.assertNotIn("rana", motions)
            self.assertEqual(len(cast(list[object], motions["happiness"])), 6)
            self.assertEqual(len(cast(list[object], motions["talking_motion"])), 1)


if __name__ == "__main__":
    unittest.main()
