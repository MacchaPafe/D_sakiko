"""世界书对话控制模块与个性化诊断设置测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QMenu,
    QMessageBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from character import CharacterAttributes
from chat.chat import Chat, ChatManager, Message
from emotion_enum import EmotionEnum
from qconfig import d_sakiko_config
from rag.models import CharacterId
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.runtime.diagnostics import WorldbookDiagnosticStore
from rag.worldbook.runtime.models import WorldbookRootOption, WorldbookTurnSnapshot
from ui.components.custom_setting_area import CustomSettingArea
from ui_main.components.worldbook_conversation_control import (
    WORLDBOOK_DIAGNOSTICS_UI_ENV,
    WorldbookConversationControl,
)


class _ConfigItem:
    """提供测试所需的可变配置值。"""

    def __init__(self, value: object) -> None:
        """保存初始配置值。"""

        self.value = value


class _FakeWorldbookConfig:
    """在内存中模拟世界书相关 QConfig 行为。"""

    def __init__(self) -> None:
        """创建默认世界书配置和写入记录。"""

        self.worldbook_character_mappings = _ConfigItem({})
        self.worldbook_diagnostics_persistence = _ConfigItem(True)
        self.worldbook_diagnostics_disclosure_seen = _ConfigItem(True)
        self.set_calls: list[tuple[object, object]] = []

    def set(self, item: object, value: object) -> None:
        """写入测试配置项并记录调用。"""

        if isinstance(item, _ConfigItem):
            item.value = value
        self.set_calls.append((item, value))


def _root_option(*, enabled: bool = True) -> WorldbookRootOption:
    """创建一项 MyGO 世界书根包菜单数据。"""

    return WorldbookRootOption(
        package_id="official.bang_dream.its_mygo",
        display_name="BanG Dream! It's MyGO!!!!!",
        package_version="0.1.0",
        enabled=enabled,
        unavailable_reasons=[] if enabled else ["index_unavailable: 测试原因"],
        available_characters=[CharacterId.ANON, CharacterId.SOYO],
    )


def _snapshot(episode: int = 5) -> WorldbookTurnSnapshot:
    """创建一份可用于重新生成测试的世界书回合快照。"""

    return WorldbookTurnSnapshot(
        root_package_id=_root_option().package_id,
        root_package_version="0.1.0",
        package_ids=[_root_option().package_id],
        package_versions={_root_option().package_id: "0.1.0"},
        package_depths={_root_option().package_id: 0},
        character_id="anon",
        series_id="its_mygo",
        timeline_id="bang_dream_original",
        canon_branch="main",
        current_time=4099,
        story_year=3,
        episode=episode,
    )


def _menu_action(menu: QMenu, text: str) -> QAction:
    """从菜单中按可见文本取得一项操作。"""

    return next(action for action in menu.actions() if action.text() == text)


class WorldbookConversationControlTest(unittest.TestCase):
    """通过公开接口验证世界书对话控制模块。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """创建无界面 Qt 应用。"""

        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def setUp(self) -> None:
        """构造完全隔离于生产聊天存档的世界书控制模块。"""

        self.parent = QWidget()
        self.character = CharacterAttributes()
        self.character.character_name = "爱音"
        self.character.character_folder_name = "anon"
        self.chat = Chat.new_single_chat(self.character, name="测试对话")
        self.manager = ChatManager([self.chat])
        self.manager.save = mock.Mock()
        self.config = _FakeWorldbookConfig()
        self.catalog = mock.Mock(spec=WorldbookRootCatalog)
        self.catalog.list_roots.return_value = [_root_option()]
        self.store = mock.Mock(spec=WorldbookDiagnosticStore)
        self.store.recent.return_value = []
        self.control = WorldbookConversationControl(
            chat_manager=self.manager,
            config=self.config,
            catalog=self.catalog,
            diagnostic_store=self.store,
            button_height=28,
            parent=self.parent,
        )
        self.control.bind(self.chat, self.character)

    def tearDown(self) -> None:
        """销毁测试控件并处理 Qt 延迟删除。"""

        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def test_button_uses_fixed_label_and_exposes_configuration_menu(self) -> None:
        """完整配置应保持固定按钮文案，并在菜单说明当前配置。"""

        settings = self.chat.meta.worldbook
        settings.enabled = True
        settings.root_package_id = _root_option().package_id
        settings.episode = 5

        self.control.bind(self.chat, self.character)

        self.assertEqual(self.control.button.text(), "世界书")
        self.assertTrue(self.control.button.isChecked())
        self.assertEqual(
            self.control.button.popupMode(),
            QToolButton.MenuButtonPopup,
        )
        menu = self.control.button.menu()
        self.assertIsInstance(menu, QMenu)
        action_texts = [action.text() for action in menu.actions()]
        self.assertNotIn("启用世界书", action_texts)
        self.assertIn("世界书包", action_texts)
        self.assertIn("剧情进度", action_texts)
        self.assertIn("角色知识视角：爱音", action_texts)
        self.assertIn("导出此对话的世界书诊断…", action_texts)
        summary_action = menu.actions()[0]
        self.assertIsInstance(summary_action, QWidgetAction)
        summary_widget = summary_action.defaultWidget()
        self.assertIsNotNone(summary_widget)
        self.assertIn("第 5 集结束后", summary_widget.text())
        episode_menu = _menu_action(menu, "剧情进度").menu()
        self.assertIsNotNone(episode_menu)
        self.assertEqual(len(episode_menu.actions()), 13)

    def test_parent_close_does_not_save_or_touch_chat_storage(self) -> None:
        """关闭所属窗口不应让控制模块自行保存聊天存档。"""

        self.parent.close()
        self.app.processEvents()

        self.manager.save.assert_not_called()

    def test_unavailable_root_is_visible_but_disabled_with_reason(self) -> None:
        """不可用根包仍应展示，并携带结构化不可用原因。"""

        self.catalog.list_roots.return_value = [_root_option(enabled=False)]
        self.control.bind(self.chat, self.character)

        menu = self.control.button.menu()
        root_menu = _menu_action(menu, "世界书包").menu()
        self.assertIsNotNone(root_menu)
        package_action = root_menu.actions()[0]
        self.assertFalse(package_action.isEnabled())
        self.assertIn("index_unavailable", package_action.toolTip())

    def test_runtime_diagnostics_controls_require_environment_variable(self) -> None:
        """运行期诊断入口默认隐藏，仅在调试环境变量开启时显示。"""

        with mock.patch.dict(
            os.environ,
            {WORLDBOOK_DIAGNOSTICS_UI_ENV: ""},
        ):
            self.control.bind(self.chat, self.character)
            hidden_texts = [
                action.text() for action in self.control.button.menu().actions()
            ]

        self.assertNotIn("显示本次运行的诊断", hidden_texts)
        self.assertNotIn("查看最近诊断…", hidden_texts)
        self.assertIn("导出此对话的世界书诊断…", hidden_texts)

        with mock.patch.dict(
            os.environ,
            {WORLDBOOK_DIAGNOSTICS_UI_ENV: "1"},
        ):
            self.control.bind(self.chat, self.character)
            _menu_action(
                self.control.button.menu(),
                "显示本次运行的诊断",
            ).trigger()
            visible_texts = [
                action.text() for action in self.control.button.menu().actions()
            ]

        self.assertIn("显示本次运行的诊断", visible_texts)
        self.assertIn("查看最近诊断…", visible_texts)
        self.assertIn("导出此对话的世界书诊断…", visible_texts)

    def test_selecting_root_defaults_only_unset_episode_to_last_episode(self) -> None:
        """选择根包应默认未设置进度，并保留用户已有的明确进度。"""

        root_menu = _menu_action(
            self.control.button.menu(),
            "世界书包",
        ).menu()
        self.assertIsNotNone(root_menu)
        root_menu.actions()[0].trigger()

        self.assertEqual(self.chat.meta.worldbook.episode, 13)
        self.assertFalse(self.chat.meta.worldbook.enabled)
        self.manager.save.assert_called_once_with()

        self.manager.save.reset_mock()
        self.chat.meta.worldbook.episode = 5
        self.control.bind(self.chat, self.character)
        root_menu = _menu_action(
            self.control.button.menu(),
            "世界书包",
        ).menu()
        self.assertIsNotNone(root_menu)
        root_menu.actions()[0].trigger()

        self.assertEqual(self.chat.meta.worldbook.episode, 5)
        self.manager.save.assert_called_once_with()

    def test_episode_rollback_warns_then_saves_new_episode(self) -> None:
        """剧情进度回退应先提示历史泄露风险再保存。"""

        self.chat.meta.worldbook.episode = 8
        self.control.bind(self.chat, self.character)
        episode_menu = _menu_action(
            self.control.button.menu(),
            "剧情进度",
        ).menu()
        self.assertIsNotNone(episode_menu)
        with mock.patch(
            "ui_main.components.worldbook_conversation_control.QMessageBox.warning"
        ) as warning:
            episode_menu.actions()[2].trigger()

        self.assertEqual(self.chat.meta.worldbook.episode, 3)
        warning.assert_called_once()
        self.manager.save.assert_called_once_with()

    def test_first_disclosure_can_disable_diagnostic_persistence(self) -> None:
        """首次启用世界书时应告知诊断内容并允许立即关闭落盘。"""

        self.chat.meta.worldbook.root_package_id = _root_option().package_id
        self.chat.meta.worldbook.episode = 5
        self.control.bind(self.chat, self.character)
        self.config.worldbook_diagnostics_disclosure_seen.value = False
        message_box_class = mock.Mock()
        message_box_class.Information = 1
        message_box_class.No = 2
        message_box_class.Yes = 4
        box = message_box_class.return_value
        box.exec_.return_value = message_box_class.No
        box.button.return_value = mock.Mock()
        with mock.patch(
            "ui_main.components.worldbook_conversation_control.QMessageBox",
            message_box_class,
        ):
            self.control.button.click()

        self.assertEqual(
            self.config.set_calls,
            [
                (self.config.worldbook_diagnostics_disclosure_seen, True),
                (self.config.worldbook_diagnostics_persistence, False),
            ],
        )

    def test_incomplete_main_click_opens_configuration_without_enabling(self) -> None:
        """主区启用缺少必要配置时应打开菜单，并保持关闭状态。"""

        with mock.patch.object(self.control.button, "showMenu") as show_menu:
            self.control.button.click()

        self.assertFalse(self.chat.meta.worldbook.enabled)
        self.assertFalse(self.control.button.isChecked())
        show_menu.assert_called_once_with()
        summary = self.control.button.menu().actions()[0].defaultWidget()
        self.assertIn("启用前请完成", summary.text())

    def test_complete_main_click_toggles_and_saves(self) -> None:
        """配置完整时主区应直接切换世界书，并保存一次。"""

        self.chat.meta.worldbook.root_package_id = _root_option().package_id
        self.chat.meta.worldbook.episode = 5
        self.control.bind(self.chat, self.character)

        self.control.button.click()

        self.assertTrue(self.chat.meta.worldbook.enabled)
        self.assertTrue(self.control.button.isChecked())
        self.manager.save.assert_called_once_with()

    def test_main_enable_intent_auto_enables_after_configuration(self) -> None:
        """从主区发起配置并在同次操作补齐要求后应自动启用。"""

        with mock.patch.object(self.control.button, "showMenu"):
            self.control.button.click()
        root_menu = _menu_action(
            self.control.button.menu(),
            "世界书包",
        ).menu()
        self.assertIsNotNone(root_menu)

        root_menu.actions()[0].trigger()

        self.assertTrue(self.chat.meta.worldbook.enabled)
        self.assertTrue(self.control.button.isChecked())
        self.assertEqual(self.chat.meta.worldbook.episode, 13)
        self.manager.save.assert_called_once_with()

    def test_save_failure_keeps_state_and_emits_plain_status(self) -> None:
        """保存失败时应保留内存状态，并发出无重试入口的简短状态。"""

        self.chat.meta.worldbook.root_package_id = _root_option().package_id
        self.chat.meta.worldbook.episode = 5
        self.control.bind(self.chat, self.character)
        self.manager.save.side_effect = OSError("disk unavailable")
        statuses: list[str] = []
        self.control.status_changed.connect(statuses.append)

        self.control.button.click()

        self.assertTrue(self.chat.meta.worldbook.enabled)
        self.assertEqual(statuses, ["世界书设置保存失败"])

    def test_button_has_stable_accessible_name_and_state_description(self) -> None:
        """动态状态不应改变读屏名称，并应通过描述暴露状态。"""

        self.assertEqual(self.control.button.accessibleName(), "世界书")
        self.assertIn("已关闭", self.control.button.accessibleDescription())

        self.chat.meta.worldbook.root_package_id = _root_option().package_id
        self.chat.meta.worldbook.episode = 5
        self.chat.meta.worldbook.enabled = True
        self.control.bind(self.chat, self.character)

        self.assertEqual(self.control.button.accessibleName(), "世界书")
        self.assertIn("已启用", self.control.button.accessibleDescription())

    def test_existing_character_mapping_warns_before_confirmed_change(self) -> None:
        """修改已有映射时应前置说明风险并要求二次确认。"""

        self.character.character_folder_name = "custom-anon"
        self.config.worldbook_character_mappings.value = {
            "custom-anon": CharacterId.ANON.value
        }
        self.control.bind(self.chat, self.character)
        with (
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QInputDialog.getItem",
                return_value=("素世（soyo）", True),
            ) as get_item,
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ) as question,
        ):
            _menu_action(
                self.control.button.menu(),
                "角色知识视角：爱音",
            ).trigger()

        self.assertIn("请勿随意修改", get_item.call_args.args[2])
        question.assert_called_once()
        self.assertIn("爱音", question.call_args.args[2])
        self.assertIn("素世", question.call_args.args[2])
        self.assertIn("旧对话", question.call_args.args[2])
        self.assertEqual(
            self.config.worldbook_character_mappings.value,
            {"custom-anon": CharacterId.SOYO.value},
        )

    def test_existing_character_mapping_change_can_be_cancelled(self) -> None:
        """拒绝二次确认时必须保留原角色知识映射。"""

        self.character.character_folder_name = "custom-anon"
        original_mapping = {"custom-anon": CharacterId.ANON.value}
        self.config.worldbook_character_mappings.value = dict(original_mapping)
        self.control.bind(self.chat, self.character)
        with (
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QInputDialog.getItem",
                return_value=("素世（soyo）", True),
            ),
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QMessageBox.question",
                return_value=QMessageBox.Cancel,
            ),
        ):
            _menu_action(
                self.control.button.menu(),
                "角色知识视角：爱音",
            ).trigger()

        self.assertEqual(
            self.config.worldbook_character_mappings.value,
            original_mapping,
        )
        self.assertEqual(self.config.set_calls, [])

    def test_export_uses_bound_chat_id(self) -> None:
        """诊断导出应始终使用当前绑定对话的稳定身份。"""

        self.store.export_chat.return_value = 2
        output_path = Path("/tmp/worldbook-ui-test.zip")
        with (
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QFileDialog.getSaveFileName",
                return_value=(str(output_path), "ZIP 压缩包 (*.zip)"),
            ),
            mock.patch(
                "ui_main.components.worldbook_conversation_control.QMessageBox.information"
            ),
        ):
            _menu_action(
                self.control.button.menu(),
                "导出此对话的世界书诊断…",
            ).trigger()

        self.store.export_chat.assert_called_once_with(self.chat.chat_id, output_path)

    def test_diagnostic_forwarding_requires_visibility_and_bound_chat(self) -> None:
        """诊断记录只应在可见且属于当前绑定对话时通过信号转发。"""

        receiver = mock.Mock()
        self.control.diagnostic_ready.connect(receiver)
        record = {"tool_calls": []}

        self.control.accept_diagnostic(self.chat.chat_id, record)
        receiver.assert_not_called()

        with mock.patch.dict(
            os.environ,
            {WORLDBOOK_DIAGNOSTICS_UI_ENV: "1"},
        ):
            self.control.bind(self.chat, self.character)
            _menu_action(
                self.control.button.menu(),
                "显示本次运行的诊断",
            ).trigger()

        self.control.accept_diagnostic("another-chat", record)
        receiver.assert_not_called()
        self.control.accept_diagnostic(self.chat.chat_id, record)
        receiver.assert_called_once_with(record)

    def test_regeneration_reuses_frozen_snapshot_without_catalog_lookup(self) -> None:
        """重新生成已有快照的回合时不应重新解析当前世界书设置。"""

        snapshot = _snapshot()
        self.chat.message_list.append(
            Message(
                "User",
                "你好",
                "",
                EmotionEnum.HAPPINESS,
                "",
                worldbook_snapshot=snapshot,
            )
        )

        frozen = self.control.freeze_turn_snapshot(append_user_message=False)

        self.assertEqual(frozen, snapshot.model_dump(mode="json"))
        self.catalog.resolve.assert_not_called()
        self.manager.save.assert_not_called()

    def test_regeneration_backfills_legacy_message_snapshot_and_saves(self) -> None:
        """旧用户消息没有快照时应按当前设置补写并立即保存。"""

        snapshot = _snapshot()
        self.catalog.resolve.return_value = snapshot
        settings = self.chat.meta.worldbook
        settings.enabled = True
        settings.root_package_id = _root_option().package_id
        settings.episode = snapshot.episode
        self.chat.message_list.append(
            Message("User", "你好", "", EmotionEnum.HAPPINESS, "")
        )

        frozen = self.control.freeze_turn_snapshot(append_user_message=False)

        self.assertEqual(frozen, snapshot.model_dump(mode="json"))
        self.assertEqual(self.chat.message_list[-1].worldbook_snapshot, snapshot)
        self.manager.save.assert_called_once_with()


class WorldbookDiagnosticsSettingUiTest(unittest.TestCase):
    """验证诊断持久化开关位于个性化设置区域。"""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        """复用或创建 Qt 应用。"""

        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def test_personalization_contains_worldbook_diagnostics_switch(self) -> None:
        """个性化页应暴露全局世界书诊断持久化开关。"""

        area = CustomSettingArea()
        try:
            self.assertIs(
                area.worldbook_diagnostics_card.configItem,
                d_sakiko_config.worldbook_diagnostics_persistence,
            )
        finally:
            area.close()
            area.deleteLater()


if __name__ == "__main__":
    unittest.main()
