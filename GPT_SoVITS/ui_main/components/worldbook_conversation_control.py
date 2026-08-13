"""封装主对话窗口中的世界书选择、诊断和回合快照行为。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Protocol

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from character import CharacterAttributes
from chat.chat import Chat, ChatManager, ChatType
from log import get_logger
from rag.models import CharacterId
from rag.worldbook.runtime.catalog import WorldbookRootCatalog
from rag.worldbook.runtime.conversation import (
    freeze_worldbook_snapshot,
    normalize_character_knowledge_mappings,
)
from rag.worldbook.runtime.diagnostics import WorldbookDiagnosticStore
from rag.worldbook.runtime.models import WorldbookRootOption
from ui_main.components.input_option_chips import SplitToggleChip
from ui_main.theme import ThemePalette


WORLDBOOK_DIAGNOSTICS_UI_ENV = "D_SAKIKO_WORLDBOOK_DIAGNOSTICS_UI"
WORLDBOOK_EPISODE_COUNT = 13

logger = get_logger(__name__)


class _ConfigValue(Protocol):
    """描述世界书控制模块所需的配置值读取接口。"""

    @property
    def value(self) -> object:
        """返回当前配置值。"""


class _WorldbookConfig(Protocol):
    """描述世界书控制模块使用的最小配置接口。"""

    worldbook_character_mappings: _ConfigValue
    worldbook_diagnostics_persistence: _ConfigValue
    worldbook_diagnostics_disclosure_seen: _ConfigValue

    def set(self, item: object, value: object) -> None:
        """写入一个配置项。"""


def _is_diagnostics_ui_enabled() -> bool:
    """判断当前运行环境是否允许显示世界书运行期诊断入口。"""

    value = os.getenv(WORLDBOOK_DIAGNOSTICS_UI_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class WorldbookConversationControl(QObject):
    """集中管理单角色对话的世界书按钮、设置、诊断和回合快照。"""

    diagnostic_ready = pyqtSignal(dict)
    status_changed = pyqtSignal(str)

    def __init__(
        self,
        *,
        chat_manager: ChatManager,
        config: _WorldbookConfig,
        catalog: WorldbookRootCatalog,
        diagnostic_store: WorldbookDiagnosticStore | None,
        button_height: int,
        parent: QWidget,
    ) -> None:
        """创建尚未绑定对话的世界书控制模块。"""

        super().__init__(parent)
        self._chat_manager = chat_manager
        self._config = config
        self._catalog = catalog
        self._diagnostic_store = diagnostic_store
        self._dialog_parent = parent
        self._chat: Chat | None = None
        self._character: CharacterAttributes | None = None
        self._diagnostics_visible = False
        self._pending_enable_from_main = False

        self._button = SplitToggleChip(
            "世界书",
            accessible_name="世界书",
            height=button_height,
            parent=parent,
        )
        self._button.setObjectName("worldbookMenuButton")
        self._button.setToolTip("设置当前对话的世界书、剧情进度和角色知识视角")
        self._button.setEnabled(False)
        self._button.clicked.connect(self._on_main_button_clicked)  # noqa

    @property
    def button(self) -> SplitToggleChip:
        """返回可放入主窗口输入栏的世界书按钮。"""

        return self._button

    def bind(self, chat: Chat, character: CharacterAttributes) -> None:
        """绑定当前对话和应用角色，并刷新全部可见状态。"""

        self._chat = chat
        self._character = character
        self._pending_enable_from_main = False
        supported = chat.type == ChatType.SINGLE_CHARACTER
        self._button.setEnabled(supported)
        self._button.setVisible(supported)
        self._refresh()

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """根据主窗口语义色板刷新世界书分段按钮。"""

        self._button.set_theme_palette(palette)

    def freeze_turn_snapshot(
        self,
        *,
        append_user_message: bool,
    ) -> dict[str, object] | None:
        """冻结发送回合的世界书上下文，重新生成时优先沿用旧快照。"""

        chat = self._chat
        character = self._character
        if (
            chat is None
            or character is None
            or chat.type != ChatType.SINGLE_CHARACTER
        ):
            return None
        if not append_user_message:
            message_index = chat.find_last_real_user_message_index()
            if message_index is not None:
                existing = chat.message_list[message_index].worldbook_snapshot
                if existing is not None:
                    return existing.model_dump(mode="json")

        settings = chat.meta.worldbook
        mappings = normalize_character_knowledge_mappings(
            self._config.worldbook_character_mappings.value
        )
        resolution = freeze_worldbook_snapshot(
            self._catalog,
            enabled=settings.enabled,
            root_package_id=settings.root_package_id,
            episode=settings.episode,
            character_folder_name=character.character_folder_name,
            mappings=mappings,
        )
        snapshot = resolution.snapshot
        if snapshot is None:
            return None
        if not append_user_message:
            message_index = chat.find_last_real_user_message_index()
            if message_index is not None:
                chat.message_list[message_index].worldbook_snapshot = snapshot
                try:
                    self._chat_manager.save()
                except Exception:
                    logger.exception("补写旧用户消息的世界书快照失败")
        return snapshot.model_dump(mode="json")

    def accept_diagnostic(
        self,
        chat_id: str,
        record: dict[str, object],
    ) -> None:
        """在诊断可见且属于当前对话时转发一条诊断记录。"""

        if (
            self._diagnostics_visible
            and self._chat is not None
            and chat_id == self._chat.chat_id
        ):
            self.diagnostic_ready.emit(dict(record))

    def _root_options(self) -> list[WorldbookRootOption]:
        """读取所有季度根包；失败时记录日志并安全返回空列表。"""

        try:
            return self._catalog.list_roots()
        except (OSError, TypeError, ValueError):
            logger.exception("读取世界书根包列表失败")
            return []

    def _configuration_issues(self) -> list[str]:
        """返回阻止世界书启用的必要配置缺失项。"""

        if self._chat is None:
            return ["尚未绑定对话"]
        settings = self._chat.meta.worldbook
        option = self._current_root_option()
        issues: list[str] = []
        if option is None or not option.enabled:
            issues.append("选择可用的世界书包")
        if settings.episode is None:
            issues.append("选择剧情进度")
        character_id = self._character_id()
        if character_id is None:
            issues.append("设置角色知识视角")
        elif option is not None and character_id not in option.available_characters:
            issues.append("选择该世界书包含的角色知识视角")
        return issues

    def _has_valid_root_selection(self) -> bool:
        """判断当前对话是否已经选择可用的世界书根包。"""

        option = self._current_root_option()
        return option is not None and option.enabled

    def _menu_summary_text(self) -> str:
        """生成菜单顶部的当前配置或待设置说明。"""

        issues = self._configuration_issues()
        if issues:
            return "启用前请完成：" + "、".join(issues)
        if self._chat is None:
            return "尚未绑定对话"
        option = self._current_root_option()
        episode = self._chat.meta.worldbook.episode
        character_id = self._character_id()
        if option is None or episode is None or character_id is None:
            return "世界书配置尚未完成"
        return (
            f"当前：{option.display_name} · 第 {episode} 集结束后 · "
            f"{character_id.common_name}视角"
        )

    def _on_main_button_clicked(self, checked: bool) -> None:
        """处理分段按钮主区切换，缺配置时改为打开设置菜单。"""

        if self._chat is None:
            self._button.setChecked(False)
            return
        if not checked:
            self._pending_enable_from_main = False
            self._set_enabled(False)
            return
        if self._configuration_issues():
            self._chat.meta.worldbook.enabled = False
            self._button.setChecked(False)
            self._pending_enable_from_main = True
            self._refresh()
            self._button.showMenu()
            return
        self._set_enabled(True)

    def _try_finish_pending_enable(self) -> bool:
        """在本次配置操作已满足要求时自动完成主区启用意图。"""

        if (
            not self._pending_enable_from_main
            or self._chat is None
            or self._configuration_issues()
        ):
            return False
        self._pending_enable_from_main = False
        self._chat.meta.worldbook.enabled = True
        self._disclose_diagnostics_if_needed()
        self._warn_if_character_missing()
        return True

    def _cancel_pending_enable(self) -> None:
        """菜单关闭且配置仍不完整时取消本次临时启用意图。"""

        if self._configuration_issues():
            self._pending_enable_from_main = False

    def _schedule_cancel_pending_enable(self) -> None:
        """把取消操作延后到菜单项的 triggered 信号处理完毕之后。"""

        QTimer.singleShot(0, self._cancel_pending_enable)

    def _current_root_option(self) -> WorldbookRootOption | None:
        """返回当前绑定对话选择的世界书根包。"""

        if self._chat is None:
            return None
        package_id = self._chat.meta.worldbook.root_package_id
        return next(
            (
                option
                for option in self._root_options()
                if option.package_id == package_id
            ),
            None,
        )

    def _character_id(self) -> CharacterId | None:
        """解析绑定应用角色的全局世界书知识身份。"""

        if self._character is None:
            return None
        folder_name = self._character.character_folder_name
        mappings = normalize_character_knowledge_mappings(
            self._config.worldbook_character_mappings.value
        )
        mapped = mappings.get(folder_name)
        if mapped is not None:
            return mapped
        try:
            return CharacterId(folder_name)
        except ValueError:
            return None

    def _build_menu(self) -> QMenu:
        """根据绑定对话的最新状态创建世界书菜单。"""

        menu = QMenu(self._button)
        chat = self._chat
        if chat is None:
            unavailable_action = menu.addAction("尚未绑定对话")
            unavailable_action.setEnabled(False)
            return menu
        settings = chat.meta.worldbook

        if self._pending_enable_from_main and not self._has_valid_root_selection():
            return self._build_initial_package_menu(menu)

        summary = QLabel(self._menu_summary_text(), menu)
        summary.setWordWrap(True)
        summary.setContentsMargins(10, 7, 10, 7)
        summary.setMinimumWidth(280)
        summary.setAccessibleName("世界书配置状态")
        summary_action = QWidgetAction(menu)
        summary_action.setDefaultWidget(summary)
        menu.addAction(summary_action)
        menu.addSeparator()

        diagnostics_ui_enabled = _is_diagnostics_ui_enabled()
        if diagnostics_ui_enabled:
            diagnostics_action = QAction("显示本次运行的诊断", menu)
            diagnostics_action.setCheckable(True)
            diagnostics_action.setChecked(self._diagnostics_visible)
            diagnostics_action.setToolTip("仅控制诊断内容是否可查看，不改变检索和回复流程")
            diagnostics_action.triggered.connect(self._set_diagnostics_visible)  # noqa
            menu.addAction(diagnostics_action)
        menu.addSeparator()

        root_menu = menu.addMenu("世界书包")
        root_options = self._root_options()
        for option in root_options:
            action = QAction(option.display_name, root_menu)
            action.setCheckable(True)
            action.setChecked(option.package_id == settings.root_package_id)
            action.setEnabled(option.enabled)
            if option.unavailable_reasons:
                reason = "\n".join(option.unavailable_reasons)
                action.setToolTip(reason)
                action.setStatusTip(reason)
            action.triggered.connect(
                lambda checked=False, package_id=option.package_id: self._set_root(
                    package_id
                )
            )  # noqa
            root_menu.addAction(action)
        if not root_options:
            unavailable_action = root_menu.addAction("没有可用的季度世界书包")
            unavailable_action.setEnabled(False)

        episode_menu = menu.addMenu("剧情进度")
        for episode in range(1, WORLDBOOK_EPISODE_COUNT + 1):
            action = QAction(f"第 {episode} 集结束后", episode_menu)
            action.setCheckable(True)
            action.setChecked(settings.episode == episode)
            action.triggered.connect(
                lambda checked=False, value=episode: self._set_episode(value)
            )  # noqa
            episode_menu.addAction(action)

        character_id = self._character_id()
        mapping_text = (
            f"角色知识视角：{character_id.common_name}"
            if character_id is not None
            else "角色知识视角：尚未映射…"
        )
        mapping_action = menu.addAction(mapping_text)
        mapping_action.triggered.connect(
            lambda checked=False: self._choose_character_mapping()
        )  # noqa

        if diagnostics_ui_enabled and self._diagnostics_visible:
            recent_action = menu.addAction("查看最近诊断…")
            recent_action.triggered.connect(self._show_recent_diagnostics)  # noqa

        export_action = menu.addAction("导出此对话的世界书诊断…")
        export_action.triggered.connect(self._export_current_chat_diagnostics)  # noqa
        menu.aboutToHide.connect(self._schedule_cancel_pending_enable)  # noqa
        return menu

    def _build_initial_package_menu(self, menu: QMenu) -> QMenu:
        """构建首次启用时只要求选择世界书包的一级菜单。"""

        prompt = QLabel("请选择世界书包", menu)
        prompt.setContentsMargins(10, 7, 10, 7)
        prompt.setMinimumWidth(280)
        prompt.setAccessibleName("首次启用世界书")
        prompt_action = QWidgetAction(menu)
        prompt_action.setDefaultWidget(prompt)
        menu.addAction(prompt_action)
        menu.addSeparator()

        package_group = QActionGroup(menu)
        package_group.setExclusive(True)
        root_options = self._root_options()
        for option in root_options:
            action = QAction(option.display_name, menu)
            action.setCheckable(True)
            action.setEnabled(option.enabled)
            if option.unavailable_reasons:
                reason = "\n".join(option.unavailable_reasons)
                action.setToolTip(reason)
                action.setStatusTip(reason)
            action.triggered.connect(
                lambda checked=False, package_id=option.package_id: self._set_root(
                    package_id
                )
            )  # noqa
            package_group.addAction(action)
            menu.addAction(action)
        if not root_options:
            unavailable_action = menu.addAction("没有可用的季度世界书包")
            unavailable_action.setEnabled(False)

        menu.aboutToHide.connect(self._schedule_cancel_pending_enable)  # noqa
        return menu

    def _set_enabled(self, enabled: bool) -> None:
        """修改世界书开关，并在首次开启时完成必要告知和映射。"""

        if self._chat is None:
            return
        if enabled and self._configuration_issues():
            self._chat.meta.worldbook.enabled = False
            self._refresh()
            return
        self._chat.meta.worldbook.enabled = bool(enabled)
        if enabled:
            self._disclose_diagnostics_if_needed()
            if self._character_id() is None:
                self._choose_character_mapping()
            self._warn_if_character_missing()
        self._save_and_refresh()

    def _disclose_diagnostics_if_needed(self) -> None:
        """首次启用时说明短期诊断内容，并允许立即关闭落盘。"""

        if bool(self._config.worldbook_diagnostics_disclosure_seen.value):
            return
        self._config.set(
            self._config.worldbook_diagnostics_disclosure_seen,
            True,
        )
        box = QMessageBox(
            QMessageBox.Information,
            "世界书诊断记录",
            "为便于排查世界书的效果，程序会短期保存对话中的你发送的消息、"
            "模型回复和世界书查询得到的结果。\n\n"
            "记录不会自动上传到任何服务器。\n"
            "你可以在“个性化”设置中随时关闭记录，或者现在关闭。",
            QMessageBox.No | QMessageBox.Yes,
            self._dialog_parent,
        )
        box.button(QMessageBox.Yes).setText("保存诊断信息")
        box.button(QMessageBox.No).setText("不要保存")
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() == QMessageBox.No:
            self._config.set(
                self._config.worldbook_diagnostics_persistence,
                False,
            )

    def _set_diagnostics_visible(self, visible: bool) -> None:
        """只修改本次进程的世界书诊断可见性。"""

        self._diagnostics_visible = bool(visible)
        self._refresh()

    def _set_root(self, package_id: str) -> None:
        """选择世界书根包，并为未设置的剧情进度提供默认值。"""

        if self._chat is None:
            return
        option = next(
            (
                item
                for item in self._root_options()
                if item.package_id == package_id and item.enabled
            ),
            None,
        )
        if option is None:
            QMessageBox.warning(
                self._dialog_parent,
                "世界书不可用",
                "所选世界书包当前不可用。",
            )
            return
        settings = self._chat.meta.worldbook
        settings.root_package_id = package_id
        episode_defaulted = settings.episode is None
        if episode_defaulted:
            settings.episode = WORLDBOOK_EPISODE_COUNT
        if self._character_id() is None:
            self._choose_character_mapping(finish_pending=False)
        self._warn_if_character_missing(option)
        enabled_from_initial_menu = self._try_finish_pending_enable()
        success_message = (
            "已启用世界书，剧情进度默认为第 13 集，可在世界书菜单中修改"
            if enabled_from_initial_menu and episode_defaulted
            else "已更新世界书设置"
        )
        self._save_and_refresh(success_message)

    def _set_episode(self, episode: int) -> None:
        """修改剧情进度，回退时提醒历史对话可能泄露剧情。"""

        if self._chat is None or not 1 <= episode <= WORLDBOOK_EPISODE_COUNT:
            return
        previous = self._chat.meta.worldbook.episode
        if previous is not None and episode < previous:
            QMessageBox.warning(
                self._dialog_parent,
                "剧情进度已回退",
                "已成功回退剧情进度，不过，对话中可能已经包含之后剧情的信息。",
            )
        self._chat.meta.worldbook.episode = episode
        self._try_finish_pending_enable()
        self._save_and_refresh()

    def _choose_character_mapping(self, *, finish_pending: bool = True) -> bool:
        """选择全局角色知识映射，修改已有映射前要求二次确认。"""

        if self._character is None:
            return False
        root_option = self._current_root_option()
        choices = (
            list(root_option.available_characters)
            if root_option is not None and root_option.available_characters
            else list(CharacterId)
        )
        labels = [
            f"{character_id.common_name}（{character_id.value}）"
            for character_id in choices
        ]
        existing = self._character_id()
        current_index = choices.index(existing) if existing in choices else 0
        if existing is None:
            mapping_prompt = (
                f"“{self._character.character_name}”在世界书中对应哪位角色？\n\n"
                "此对应关系将用于该角色的所有对话。"
            )
        else:
            mapping_prompt = (
                f"“{self._character.character_name}”当前使用"
                f"“{existing.common_name}”的知识视角。\n"
                "此关系会影响该角色的所有对话，请勿随意修改。\n\n"
                "请选择新的知识视角："
            )
        selected_label, accepted = QInputDialog.getItem(
            self._dialog_parent,
            "角色知识视角",
            mapping_prompt,
            labels,
            current_index,
            False,
        )
        if not accepted:
            return False
        selected = choices[labels.index(selected_label)]
        if existing is not None and existing != selected:
            confirmation = QMessageBox.question(
                self._dialog_parent,
                "确认修改角色知识视角",
                f"确定把“{self._character.character_name}”的知识视角"
                f"从“{existing.common_name}”改为“{selected.common_name}”吗？"
                f"这会让模型在“{self._character.character_name}”的对话中"
                f"都得知“{selected.common_name}”的知识\n\n"
                "模型在旧对话中可能会混淆修改前后的角色身份。请勿随意修改。",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if confirmation != QMessageBox.Yes:
                return False
        if existing == selected:
            return True
        raw_mappings = self._config.worldbook_character_mappings.value
        mappings = dict(raw_mappings) if isinstance(raw_mappings, dict) else {}
        mappings[self._character.character_folder_name] = selected.value
        self._config.set(self._config.worldbook_character_mappings, mappings)
        self._warn_if_character_missing(root_option)
        if finish_pending and self._try_finish_pending_enable():
            self._save_and_refresh()
        else:
            self._refresh()
        return True

    def _warn_if_character_missing(
        self,
        root_option: WorldbookRootOption | None = None,
    ) -> None:
        """若根包没有当前知识角色，只提示用户检查世界书包。"""

        option = root_option or self._current_root_option()
        character_id = self._character_id()
        if (
            option is None
            or character_id is None
            or character_id in option.available_characters
        ):
            return
        QMessageBox.warning(
            self._dialog_parent,
            "世界书中没有此角色",
            f"“{option.display_name}”中没有找到“{character_id.common_name}”的数据，"
            "请确认是否选错了世界书包。",
        )

    def _show_recent_diagnostics(self) -> None:
        """显示本次程序运行中最近十个世界书诊断回合。"""

        if self._diagnostic_store is None:
            QMessageBox.information(
                self._dialog_parent,
                "世界书诊断",
                "当前没有可用的诊断记录。",
            )
            return
        records = self._diagnostic_store.recent(10)
        dialog = QDialog(self._dialog_parent)
        dialog.setWindowTitle("最近世界书诊断")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(
            "\n\n".join(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
                for record in records
            )
            or "本次运行中尚无世界书诊断记录。"
        )
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)  # noqa
        layout.addWidget(buttons)
        dialog.exec_()

    def _export_current_chat_diagnostics(self) -> None:
        """导出当前绑定对话的内存和滚动世界书诊断。"""

        if self._diagnostic_store is None or self._chat is None:
            QMessageBox.warning(
                self._dialog_parent,
                "导出失败",
                "世界书诊断存储尚未初始化。",
            )
            return
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", self._chat.name).strip() or "对话"
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self._dialog_parent,
            "导出此对话的世界书诊断",
            f"{safe_name}-世界书诊断.zip",
            "ZIP 压缩包 (*.zip)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".zip"):
            output_path += ".zip"
        try:
            count = self._diagnostic_store.export_chat(
                self._chat.chat_id,
                Path(output_path),
            )
        except (OSError, ValueError):
            logger.exception("导出世界书诊断失败")
            QMessageBox.warning(
                self._dialog_parent,
                "导出失败",
                "无法写入世界书诊断压缩包。",
            )
            return
        QMessageBox.information(
            self._dialog_parent,
            "导出完成",
            f"已导出当前对话的 {count} 条世界书诊断记录。",
        )

    def _save_and_refresh(
        self,
        success_message: str = "已更新世界书设置",
    ) -> None:
        """保存世界书配置并刷新按钮；保存失败时保持内存状态。"""

        try:
            self._chat_manager.save()
            self.status_changed.emit(success_message)
        except Exception:
            logger.exception("保存世界书设置失败")
            self.status_changed.emit("世界书设置保存失败")
        self._refresh()

    def _refresh(self) -> None:
        """刷新按钮摘要、提示和菜单。"""

        chat = self._chat
        if chat is None:
            self._button.setChecked(False)
            self._button.setText("世界书")
            self._button.setAccessibleDescription("尚未绑定对话")
            self._button.setMenu(self._build_menu())
            return
        settings = chat.meta.worldbook
        self._button.setChecked(settings.enabled)
        self._button.setText("世界书")
        self._button.setMenu(self._build_menu())
        summary = self._menu_summary_text()
        state = "已启用" if settings.enabled else "已关闭"
        self._button.setAccessibleDescription(f"{state}。{summary}")
        self._button.setToolTip(
            f"世界书已启用；{summary}"
            if settings.enabled
            else f"世界书已关闭；{summary}"
        )
