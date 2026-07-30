from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication, QWidget
from qfluentwidgets import MessageBoxBase

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from character import CharacterAttributes
from ui.interfaces.character_area import (
    CharacterArea,
    CharacterCreationDialog,
    SystemCharacterDetailView,
)


class CharacterAreaFallbackTestCase(unittest.TestCase):
    """测试角色面板对无模型角色的展示。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建 Qt offscreen 测试所需的应用实例。"""
        cls.application = QApplication.instance() or QApplication([])

    def test_no_model_character_shows_unconfigured_and_keeps_import_available(self) -> None:
        """验证无模型角色显示“未配置”并保留模型导入入口。"""
        character = CharacterAttributes()
        character.character_folder_name = "uika"
        character.character_name = "三角初华"
        character.character_description = "擅长隐藏真实想法的吉他手。"
        character.live2d_json = None

        view = SystemCharacterDetailView()
        view.set_character(character)

        self.assertEqual(view.live2d_card.contentLabel.text(), "未配置")
        self.assertEqual(view.import_live2d_button.text(), "添加 Live2D 模型")

    def test_creation_form_only_suggests_id_for_safe_english_name(self) -> None:
        """验证创建表单不会把中文名称强行转换成不稳定的角色 ID。"""
        parent = QWidget()
        dialog = CharacterCreationDialog(parent)
        self.assertIsInstance(dialog, MessageBoxBase)
        self.assertEqual(dialog.yesButton.text(), "创建")
        self.assertEqual(dialog.cancelButton.text(), "取消")

        dialog.name_edit.setText("Alice Smith")
        self.assertEqual(dialog.id_edit.text(), "alice_smith")

        chinese_dialog = CharacterCreationDialog(parent)
        chinese_dialog.name_edit.setText("三角初华")
        self.assertEqual(chinese_dialog.id_edit.text(), "")

    def test_character_segment_exposes_creation_button(self) -> None:
        """验证“角色”页签保留创建入口而不显示删除入口。"""

        class FakeCharacterManager:
            """提供角色面板初始化所需的最小测试数据。"""

            def __init__(self) -> None:
                """初始化空角色与空对话身份列表。"""
                self.character_class_list: list[CharacterAttributes] = []
                self.user_characters: list[CharacterAttributes] = []

        with (
            patch(
                "ui.interfaces.character_area.GetCharacterAttributes",
                return_value=FakeCharacterManager(),
            ),
            patch(
                "ui.interfaces.character_area.discover_complete_character_records",
                return_value=[],
            ),
        ):
            area = CharacterArea()
            area.segment.setCurrentItem("system")

        self.assertEqual(area.segment.currentRouteKey(), "system")
        self.assertFalse(area.button_container.isHidden())
        self.assertTrue(area.delete_button.isHidden())


if __name__ == "__main__":
    unittest.main()
