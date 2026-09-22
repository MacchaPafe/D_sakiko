from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from live2d_support.model_catalog import Live2DModelCatalog


class Live2DModelCatalogTest(unittest.TestCase):
    """验证共享 Live2D 模型目录的枚举与匹配语义。"""

    def setUp(self) -> None:
        """为每个测试建立独立的角色资源目录。"""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name).resolve()
        self.live2d_root = self.project_root / "live2d_related"
        self.character_root = self.live2d_root / "anon"
        self.character_root.mkdir(parents=True)
        self.catalog = Live2DModelCatalog(self.live2d_root, self.project_root)

    def tearDown(self) -> None:
        """删除测试资源目录。"""
        self.temporary_directory.cleanup()

    def _write_model(self, relative_path: str, version: str = "v2") -> Path:
        """写入一个可被基础识别的模型 JSON。"""
        path = self.character_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object]
        if version == "v3":
            data = {"Version": 3, "FileReferences": {"Moc": "model.moc3"}}
        else:
            data = {"model": "model.moc"}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path.resolve()

    def test_lists_default_first_and_naturally_sorts_extra_models(self) -> None:
        """默认项应置顶，额外服装应按名称自然排序。"""
        self._write_model("live2D_model/default.model.json")
        self._write_model("extra_model/costume10/model.model.json")
        self._write_model("extra_model/costume2/model.model.json")

        options = self.catalog.list_options("anon")

        self.assertEqual([option.display_name for option in options], ["默认", "costume2", "costume10"])
        self.assertTrue(options[0].is_default)

    def test_prefers_v3_when_both_versions_exist(self) -> None:
        """同一服装目录同时存在 v2/v3 时应选择 v3。"""
        self._write_model("extra_model/room/model.model.json")
        expected = self._write_model("extra_model/room/model.model3.json", "v3")

        option = self.catalog.list_options("anon")[0]

        self.assertEqual(option.model_json_path, expected)
        self.assertEqual(option.version, "v3")

    def test_keeps_invalid_json_as_disabled_option(self) -> None:
        """找到模型文件但 JSON 损坏时应保留禁用项。"""
        path = self.character_root / "extra_model" / "broken" / "broken.model3.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")

        option = self.catalog.list_options("anon")[0]

        self.assertFalse(option.available)
        self.assertIsNotNone(option.error_message)

    def test_ignores_directory_without_model_json(self) -> None:
        """没有模型 JSON 的普通目录不应出现在列表中。"""
        directory = self.character_root / "extra_model" / "empty"
        directory.mkdir(parents=True)
        (directory / "texture.png").write_bytes(b"png")

        self.assertEqual(self.catalog.list_options("anon"), ())

    def test_option_id_is_stable_and_does_not_contain_path(self) -> None:
        """选项 ID 应稳定且不暴露主机路径。"""
        self._write_model("extra_model/room/model.model3.json", "v3")

        first = self.catalog.list_options("anon")[0]
        second = self.catalog.list_options("anon")[0]

        self.assertEqual(first.option_id, second.option_id)
        self.assertNotIn(str(self.project_root), first.option_id)

    def test_matches_legacy_relative_configured_path(self) -> None:
        """应能使用旧存档的 GPT 相对路径匹配选项。"""
        expected = self._write_model("extra_model/room/model.model.json")
        configured = Path("../live2d_related/anon/extra_model/room/model.model.json")

        option = self.catalog.find_by_path("anon", configured)

        self.assertIsNotNone(option)
        self.assertEqual(option.model_json_path, expected)


if __name__ == "__main__":
    unittest.main()
