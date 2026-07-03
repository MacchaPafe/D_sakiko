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

from live2d_support.model_importer import Live2DModelImportError, import_live2d_model_to_extra_model


class Live2DModelImporterTestCase(unittest.TestCase):
    """测试本地 Live2D 模型导入逻辑。"""

    def _write_file(self, path: Path, content: str = "{}") -> None:
        """写入测试用模型资源文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_json_object(self, path: Path) -> dict[str, object]:
        """读取 JSON 对象测试文件。"""
        data: object = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        return cast(dict[str, object], data)

    def test_import_model3_copies_referenced_files_and_normalizes_result(self) -> None:
        """验证导入 V3 模型会复制引用资源并执行项目规范化。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "source" / "Band Model"
            shared_dir = temp_path / "source" / "shared"
            live2d_related_dir = temp_path / "live2d_related"
            self._write_file(source_dir / "moc" / "model.moc3", "moc")
            self._write_file(source_dir / "textures" / "texture_00.png", "texture")
            self._write_file(source_dir / "expressions" / "smile.exp3.json")
            self._write_file(source_dir / "motions" / "mtn_smile01_L.motion3.json")
            self._write_file(shared_dir / "voice.wav", "voice")
            source_model_json = source_dir / "sample.model3.json"
            source_model_json.write_text(
                json.dumps(
                    {
                        "Version": 3,
                        "FileReferences": {
                            "Moc": "moc/model.moc3",
                            "Textures": ["textures/texture_00.png"],
                            "Expressions": [
                                {"Name": "smile", "File": "expressions/smile.exp3.json"}
                            ],
                            "Motions": {
                                "mtn_smile01_L": [
                                    {
                                        "File": "motions/mtn_smile01_L.motion3.json",
                                        "Sound": "../shared/voice.wav",
                                    }
                                ]
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = import_live2d_model_to_extra_model(
                str(source_model_json),
                "tomori",
                str(live2d_related_dir),
            )

            target_dir = Path(result.target_dir)
            target_model_json = Path(result.model_json_path)
            self.assertEqual(result.model_name, "Band Model")
            self.assertEqual(
                target_dir.parent.resolve(strict=False),
                (live2d_related_dir / "tomori" / "extra_model").resolve(strict=False),
            )
            self.assertTrue(target_model_json.is_file())
            self.assertTrue((target_dir / "model.moc3").is_file())
            self.assertTrue((target_dir / "texture_00.png").is_file())
            self.assertTrue((target_dir / "smile.exp3.json").is_file())
            self.assertTrue((target_dir / "mtn_smile01_L.motion3.json").is_file())
            self.assertTrue((target_dir / "voice.wav").is_file())
            self.assertFalse((target_dir / "external_assets").exists())

            imported_data = self._read_json_object(target_model_json)
            file_references = cast(dict[str, object], imported_data["FileReferences"])
            motions = cast(dict[str, object], file_references["Motions"])
            happiness_l = cast(list[dict[str, object]], motions["happiness_L"])
            self.assertEqual(file_references["Moc"], "model.moc3")
            self.assertEqual(file_references["Textures"], ["texture_00.png"])
            self.assertEqual(happiness_l[0]["File"], "mtn_smile01_L.motion3.json")
            self.assertEqual(happiness_l[0]["Sound"], "voice.wav")

    def test_import_model2_preserves_safe_relative_structure_and_uses_unique_names(self) -> None:
        """验证导入 V2 模型会保留安全相对目录并避免覆盖既有模型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "source" / "Stage:Model"
            shared_dir = temp_path / "source" / "voice"
            live2d_related_dir = temp_path / "live2d_related"
            self._write_file(source_dir / "moc" / "model.moc", "moc")
            self._write_file(source_dir / "textures" / "texture.png", "texture")
            self._write_file(source_dir / "motions" / "idle.mtn", "motion")
            self._write_file(shared_dir / "start.wav", "voice")
            source_model_json = source_dir / "stage.model.json"
            source_model_json.write_text(
                json.dumps(
                    {
                        "model": "moc/model.moc",
                        "textures": ["textures/texture.png"],
                        "motions": {
                            "idle": [
                                {
                                    "file": "motions/idle.mtn",
                                    "sound": "../voice/start.wav",
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            first_result = import_live2d_model_to_extra_model(
                str(source_model_json),
                "tomori",
                str(live2d_related_dir),
            )
            second_result = import_live2d_model_to_extra_model(
                str(source_model_json),
                "tomori",
                str(live2d_related_dir),
            )

            first_target_dir = Path(first_result.target_dir)
            second_target_dir = Path(second_result.target_dir)
            self.assertEqual(first_result.model_name, "Stage_Model")
            self.assertEqual(second_result.model_name, "Stage_Model_2")
            self.assertTrue((first_target_dir / "moc" / "model.moc").is_file())
            self.assertTrue((first_target_dir / "textures" / "texture.png").is_file())
            self.assertTrue((first_target_dir / "motions" / "idle.mtn").is_file())
            self.assertTrue((first_target_dir / "external_assets" / "start.wav").is_file())
            self.assertTrue(second_target_dir.is_dir())

            imported_data = self._read_json_object(Path(first_result.model_json_path))
            motions = cast(dict[str, object], imported_data["motions"])
            idle_entries = cast(list[dict[str, object]], motions["idle"])
            self.assertEqual(imported_data["model"], "moc/model.moc")
            self.assertEqual(imported_data["textures"], ["textures/texture.png"])
            self.assertEqual(idle_entries[0]["file"], "motions/idle.mtn")
            self.assertEqual(idle_entries[0]["sound"], "external_assets/start.wav")

    def test_import_rejects_models_inside_live2d_related(self) -> None:
        """验证不会从当前 live2d_related 目录循环导入模型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            live2d_related_dir = Path(temp_dir) / "live2d_related"
            model_dir = live2d_related_dir / "tomori" / "live2D_model"
            model_dir.mkdir(parents=True)
            source_model_json = model_dir / "inside.model.json"
            source_model_json.write_text("{}", encoding="utf-8")

            with self.assertRaises(Live2DModelImportError):
                import_live2d_model_to_extra_model(
                    str(source_model_json),
                    "tomori",
                    str(live2d_related_dir),
                )

    def test_import_removes_new_target_dir_when_reference_is_missing(self) -> None:
        """验证导入失败时会清理本次新建的目标模型目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "source" / "Broken Model"
            live2d_related_dir = temp_path / "live2d_related"
            source_dir.mkdir(parents=True)
            source_model_json = source_dir / "broken.model.json"
            source_model_json.write_text(
                json.dumps({"model": "missing.moc"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(Live2DModelImportError):
                import_live2d_model_to_extra_model(
                    str(source_model_json),
                    "tomori",
                    str(live2d_related_dir),
                )

            extra_model_dir = live2d_related_dir / "tomori" / "extra_model"
            self.assertEqual(list(extra_model_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
