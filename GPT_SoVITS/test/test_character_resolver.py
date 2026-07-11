"""角色知识库标识解析器的单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rag.character_resolver import resolve_character_id
from rag.models import CharacterId


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


if __name__ == "__main__":
    unittest.main()
