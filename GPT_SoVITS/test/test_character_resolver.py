"""角色知识库标识解析器的单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.character_resolver import resolve_character_id
from rag.models import CharacterId, SeriesId
from rag.pipeline.characters import (
    build_character_catalog,
    default_episode_prior_candidates,
)
from rag.pipeline.stage3_rag_import import (
    resolve_character_id as resolve_pipeline_character_id,
)
from ui_constants import char_info_json, downloadable_character_names


class TestResolveCharacterId(unittest.TestCase):
    """验证有限且严格的角色名称解析行为。"""

    def test_resolves_common_chinese_name(self) -> None:
        """常用中文名可以解析为知识库角色标识。"""

        self.assertEqual(resolve_character_id("祥子"), CharacterId.SAKIKO)

    def test_resolves_enum_value_and_member_name(self) -> None:
        """枚举值和不区分大小写的成员名均可解析。"""

        self.assertEqual(resolve_character_id("sakiko"), CharacterId.SAKIKO)
        self.assertEqual(resolve_character_id("SAKIKO"), CharacterId.SAKIKO)

    def test_unknown_or_empty_name_returns_none(self) -> None:
        """未知角色和空名称不会被模糊映射。"""

        self.assertIsNone(resolve_character_id("自定义角色"))
        self.assertIsNone(resolve_character_id("  "))

    def test_mugendai_characters_have_metadata_but_no_download_option(self) -> None:
        """梦限大角色应参与名称解析，但不伪装成 Bestdori 下载目标。"""

        expected = {
            "阿拉蕾": ("arale", "#FFEE55"),
            "野乃花": ("nonoka", "#FFBBCC"),
            "律": ("ritsu", "#4477CC"),
            "都子": ("miyako", "#9977CC"),
            "由乃": ("yuno", "#EE5577"),
        }
        downloadable = set(downloadable_character_names())
        catalog = {
            candidate.character_id: candidate
            for candidate in build_character_catalog()
        }

        for display_name, (character_id, theme_color) in expected.items():
            info = char_info_json[display_name]
            self.assertEqual(info["romaji"], character_id)
            self.assertEqual(info["theme_color"], theme_color)
            self.assertIsNone(info["bestdori_index"])
            self.assertNotIn(display_name, downloadable)
            self.assertIn(character_id, catalog)
        self.assertIn("祥子", downloadable)

    def test_mugendai_romanized_full_names_are_pipeline_aliases(self) -> None:
        """梦限大成员的完整罗马字姓名应可供标注流水线识别。"""

        catalog = {
            candidate.character_id: candidate
            for candidate in build_character_catalog()
        }
        self.assertIn("Nakamachi Arale", catalog["arale"].aliases)
        self.assertIn("Miyanaga Nonoka", catalog["nonoka"].aliases)
        self.assertIn("Minetsuki Ritsu", catalog["ritsu"].aliases)
        self.assertIn("Fuji Miyako", catalog["miyako"].aliases)
        self.assertIn("Sengoku Yuno", catalog["yuno"].aliases)
        self.assertEqual(
            resolve_pipeline_character_id("Nakamachi Arale"),
            CharacterId.ARALE,
        )
        self.assertEqual(
            resolve_pipeline_character_id("宮永ののか"),
            CharacterId.NONOKA,
        )

    def test_ririko_is_annotation_only_and_distinct_from_rinko(self) -> None:
        """凛凛子可供 RAG 解析，但不会进入可对话角色配置。"""

        catalog = {
            candidate.character_id: candidate
            for candidate in build_character_catalog()
        }

        self.assertEqual(CharacterId.RIRIKO.common_name, "凛凛子")
        self.assertEqual(resolve_character_id("凛凛子"), CharacterId.RIRIKO)
        self.assertEqual(resolve_pipeline_character_id("凛凛子姐"), CharacterId.RIRIKO)
        self.assertEqual(resolve_pipeline_character_id("凛々子"), CharacterId.RIRIKO)
        self.assertEqual(resolve_pipeline_character_id("燐子"), CharacterId.RINKO)
        self.assertIn("ririko", catalog)
        self.assertNotIn("凛凛子", catalog["rinko"].aliases)
        self.assertNotIn("凛凛子", char_info_json)
        self.assertNotIn(
            "凛凛子",
            default_episode_prior_candidates(SeriesId.ITS_MYGO),
        )


if __name__ == "__main__":
    unittest.main()
