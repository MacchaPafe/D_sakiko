"""世界书类型化编辑页面的 Qt 离屏测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PyQt5.QtCore import QRect, QSettings, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFormLayout, QSplitter, QWidget
from qfluentwidgets import (
    ComboBox,
    ListWidget,
    PrimaryPushButton,
    SearchLineEdit,
    TransparentDropDownPushButton,
)

from rag.worldbook.adapters import create_default_registry
from rag.worldbook.hashing import file_sha256
from rag.worldbook.models import ContentFileRecord, WorldbookEntry, WorldbookManifest
from ui.components.worldbook_editor import (
    EntryEditorHost,
    MultiSelectField,
    SelectionChip,
    SelectionOption,
    StoryEventEditor,
    TagEditor,
)
from ui.interfaces.worldbook_area import UnsavedChangesBox, WorldbookArea


class WorldbookUiTest(unittest.TestCase):
    """验证编辑器不加载重型资源并使用 Fluent 类型化组件。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试进程共享的 QApplication。"""

        cls.app = QApplication.instance() or QApplication([])

    def test_area_loads_without_index_or_embedding_model(self) -> None:
        """空应用根目录也应创建完整两栏编辑器。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area.show()
            self.app.processEvents()

            self.assertEqual(area.package_combo.count(), 0)
            self.assertFalse(area._controller.is_rag_available())
            self.assertIsInstance(area.package_combo, ComboBox)
            self.assertIsInstance(area.type_combo, ComboBox)
            self.assertIsInstance(area.search_edit, SearchLineEdit)
            self.assertIsInstance(area.entry_list, ListWidget)
            self.assertIsInstance(area.splitter, QSplitter)
            self.assertEqual(area.splitter.count(), 2)
            self.assertIsInstance(area.modified_host, EntryEditorHost)
            self.assertIsInstance(area.save_button, PrimaryPushButton)
            self.assertIsInstance(area.more_button, TransparentDropDownPushButton)
            self.assertEqual(area.sync_action.text(), "检查并修复搜索功能")
            self.assertEqual(area.rebuild_action.text(), "重新生成全部搜索数据…")
            self.assertEqual(area.right_panel.minimumWidth(), 0)
            self.assertFalse(
                area.show_hidden_check.geometry().intersects(
                    area.modified_only_check.geometry()
                )
            )
            area.close()

    def test_story_form_is_responsive_and_header_click_toggles_card(self) -> None:
        """窄编辑区应允许字段增长换行，且整块卡片标题都可展开。"""

        editor = StoryEventEditor(create_default_registry())
        editor.resize(420, 700)
        editor.show()
        self.app.processEvents()

        form = editor.title_edit.parentWidget().layout()
        self.assertIsInstance(form, QFormLayout)
        if not isinstance(form, QFormLayout):
            self.fail("剧情事件字段不在 QFormLayout 中")
        self.assertEqual(form.fieldGrowthPolicy(), QFormLayout.AllNonFixedFieldsGrow)
        self.assertEqual(form.rowWrapPolicy(), QFormLayout.WrapLongRows)
        self.assertFalse(hasattr(editor, "retrieval_confirm_check"))
        self.assertTrue(editor.retrieval_help_label.wordWrap())

        self.assertFalse(editor.retrieval_card.view.isVisible())
        QTest.mouseClick(editor.retrieval_card.headerLabel, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(editor.retrieval_card.view.isVisible())
        editor.close()

    def test_unsaved_changes_buttons_share_layout_metrics(self) -> None:
        """放弃按钮应复用内置按钮的伸展、居中和 macOS 几何规则。"""

        parent = QWidget()
        parent.resize(900, 700)
        parent.show()
        box = UnsavedChangesBox(parent)
        box.show()
        self.app.processEvents()

        buttons = (box.yesButton, box.discard_button, box.cancelButton)
        self.assertTrue(
            all(button.testAttribute(Qt.WA_LayoutUsesWidgetRect) for button in buttons)
        )
        self.assertEqual([box.buttonLayout.stretch(index) for index in range(3)], [1, 1, 1])
        self.assertTrue(box.buttonLayout.itemAt(1).alignment() & Qt.AlignVCenter)
        self.assertEqual(len({button.height() for button in buttons}), 1)
        self.assertEqual(len({button.y() for button in buttons}), 1)
        box.close()
        parent.close()

    def test_tag_editor_reflows_immediately_after_add_and_remove(self) -> None:
        """动态增删标签后不应等待窗口缩放才重新排列。"""

        editor = TagEditor()
        editor.resize(260, 180)
        editor.show()
        editor.set_value(["第一个较长标签", "第二个较长标签"])
        self.app.processEvents()
        self._assert_flow_items_do_not_overlap(editor)

        editor.input_edit.setText("新增标签")
        QTest.keyClick(editor.input_edit, Qt.Key_Return)
        self.app.processEvents()
        self.assertIn("新增标签", editor.value())
        self._assert_flow_items_do_not_overlap(editor)

        first_item = editor.chip_layout.itemAt(0)
        first_chip = first_item.widget() if first_item is not None else None
        self.assertIsInstance(first_chip, SelectionChip)
        if not isinstance(first_chip, SelectionChip):
            self.fail("标签布局中缺少 SelectionChip")
        QTest.mouseClick(first_chip.remove_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertNotIn("第一个较长标签", editor.value())
        self._assert_flow_items_do_not_overlap(editor)
        editor.close()

    def test_multi_select_reflows_immediately_after_selection_changes(self) -> None:
        """多选标签变化后应隐藏旧控件并立即重新排列新控件。"""

        field = MultiSelectField("选择项目")
        field.set_options(
            [
                SelectionOption("a", "第一个较长项目"),
                SelectionOption("b", "第二个较长项目"),
                SelectionOption("c", "第三个较长项目"),
            ]
        )
        field.resize(260, 180)
        field.show()
        field.set_value(["a", "b"])
        self.app.processEvents()
        self._assert_flow_items_do_not_overlap(field)

        field.set_value(["b", "c"])
        self.app.processEvents()
        self._assert_flow_items_do_not_overlap(field)
        visible_chips = [
            chip
            for chip in field.chip_container.findChildren(
                SelectionChip, options=Qt.FindDirectChildrenOnly
            )
            if chip.isVisible()
        ]
        self.assertEqual(len(visible_chips), 2)
        field.close()

    def test_clean_entry_cannot_create_empty_override(self) -> None:
        """未修改的条目应禁用保存，直接调用保存也不得写入 Override。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_package(root, _story_entry())
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area._controller.reconcile_all = _do_nothing
            area.show()
            self.app.processEvents()

            self.assertFalse(area.modified_host.editor().is_dirty())
            self.assertFalse(area.save_button.isEnabled())
            self.assertFalse(area._save_current())
            self.assertEqual(area._states.load("test.package").overrides, [])

            editor = area.modified_host.editor()
            self.assertIsInstance(editor, StoryEventEditor)
            if not isinstance(editor, StoryEventEditor):
                self.fail("当前编辑器不是 StoryEventEditor")
            editor.title_edit.setText("临时修改")
            editor.title_edit.setText("测试事件")
            self.assertTrue(editor.is_dirty())
            self.assertFalse(area._save_current())
            self.assertFalse(editor.is_dirty())
            self.assertEqual(area._states.load("test.package").overrides, [])
            area.close()

    def test_story_form_saves_full_override_and_preserves_hidden_importance(self) -> None:
        """类型表单应保存完整 Override 且不丢失未展示的 importance。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = _story_entry()
            _write_package(root, entry)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area._controller.reconcile_all = _do_nothing
            area.show()
            self.app.processEvents()

            editor = area.modified_host.editor()
            self.assertIsInstance(editor, StoryEventEditor)
            story_editor = editor
            if not isinstance(story_editor, StoryEventEditor):
                self.fail("当前编辑器不是 StoryEventEditor")
            story_editor.title_edit.setText("用户修改后的标题")
            area.search_edit.setText("不会命中的搜索词")
            self.app.processEvents()
            self.assertIs(area.modified_host.editor(), story_editor)
            self.assertTrue(story_editor.is_dirty())
            self.assertTrue(area.save_button.isEnabled())
            area.search_edit.clear()
            self.app.processEvents()
            story_editor.retrieval_edit.setPlainText("用于检索的用户修改标题")

            self.assertTrue(area._save_current())
            saved = area._states.load("test.package").overrides[0]
            self.assertEqual(saved.content["title"], "用户修改后的标题")
            self.assertEqual(saved.content["importance"], 7)
            self.assertEqual(saved.entry_id, entry.entry_id)
            self.assertTrue(area.modified_badge.isVisible())
            self.assertTrue(area.source_segment.isVisible())
            area.close()

    def _assert_flow_items_do_not_overlap(
        self, editor: TagEditor | MultiSelectField
    ) -> None:
        """断言当前标签布局中的所有可见控件互不相交。"""

        geometries: list[QRect] = []
        for index in range(editor.chip_layout.count()):
            item = editor.chip_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if not isinstance(widget, SelectionChip) or not widget.isVisible():
                continue
            geometry = widget.geometry()
            self.assertTrue(all(not geometry.intersects(existing) for existing in geometries))
            geometries.append(geometry)


def _do_nothing() -> None:
    """替代 UI 测试中不需要启动的异步索引同步。"""


def _story_entry() -> WorldbookEntry:
    """创建使用正式 MyGO 时间坐标的测试剧情事件。"""

    return WorldbookEntry(
        entry_id="12345678-1234-5678-1234-567812345678",
        entry_type="story_event",
        content={
            "timeline_id": "bang_dream_original",
            "occurred_story_year": None,
            "series_id": "its_mygo",
            "episode": 1,
            "time_order": 4000,
            "visible_from": 4000,
            "visible_to": 999999,
            "canon_branch": "main",
            "title": "测试事件",
            "summary": "测试摘要",
            "participants": ["anon"],
            "importance": 7,
            "tags": ["测试"],
            "retrieval_text": "测试事件的检索摘要",
        },
    )


def _write_package(root: Path, entry: WorldbookEntry) -> None:
    """写入 UI 测试使用的最小正式世界书包。"""

    package_dir = root / "GPT_SoVITS" / "rag" / "worldbooks" / "official" / "test.package"
    content_dir = package_dir / "content"
    content_dir.mkdir(parents=True)
    content_path = content_dir / "story_events.json"
    content_path.write_text(
        json.dumps({"entries": [entry.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = WorldbookManifest(
        package_id="test.package",
        package_version="1.0.0",
        display_name="测试世界书",
        package_type="season",
        timeline_id="bang_dream_original",
        content_files=[
            ContentFileRecord(
                path="content/story_events.json",
                sha256=file_sha256(content_path),
                entry_type="story_event",
            )
        ],
    )
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
