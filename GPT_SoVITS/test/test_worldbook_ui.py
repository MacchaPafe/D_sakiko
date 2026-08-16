"""世界书类型化编辑页面的 Qt 离屏测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from PyQt5.QtCore import QRect, QSettings, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFormLayout, QSplitter, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    ListWidget,
    PrimaryPushButton,
    SearchLineEdit,
    ScrollArea,
    ToolButton,
    TransparentDropDownPushButton,
)

from rag.models import BAND_MEMBERS, BandId, CharacterId
from rag.worldbook.adapters import create_default_registry
from rag.worldbook.hashing import file_sha256
from rag.worldbook.models import (
    ContentFileRecord,
    PackageDependency,
    WorldbookEntry,
    WorldbookManifest,
    WorldbookOverride,
    WorldbookReadiness,
)
from rag.worldbook.runtime.readiness import WorldbookIndexReadinessState
from rag.worldbook.user_state import WorldbookUserStateRepository
from ui.components.worldbook_editor import (
    EntryEditorHost,
    MultiSelectDialog,
    MultiSelectField,
    SelectionChip,
    SelectionOption,
    StoryEventEditor,
    TagEditor,
    _character_options,
)
from ui.interfaces.worldbook_area import (
    StoryEventDeletionBox,
    UnsavedChangesBox,
    WorldbookArea,
)
from ui.controllers.worldbook_sync_controller import WorldbookSyncController


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
            self.assertIsInstance(area.add_entry_button, ToolButton)
            self.assertIsInstance(area.source_filter_combo, ComboBox)
            self.assertEqual(area.source_filter_combo.count(), 3)
            self.assertIsInstance(area.search_edit, SearchLineEdit)
            self.assertIsInstance(area.entry_list, ListWidget)
            self.assertIsInstance(area.splitter, QSplitter)
            self.assertEqual(area.splitter.count(), 2)
            self.assertIsInstance(area.modified_host, EntryEditorHost)
            self.assertIsInstance(area.save_button, PrimaryPushButton)
            self.assertIsInstance(area.more_button, TransparentDropDownPushButton)
            self.assertEqual(area.sync_action.text(), "检查并修复搜索功能")
            self.assertEqual(area.rebuild_action.text(), "重新生成全部搜索数据…")
            self.assertEqual(
                _menu_action_texts(area),
                ["检查并修复搜索功能", "重新生成全部搜索数据…"],
            )
            self.assertEqual(area.right_panel.minimumWidth(), 0)
            self.assertFalse(
                area.show_hidden_check.geometry().intersects(
                    area.source_filter_combo.geometry()
                )
            )
            area.close()

    def test_sync_controller_updates_shared_query_readiness(self) -> None:
        """同步状态变化应立即写入聊天检索共享状态。"""

        with tempfile.TemporaryDirectory() as directory:
            state = WorldbookIndexReadinessState(WorldbookReadiness.READY)
            controller = WorldbookSyncController(
                Path(directory),
                readiness_state=state,
            )

            controller._set_readiness("syncing")
            self.assertEqual(state.get(), WorldbookReadiness.SYNCING)
            self.assertFalse(controller.is_rag_available())

            controller._set_readiness("degraded")
            self.assertEqual(state.get(), WorldbookReadiness.DEGRADED)
            self.assertTrue(controller.is_rag_available())
            controller.deleteLater()

    def test_story_extension_can_be_created_and_filtered(self) -> None:
        """新增按钮应创建可编辑草稿，并在保存后选中用户条目。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_package(root, _story_entry())
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area._controller.reconcile_all = _do_nothing
            area.resize(1120, 820)
            area.show()
            self.app.processEvents()

            area._begin_create_extension()
            self.app.processEvents()
            self.assertIsNotNone(area._draft_entry)
            self.assertTrue(area.draft_badge.isVisible())
            self.assertEqual(area.save_button.text(), "创建条目")
            self.assertEqual(area.discard_button.text(), "取消创建")

            editor = area.modified_host.editor()
            self.assertIsInstance(editor, StoryEventEditor)
            if not isinstance(editor, StoryEventEditor):
                self.fail("新增草稿没有使用剧情事件编辑器")
            self.assertTrue(editor.extension_scope_card.isVisible())
            self.assertGreater(
                area.modified_host.height(), area.modified_scroll.viewport().height()
            )
            self.assertGreater(
                area.modified_scroll.verticalScrollBar().maximum(), 0
            )
            editor.title_edit.setText("我添加的事件")
            editor.summary_edit.setPlainText("用于自动生成检索文本的摘要")
            editor.participants_field.set_value(["anon"])
            editor.known_by_field.set_value(["anon"])
            editor.tags_field.set_value(["测试"])

            self.assertTrue(area._save_current())
            extensions = area._states.load("test.package").extensions
            self.assertEqual(len(extensions), 1)
            saved = extensions[0]
            self.assertEqual(saved.content["importance"], 3)
            self.assertEqual(saved.content["known_by_character_ids"], ["anon"])
            self.assertEqual(saved.content["retrieval_text"], "用于自动生成检索文本的摘要")
            self.assertEqual(area.source_filter_combo.currentData(), "all")
            self.assertIsNotNone(area._current_record)
            self.assertEqual(area._current_record.entry.entry_id, saved.entry_id)
            self.assertTrue(area.added_badge.isVisible())

            area.source_filter_combo.setCurrentIndex(
                area.source_filter_combo.findData("user")
            )
            self.app.processEvents()
            self.assertEqual(area.entry_list.count(), 1)
            row = area.entry_list.itemWidget(area.entry_list.item(0))
            self.assertIsInstance(row, QWidget)
            if isinstance(row, QWidget):
                self.assertIn("我添加的", row.findChild(CaptionLabel).text())

            with patch("ui.interfaces.worldbook_area.MessageBox") as message_box:
                message_box.return_value.exec.return_value = True
                area._delete_current_extension()
            message_box.assert_called_once_with(
                "删除“我添加的事件”？",
                "这个剧情事件将被永久删除，并从世界书搜索中移除。此操作无法撤销。",
                area.window(),
            )
            self.app.processEvents()
            self.assertEqual(area._states.load("test.package").extensions, [])
            self.assertEqual(area.source_filter_combo.currentData(), "user")
            self.assertEqual(area.entry_list.count(), 0)
            area.close()

    def test_story_extension_validation_uses_aggregated_user_facing_message(self) -> None:
        """空白剧情事件应一次提示全部缺失内容，并聚焦首个字段。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_package(root, _story_entry())
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area.resize(1120, 820)
            area.show()
            area._begin_create_extension()
            self.app.processEvents()
            editor = area.modified_host.editor()
            self.assertIsInstance(editor, StoryEventEditor)
            if not isinstance(editor, StoryEventEditor):
                self.fail("新增草稿没有使用剧情事件编辑器")

            with (
                patch.object(area, "_show_error") as show_error,
                patch.object(area._edit_service, "save_extension") as save_extension,
            ):
                self.assertFalse(area._save_current())
            self.app.processEvents()

            show_error.assert_called_once_with(
                "无法创建条目",
                "请完善以下内容：事件标题；事件摘要；至少一名参与角色；至少一个标签。",
            )
            save_extension.assert_not_called()
            self.assertTrue(editor.title_edit.hasFocus())
            self.assertEqual(
                editor.tags_field.help_label.text(),
                "添加角色、地点、事件或主题等关键词，可以让这条内容更容易在相关对话中被找到。",
            )
            self.assertTrue(editor.tags_field.help_label.isVisible())
            self.assertIn(
                "标签（必填）",
                [label.text() for label in editor.findChildren(BodyLabel)],
            )
            area.close()

    def test_orphan_override_is_only_in_my_content_and_deletes_without_sync(self) -> None:
        """孤立修改应由警告入口导航，并在所属包中删除而不触发索引同步。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = _story_entry()
            _write_package(root, official)
            orphan = official.model_copy(
                update={
                    "entry_id": uuid4(),
                    "content": {**official.content, "title": "失去官方条目的修改"},
                }
            )
            repository = WorldbookUserStateRepository(
                root / "knowledge_base" / "worldbooks" / "package-state"
            )
            repository.put_override(
                "test.package",
                WorldbookOverride(
                    entry_id=orphan.entry_id,
                    entry_type="story_event",
                    schema_version=0,
                    base_revision="missing-base",
                    content=orphan.content,
                ),
            )
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            reconcile = Mock()
            area._controller.reconcile_all = reconcile
            area.show()
            self.app.processEvents()

            self.assertEqual(area.entry_list.count(), 1)
            self.assertTrue(area.orphan_navigation_button.isVisible())
            self.assertEqual(area.orphan_navigation_button.text(), "1 条修改需处理")

            area._navigate_to_next_orphan()
            self.app.processEvents()
            self.assertEqual(area.source_filter_combo.currentData(), "user")
            self.assertEqual(area.entry_list.count(), 1)
            self.assertIsNotNone(area._current_record)
            self.assertTrue(area._current_record.orphaned_override)
            self.assertTrue(area.orphan_badge.isVisible())
            self.assertTrue(area.modified_host.editor()._read_only)
            self.assertTrue(area.delete_extension_action.isEnabled())
            self.assertEqual(
                _menu_action_texts(area),
                [
                    "导出原始修改",
                    "永久删除孤立修改",
                    "检查并修复搜索功能",
                    "重新生成全部搜索数据…",
                ],
            )

            with patch("ui.interfaces.worldbook_area.MessageBox") as message_box:
                message_box.return_value.exec.return_value = True
                area._delete_current_extension()
            self.app.processEvents()

            self.assertEqual(repository.load("test.package").overrides, [])
            self.assertFalse(area.orphan_navigation_button.isVisible())
            reconcile.assert_not_called()
            area.close()

    def test_story_event_delete_dialog_applies_reference_detach_once(self) -> None:
        """剧情事件删除确认后应保留扩展想法正文并只触发一次索引同步。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_package(root, _story_entry())
            story = _story_entry().model_copy(update={"entry_id": uuid4()})
            thought = _thought_entry(story.entry_id)
            repository = WorldbookUserStateRepository(
                root / "knowledge_base" / "worldbooks" / "package-state"
            )
            repository.put_extension("test.package", story)
            repository.put_extension("test.package", thought)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            reconcile = Mock()
            area._controller.reconcile_all = reconcile
            area.show()
            self.app.processEvents()
            area._display_record(area._records[story.entry_id])

            plan = area._edit_service.plan_story_event_deletion(
                "test.package",
                story.entry_id,
                area._persistent_catalog(),
            )
            deletion_box = StoryEventDeletionBox(
                plan,
                {"test.package": "测试世界书"},
                area.window(),
            )
            impact_scroll = deletion_box.findChild(ScrollArea)
            self.assertIsNotNone(impact_scroll)
            if impact_scroll is None:
                self.fail("关联内容删除对话框缺少滚动区域")
            self.assertIn("transparent", impact_scroll.viewport().styleSheet())
            self.assertIsNotNone(impact_scroll.widget())
            if impact_scroll.widget() is not None:
                self.assertIn("transparent", impact_scroll.widget().styleSheet())
            deletion_box.close()

            with patch(
                "ui.interfaces.worldbook_area.StoryEventDeletionBox.exec",
                return_value=1,
            ):
                area._delete_current_extension()
            self.app.processEvents()
            state = repository.load("test.package")

            self.assertNotIn(story.entry_id, {entry.entry_id for entry in state.extensions})
            saved_thought = next(
                entry for entry in state.extensions if entry.entry_id == thought.entry_id
            )
            self.assertEqual(saved_thought.content["story_event_entry_ids"], [])
            self.assertEqual(saved_thought.content["thought_text"], thought.content["thought_text"])
            reconcile.assert_called_once_with()
            area.close()

    def test_dependency_extension_is_read_only_until_owner_is_selected(self) -> None:
        """依赖包扩展应提示切换所属世界书，并只在所属根包下开放编辑。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common_official = _story_entry()
            root_official = _story_entry().model_copy(update={"entry_id": uuid4()})
            _write_package(
                root,
                common_official,
                package_id="common.package",
                display_name="通用世界书",
                package_type="common",
            )
            _write_package(
                root,
                root_official,
                package_id="root.package",
                display_name="根世界书",
                dependencies=[PackageDependency(package_id="common.package")],
            )
            extension = _story_entry().model_copy(
                update={
                    "entry_id": uuid4(),
                    "content": {**_story_entry().content, "title": "依赖包自定义事件"},
                }
            )
            repository = WorldbookUserStateRepository(
                root / "knowledge_base" / "worldbooks" / "package-state"
            )
            repository.put_extension("common.package", extension)
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            settings.setValue("last_package_id", "root.package")
            area = WorldbookArea(app_root=root, settings=settings)
            area._controller.reconcile_all = _do_nothing
            area.show()
            self.app.processEvents()

            dependency_record = area._records[extension.entry_id]
            area._display_record(dependency_record)
            self.app.processEvents()
            self.assertTrue(area.added_badge.isVisible())
            self.assertTrue(area.modified_host.editor()._read_only)
            self.assertEqual(
                area.problem_label.text(),
                "该条目属于“通用世界书”。请在上方直接选择该世界书后进行编辑。",
            )
            self.assertFalse(area.delete_extension_action.isEnabled())
            self.assertFalse(area.show_state_location_action.isEnabled())
            self.assertEqual(
                _menu_action_texts(area),
                ["检查并修复搜索功能", "重新生成全部搜索数据…"],
            )

            area.package_combo.setCurrentIndex(
                area.package_combo.findData("common.package")
            )
            self.app.processEvents()
            area._display_record(area._records[extension.entry_id])
            self.app.processEvents()
            self.assertFalse(area.modified_host.editor()._read_only)
            self.assertTrue(area.delete_extension_action.isEnabled())
            self.assertEqual(
                _menu_action_texts(area),
                [
                    "永久删除条目",
                    "检查并修复搜索功能",
                    "重新生成全部搜索数据…",
                ],
            )
            area.close()

    def test_more_menu_uses_current_record_state_instead_of_disabled_actions(self) -> None:
        """菜单应只包含当前条目适用动作，并仅在基准冲突时暴露状态文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = _story_entry()
            _write_package(root, official)
            repository = WorldbookUserStateRepository(
                root / "knowledge_base" / "worldbooks" / "package-state"
            )
            repository.put_override(
                "test.package",
                WorldbookOverride(
                    entry_id=official.entry_id,
                    entry_type=official.entry_type,
                    schema_version=official.schema_version,
                    base_revision="outdated-base",
                    content={**official.content, "title": "用户标题"},
                ),
            )
            settings = QSettings(str(root / "ui.ini"), QSettings.IniFormat)
            area = WorldbookArea(app_root=root, settings=settings)
            area._controller.reconcile_all = _do_nothing
            area.show()
            self.app.processEvents()

            self.assertEqual(
                _menu_action_texts(area),
                [
                    "隐藏条目",
                    "恢复官方内容",
                    "保留我的修改",
                    "导出原始修改",
                    "打开用户状态文件位置",
                    "检查并修复搜索功能",
                    "重新生成全部搜索数据…",
                ],
            )
            self.assertEqual(
                sum(
                    area.more_button.menu().view.item(index).data(Qt.DecorationRole)
                    == "seperator"
                    for index in range(area.more_button.menu().view.count())
                ),
                1,
            )
            with patch(
                "ui.interfaces.worldbook_area.show_file_in_manager"
            ) as show_file:
                area._show_user_state_location()
            show_file.assert_called_once_with(repository.path_for("test.package"))
            with (
                patch.object(area, "_resolve_unsaved_changes", return_value=False),
                patch("ui.interfaces.worldbook_area.MessageBox") as message_box,
                patch.object(
                    area._edit_service, "confirm_current_base"
                ) as confirm_current_base,
            ):
                area._restore_official()
                area._confirm_current_base()
            message_box.assert_not_called()
            confirm_current_base.assert_not_called()

            repository.remove_override("test.package", official.entry_id)
            area._refresh_records(official.entry_id)
            self.app.processEvents()
            self.assertEqual(
                _menu_action_texts(area),
                [
                    "隐藏条目",
                    "检查并修复搜索功能",
                    "重新生成全部搜索数据…",
                ],
            )

            broken_content = dict(official.content)
            broken_content.pop("summary")
            repository.put_override(
                "test.package",
                WorldbookOverride(
                    entry_id=official.entry_id,
                    entry_type=official.entry_type,
                    schema_version=official.schema_version,
                    base_revision="outdated-base",
                    content=broken_content,
                ),
            )
            area._refresh_records(official.entry_id)
            self.app.processEvents()
            self.assertEqual(
                _menu_action_texts(area),
                [
                    "恢复官方内容",
                    "导出原始修改",
                    "检查并修复搜索功能",
                    "重新生成全部搜索数据…",
                ],
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

    def test_free_tag_chip_edits_inline_and_merges_duplicates(self) -> None:
        """自由标签应支持原位改名、逗号文字和重复标签合并。"""

        editor = TagEditor()
        editor.set_value(["旧标签", "已有标签"])
        editor.resize(360, 180)
        editor.show()
        changed = Mock()
        editor.value_changed.connect(changed)
        self.app.processEvents()

        first_item = editor.chip_layout.itemAt(0)
        first_chip = first_item.widget() if first_item is not None else None
        self.assertIsInstance(first_chip, SelectionChip)
        if not isinstance(first_chip, SelectionChip):
            self.fail("标签布局中缺少可编辑 SelectionChip")
        QTest.mouseDClick(first_chip.label, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(first_chip.edit_field.isVisible())
        self._assert_flow_items_do_not_overlap(editor)
        first_chip.edit_field.setText("新,标签")
        QTest.keyClick(first_chip.edit_field, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(editor.value(), ["新,标签", "已有标签"])
        self.assertEqual(changed.call_count, 1)

        renamed_item = editor.chip_layout.itemAt(0)
        renamed_chip = renamed_item.widget() if renamed_item is not None else None
        self.assertIsInstance(renamed_chip, SelectionChip)
        if not isinstance(renamed_chip, SelectionChip):
            self.fail("改名后标签布局中缺少 SelectionChip")
        renamed_chip._start_edit()
        renamed_chip.edit_field.setText("已有标签")
        QTest.keyClick(renamed_chip.edit_field, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(editor.value(), ["已有标签"])
        self.assertEqual(changed.call_count, 2)
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
        self.assertTrue(all(not chip._editable for chip in visible_chips))
        field.close()

    def test_character_dialog_prioritizes_selected_bands_without_reordering_values(self) -> None:
        """角色对话框应置顶已选乐队、搜索分组并保持参与角色值顺序。"""

        parent = QWidget()
        parent.resize(900, 700)
        parent.show()
        dialog = MultiSelectDialog(
            "选择参与角色",
            _character_options(),
            [CharacterId.ANON.value, CharacterId.SAKIKO.value],
            parent,
        )
        headers = _visible_group_headers(dialog)
        self.assertEqual(headers[:2], ["It's MyGO!!!!!", "Ave Mujica"])

        uika_item = next(
            dialog.list_widget.item(index)
            for index in range(dialog.list_widget.count())
            if dialog.list_widget.item(index).data(Qt.UserRole) == CharacterId.UIKA.value
        )
        uika_item.setCheckState(Qt.Checked)
        self.assertEqual(
            dialog.selected_keys(),
            [
                CharacterId.ANON.value,
                CharacterId.SAKIKO.value,
                CharacterId.UIKA.value,
            ],
        )
        self.assertEqual(_visible_group_headers(dialog)[:2], headers[:2])

        dialog.search_edit.setText("MyGO")
        self.app.processEvents()
        self.assertEqual(_visible_group_headers(dialog), ["It's MyGO!!!!!"])
        self.assertEqual(_visible_option_count(dialog), 5)

        dialog.search_edit.setText("灯")
        self.app.processEvents()
        self.assertEqual(_visible_group_headers(dialog), ["It's MyGO!!!!!"])
        self.assertEqual(_visible_option_count(dialog), 1)
        dialog.close()
        parent.close()

    def test_role_bands_cover_every_band_member_exactly_once(self) -> None:
        """九支角色乐队应各含五人，且不包含仅供标注使用的配角。"""

        members = [character for band in BandId for character in BAND_MEMBERS[band]]
        self.assertTrue(all(len(BAND_MEMBERS[band]) == 5 for band in BandId))
        self.assertEqual(len(members), len(set(members)))
        self.assertNotIn(CharacterId.RIRIKO, members)
        self.assertEqual(
            set(members),
            set(CharacterId) - {CharacterId.RIRIKO},
        )

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
            story_editor.known_by_field.set_value(["anon", "tomori"])
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
            self.assertEqual(
                saved.content["known_by_character_ids"],
                ["anon", "tomori"],
            )
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


def _menu_action_texts(area: WorldbookArea) -> list[str]:
    """返回动态更多菜单中除分隔线外的动作文字。"""

    return [
        action.text()
        for action in area.more_button.menu().menuActions()
        if not action.isSeparator()
    ]


def _visible_group_headers(dialog: MultiSelectDialog) -> list[str]:
    """返回多选对话框当前可见的不可勾选分组标题。"""

    return [
        dialog.list_widget.item(index).text()
        for index in range(dialog.list_widget.count())
        if not dialog.list_widget.item(index).isHidden()
        and not dialog.list_widget.item(index).flags() & Qt.ItemIsUserCheckable
    ]


def _visible_option_count(dialog: MultiSelectDialog) -> int:
    """统计多选对话框当前可见的可勾选项。"""

    return sum(
        1
        for index in range(dialog.list_widget.count())
        if not dialog.list_widget.item(index).isHidden()
        and dialog.list_widget.item(index).flags() & Qt.ItemIsUserCheckable
    )


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


def _thought_entry(story_entry_id: UUID) -> WorldbookEntry:
    """创建引用指定剧情事件的测试角色想法扩展。"""

    return WorldbookEntry(
        entry_id=uuid4(),
        entry_type="character_thought",
        content={
            "character_id": "tomori",
            "series_id": "its_mygo",
            "timeline_id": "bang_dream_original",
            "canon_branch": "main",
            "thought_thread_key": str(uuid4()),
            "canonical_subject": "测试事件",
            "thought_aspect": "记忆",
            "thought_text": "灯仍然记得这个事件的影响。",
            "epistemic_status": "believes",
            "visible_from": 4000,
            "visible_to": 999999,
            "story_event_entry_ids": [str(story_entry_id)],
            "tags": ["测试"],
            "retrieval_text": "灯仍然记得这个事件的影响。",
        },
    )


def _write_package(
    root: Path,
    entry: WorldbookEntry,
    package_id: str = "test.package",
    display_name: str = "测试世界书",
    package_type: Literal["season", "common"] = "season",
    dependencies: list[PackageDependency] | None = None,
) -> None:
    """写入 UI 测试使用的最小正式世界书包。"""

    package_dir = root / "GPT_SoVITS" / "rag" / "worldbooks" / "official" / package_id
    content_dir = package_dir / "content"
    content_dir.mkdir(parents=True)
    content_path = content_dir / "story_events.json"
    content_path.write_text(
        json.dumps({"entries": [entry.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = WorldbookManifest(
        package_id=package_id,
        package_version="1.0.0",
        display_name=display_name,
        package_type=package_type,
        timeline_id="bang_dream_original",
        conversation_context=(
            {
                "series_id": "its_mygo",
                "canon_branch": "main",
                "story_year": None,
            }
            if package_type == "season"
            else None
        ),
        dependencies=dependencies or [],
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
