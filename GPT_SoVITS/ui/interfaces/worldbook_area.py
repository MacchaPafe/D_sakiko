"""世界书依赖闭包浏览与用户 Override 编辑页面。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from PyQt5.QtCore import QSettings, QSignalBlocker, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidgetItem,
    QShortcut,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBadge,
    InfoBar,
    InfoBarPosition,
    ListWidget,
    MessageBox,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    SubtitleLabel,
    TitleLabel,
    TransparentDropDownPushButton,
)

from rag.models import CharacterId
from rag.worldbook.adapters import AdapterRegistry, create_default_registry
from rag.worldbook.editing import (
    PackageEntryRecord,
    RetrievalSummaryReviewRequired,
    WorldbookEditService,
    WorldbookReferenceError,
    WorldbookSequenceConflict,
    build_package_entry_records,
)
from rag.worldbook.models import EntryType, PackageLoadResult, PackageReadiness, WorldbookEntry
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.paths import WorldbookPaths
from rag.worldbook.time_coordinates import OPEN_ENDED_TIME, decode_story_time
from rag.worldbook.user_state import WorldbookUserStateRepository
from ui.components.worldbook_editor import EntryEditorHost
from ui.controllers.worldbook_sync_controller import WorldbookSyncController


_ENTRY_TYPE_LABELS: tuple[tuple[EntryType, str], ...] = (
    ("story_event", "剧情事件"),
    ("character_thought", "角色想法"),
    ("character_relation", "角色关系"),
    ("lore_entry", "名词解释"),
)


class UnsavedChangesBox(MessageBoxBase):
    """提供保存、放弃和取消三种未保存处理结果。"""

    DISCARD_RESULT = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建未保存修改确认对话框。"""

        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(self.tr("存在未保存的修改"), self))
        self.viewLayout.addWidget(BodyLabel(self.tr("离开前要如何处理当前条目的修改？"), self))
        self.yesButton.setText(self.tr("保存并继续"))
        self.cancelButton.setText(self.tr("取消"))
        self.discard_button = PushButton(self.tr("放弃修改"), self.buttonGroup)
        self.discard_button.setAttribute(Qt.WA_LayoutUsesWidgetRect)
        self.discard_button.clicked.connect(lambda: self.done(self.DISCARD_RESULT))
        self.buttonLayout.insertWidget(1, self.discard_button, 1, Qt.AlignVCenter)


class EntryListRow(QWidget):
    """在 Fluent 列表项中显示主标题与一行辅助信息。"""

    def __init__(
        self,
        title: str,
        secondary: str,
        muted: bool,
        warning: bool,
        parent: QWidget | None = None,
    ) -> None:
        """创建透明、不可交互的两行列表内容。"""

        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(1)
        self.title_label = BodyLabel(title, self)
        self.secondary_label = CaptionLabel(secondary, self)
        if muted:
            self.title_label.setEnabled(False)
            self.secondary_label.setEnabled(False)
        if warning:
            self.title_label.setStyleSheet("color: rgb(196, 43, 28);")
        layout.addWidget(self.title_label)
        layout.addWidget(self.secondary_label)


class WorldbookArea(QWidget):
    """浏览根世界书依赖闭包并编辑官方条目的用户替换版本。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        app_root: Path | None = None,
        settings: QSettings | None = None,
    ) -> None:
        """创建不在 UI 进程中加载 Qdrant 或 embedding 模型的编辑器。"""

        super().__init__(parent)
        self.setObjectName("WorldbookArea")
        resolved_root = app_root or Path(__file__).resolve().parents[3]
        self._paths = WorldbookPaths(resolved_root)
        self._loader = WorldbookPackageLoader(self._paths.official_packages)
        self._states = WorldbookUserStateRepository(self._paths.user_state)
        self._registry: AdapterRegistry = create_default_registry()
        self._edit_service = WorldbookEditService(self._states, self._registry)
        self._packages: dict[str, PackageLoadResult] = {}
        self._records: dict[UUID, PackageEntryRecord] = {}
        self._available_entries: list[WorldbookEntry] = []
        self._root_package_id: str | None = None
        self._selected_entry_type: EntryType = "story_event"
        self._current_record: PackageEntryRecord | None = None
        self._session_selection: dict[tuple[str, EntryType], UUID] = {}
        self._loading_ui = False
        self._settings = settings or QSettings("D_sakiko", "WorldbookEditor")
        self._controller = WorldbookSyncController(resolved_root, self)
        self._build_ui()
        self._connect_signals()
        self.reload_packages()

    def _build_ui(self) -> None:
        """构建顶部世界书选择、左侧导航和右侧类型化详情。"""

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(14)
        root_layout.addWidget(SubtitleLabel(self.tr("世界书管理"), self))

        package_layout = QHBoxLayout()
        self.package_combo = ComboBox(self)
        self.package_combo.setMinimumWidth(360)
        self.package_combo.setPlaceholderText(self.tr("尚未安装可用的世界书"))
        package_layout.addWidget(self.package_combo, 1)
        self.package_info_label = CaptionLabel("", self)
        package_layout.addWidget(self.package_info_label)
        root_layout.addLayout(package_layout)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.left_panel = QWidget(self.splitter)
        self.left_panel.setMinimumWidth(240)
        self.left_panel.setMaximumWidth(460)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)
        self.type_combo = ComboBox(self.left_panel)
        for entry_type, label in _ENTRY_TYPE_LABELS:
            self.type_combo.addItem(label, userData=entry_type)
        left_layout.addWidget(self.type_combo)
        self.search_edit = SearchLineEdit(self.left_panel)
        self.search_edit.setPlaceholderText(self.tr("搜索当前类型的条目"))
        left_layout.addWidget(self.search_edit)
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        self.modified_only_check = CheckBox(self.tr("只看已修改"), self.left_panel)
        self.show_hidden_check = CheckBox(self.tr("显示隐藏内容"), self.left_panel)
        filter_layout.addWidget(self.modified_only_check)
        filter_layout.addWidget(self.show_hidden_check)
        left_layout.addLayout(filter_layout)
        self.entry_list = ListWidget(self.left_panel)
        left_layout.addWidget(self.entry_list, 1)

        self.right_panel = QWidget(self.splitter)
        self.right_panel.setMinimumWidth(0)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)
        header_layout = QHBoxLayout()
        self.detail_title = TitleLabel(self.tr("请选择条目"), self.right_panel)
        self.detail_title.setMinimumWidth(0)
        self.detail_title.setWordWrap(True)
        self.detail_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.modified_badge = InfoBadge.success(self.tr("该条目已修改"), self.right_panel)
        self.modified_badge.hide()
        self.owner_label = CaptionLabel("", self.right_panel)
        header_layout.addWidget(self.detail_title)
        header_layout.addWidget(self.modified_badge)
        right_layout.addLayout(header_layout)
        right_layout.addWidget(self.owner_label)
        self.problem_label = CaptionLabel("", self.right_panel)
        self.problem_label.setWordWrap(True)
        self.problem_label.hide()
        right_layout.addWidget(self.problem_label)

        self.detail_stack = QStackedWidget(self.right_panel)
        self.empty_page = QWidget(self.detail_stack)
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.addStretch(1)
        self.empty_title = SubtitleLabel(self.tr("请从左侧选择一个条目"), self.empty_page)
        self.empty_message = BodyLabel("", self.empty_page)
        self.empty_message.setWordWrap(True)
        empty_layout.addWidget(self.empty_title, 0, Qt.AlignCenter)
        empty_layout.addWidget(self.empty_message, 0, Qt.AlignCenter)
        empty_layout.addStretch(1)
        self.editor_page = QWidget(self.detail_stack)
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.source_segment = SegmentedWidget(self.editor_page)
        self.source_segment.addItem("modified", self.tr("修改后内容"))
        self.source_segment.addItem("official", self.tr("官方内容"))
        editor_layout.addWidget(self.source_segment, 0, Qt.AlignLeft)
        self.source_stack = QStackedWidget(self.editor_page)
        self.modified_host = EntryEditorHost(self._registry, self.source_stack)
        self.official_host = EntryEditorHost(self._registry, self.source_stack)
        self.modified_scroll = self._create_scroll_page(self.modified_host)
        self.official_scroll = self._create_scroll_page(self.official_host)
        self.source_stack.addWidget(self.modified_scroll)
        self.source_stack.addWidget(self.official_scroll)
        editor_layout.addWidget(self.source_stack, 1)
        self.detail_stack.addWidget(self.empty_page)
        self.detail_stack.addWidget(self.editor_page)
        right_layout.addWidget(self.detail_stack, 1)

        action_layout = QHBoxLayout()
        self.sync_state_label = CaptionLabel("", self.right_panel)
        self.sync_state_label.hide()
        action_layout.addWidget(self.sync_state_label)
        action_layout.addStretch(1)
        self.discard_button = PushButton(self.tr("放弃未保存修改"), self.right_panel)
        self.save_button = PrimaryPushButton(self.tr("保存修改"), self.right_panel)
        self.restore_display_button = PrimaryPushButton(self.tr("恢复显示"), self.right_panel)
        self.more_button = TransparentDropDownPushButton(self.tr("更多"), self.right_panel)
        action_layout.addWidget(self.discard_button)
        action_layout.addWidget(self.save_button)
        action_layout.addWidget(self.restore_display_button)
        action_layout.addWidget(self.more_button)
        right_layout.addLayout(action_layout)

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([320, 760])
        root_layout.addWidget(self.splitter, 1)
        self._build_more_menu()
        self._show_empty(self.tr("请从左侧选择一个条目"), "")

    def _create_scroll_page(self, host: EntryEditorHost) -> ScrollArea:
        """创建背景透明并承载类型编辑器的 Fluent 滚动页。"""

        scroll = ScrollArea(self.right_panel)
        scroll.setMinimumWidth(0)
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        host.setMinimumWidth(0)
        host.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host.setStyleSheet("QWidget { background: transparent; }")
        return scroll

    def _build_more_menu(self) -> None:
        """创建条目操作和疑难解答菜单。"""

        menu = RoundMenu(parent=self)
        self.hide_action = Action(FluentIcon.HIDE, self.tr("隐藏条目"), self)
        self.restore_official_action = Action(FluentIcon.RETURN, self.tr("恢复官方内容"), self)
        self.confirm_base_action = Action(FluentIcon.UPDATE, self.tr("保留我的修改"), self)
        self.export_action = Action(FluentIcon.SAVE, self.tr("导出原始修改"), self)
        self.sync_action = Action(FluentIcon.SYNC, self.tr("检查并修复搜索功能"), self)
        self.rebuild_action = Action(FluentIcon.HISTORY, self.tr("重新生成全部搜索数据…"), self)
        menu.addActions(
            [
                self.hide_action,
                self.restore_official_action,
                self.confirm_base_action,
                self.export_action,
            ]
        )
        menu.addSeparator()
        menu.addActions([self.sync_action, self.rebuild_action])
        self.more_button.setMenu(menu)

    def _connect_signals(self) -> None:
        """连接导航、编辑、用户状态操作和异步同步信号。"""

        self.package_combo.currentIndexChanged.connect(self._on_package_changed)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.search_edit.textChanged.connect(lambda _text: self._populate_entry_list())
        self.modified_only_check.stateChanged.connect(lambda _state: self._populate_entry_list())
        self.show_hidden_check.stateChanged.connect(lambda _state: self._populate_entry_list())
        self.entry_list.currentItemChanged.connect(self._on_entry_selected)
        self.source_segment.currentItemChanged.connect(self._on_source_changed)
        self.modified_host.dirty_changed.connect(self._on_dirty_changed)
        self.save_button.clicked.connect(lambda: self._save_current())
        self.discard_button.clicked.connect(self._discard_current)
        self.restore_display_button.clicked.connect(self._restore_hidden)
        self.hide_action.triggered.connect(self._hide_current)
        self.restore_official_action.triggered.connect(self._restore_official)
        self.confirm_base_action.triggered.connect(self._confirm_current_base)
        self.export_action.triggered.connect(self._export_override)
        self.sync_action.triggered.connect(self._controller.reconcile_all)
        self.rebuild_action.triggered.connect(self._confirm_rebuild)
        self._controller.started_signal.connect(self._on_sync_started)
        self._controller.progress_signal.connect(self._on_sync_progress)
        self._controller.waiting_signal.connect(self._on_sync_waiting)
        self._controller.completed_signal.connect(self._on_sync_completed)
        self._controller.failed_signal.connect(self._on_sync_failed)
        self.save_shortcut = QShortcut(QKeySequence.Save, self)
        self.save_shortcut.activated.connect(lambda: self._save_current())
        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.activated.connect(self.search_edit.setFocus)

    def reload_packages(self) -> None:
        """重新发现官方包并恢复上次选择的根世界书。"""

        self._loading_ui = True
        self._packages = self._loader.discover()
        self.package_combo.clear()
        for package_id, result in sorted(self._packages.items()):
            name = result.manifest.display_name if result.manifest is not None else package_id
            suffix = self.tr("（需要处理）") if result.readiness != PackageReadiness.READY else ""
            self.package_combo.addItem(f"{name}{suffix}", userData=package_id)
        saved_package = str(self._settings.value("last_package_id", ""))
        selected_index = self.package_combo.findData(saved_package)
        if selected_index < 0 and self.package_combo.count() > 0:
            selected_index = 0
        saved_type = str(self._settings.value("last_entry_type", "story_event"))
        type_index = self.type_combo.findData(saved_type)
        if type_index >= 0:
            self.type_combo.setCurrentIndex(type_index)
        self._selected_entry_type = self._current_entry_type()
        if selected_index >= 0:
            self.package_combo.setCurrentIndex(selected_index)
            self._root_package_id = str(self.package_combo.currentData())
        else:
            self._root_package_id = None
        self._loading_ui = False
        if self._root_package_id is None:
            self.package_info_label.clear()
            self.entry_list.clear()
            self._show_empty(self.tr("尚未安装可用的世界书"), "")
            return
        self._refresh_records()

    def _on_package_changed(self, _index: int) -> None:
        """确认未保存修改后切换根世界书。"""

        if self._loading_ui:
            return
        requested = self.package_combo.currentData()
        if requested is None:
            return
        requested_id = str(requested)
        if requested_id == self._root_package_id:
            return
        previous_id = self._root_package_id
        if not self._resolve_unsaved_changes():
            if previous_id is not None:
                with QSignalBlocker(self.package_combo):
                    self.package_combo.setCurrentIndex(self.package_combo.findData(previous_id))
            return
        self._root_package_id = requested_id
        self._settings.setValue("last_package_id", requested_id)
        self._refresh_records()

    def _on_type_changed(self, _index: int) -> None:
        """确认未保存修改后切换条目类型。"""

        if self._loading_ui:
            return
        requested = self.type_combo.currentData()
        if requested is None:
            return
        if not self._resolve_unsaved_changes():
            with QSignalBlocker(self.type_combo):
                self.type_combo.setCurrentIndex(self.type_combo.findData(self._selected_entry_type))
            return
        self._selected_entry_type = self._current_entry_type()
        self._settings.setValue("last_entry_type", self._selected_entry_type)
        self._populate_entry_list()

    def _refresh_records(self, preferred_entry_id: UUID | None = None) -> None:
        """重新合并依赖闭包并保持当前条目选择。"""

        if self._root_package_id is None:
            return
        result = self._packages[self._root_package_id]
        self._update_package_info(result)
        if result.readiness == PackageReadiness.UNAVAILABLE:
            self._records.clear()
            self.entry_list.clear()
            messages = "\n".join(issue.message for issue in result.issues)
            self._show_empty(self.tr("这个世界书暂时无法使用"), messages)
            return
        records, issues = build_package_entry_records(
            self._packages, self._states, self._root_package_id, self._registry
        )
        self._records = {record.entry.entry_id: record for record in records}
        self._available_entries = [
            record.effective_entry.entry
            for record in records
            if record.effective_entry is not None and not record.hidden
        ]
        if issues and result.readiness == PackageReadiness.DEGRADED:
            self.problem_label.setText(self.tr("部分自定义内容需要处理。请选择带问题的条目查看详情。"))
            self.problem_label.show()
        else:
            self.problem_label.hide()
        if preferred_entry_id is not None:
            key = (self._root_package_id, self._current_entry_type())
            self._session_selection[key] = preferred_entry_id
        self._populate_entry_list()

    def _update_package_info(self, result: PackageLoadResult) -> None:
        """使用面向用户的文案显示包版本和可用性。"""

        if result.manifest is None:
            self.package_info_label.setText(self.tr("需要处理"))
            return
        readiness = {
            # 如果没有问题，就不提示任何内容
            PackageReadiness.READY: "",
            PackageReadiness.DEGRADED: self.tr("部分内容需要处理"),
            PackageReadiness.UNAVAILABLE: self.tr("暂不可用"),
        }[result.readiness]
        self.package_info_label.setText(
            f"v{result.manifest.package_version} · {readiness}" if readiness else
            f"v{result.manifest.package_version}"
        )

    def _populate_entry_list(self) -> None:
        """按类型、搜索和来源筛选重新生成轻量条目列表。"""

        if self._loading_ui or self._root_package_id is None:
            return
        preserve_dirty_editor = self._has_unsaved_changes()
        active_entry_id = (
            self._current_record.entry.entry_id
            if preserve_dirty_editor and self._current_record is not None
            else None
        )
        selected_type = self._current_entry_type()
        needle = self.search_edit.text().strip().casefold()
        modified_only = self.modified_only_check.isChecked()
        show_hidden = self.show_hidden_check.isChecked()
        visible_records = [
            record
            for record in self._records.values()
            if record.entry.entry_type == selected_type
            and (show_hidden or not record.hidden)
            and (
                not modified_only
                or (
                    record.effective_entry is not None
                    and record.effective_entry.source == "override"
                )
            )
            and (not needle or needle in _record_search_text(record).casefold())
        ]
        visible_records.sort(key=_record_sort_key)
        remembered = active_entry_id or self._session_selection.get(
            (self._root_package_id, selected_type)
        )
        self._loading_ui = True
        self.entry_list.clear()
        remembered_row = -1
        for row, record in enumerate(visible_records):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(record.entry.entry_id))
            secondary = _entry_secondary(record.entry)
            if record.hidden:
                secondary = f"{secondary} · {self.tr('已隐藏')}"
            row_widget = EntryListRow(
                _entry_title(record.entry),
                secondary,
                record.hidden,
                record.issue is not None,
                self.entry_list,
            )
            item.setSizeHint(row_widget.sizeHint())
            self.entry_list.addItem(item)
            self.entry_list.setItemWidget(item, row_widget)
            if record.entry.entry_id == remembered:
                remembered_row = row
        if self.entry_list.count() == 0 and not preserve_dirty_editor:
            self._current_record = None
            title = self.tr("没有找到匹配的条目") if needle else self.tr("这个世界书中暂无此类条目")
            self._show_empty(title, "")
        elif remembered_row >= 0:
            self.entry_list.setCurrentRow(remembered_row)
        elif not preserve_dirty_editor and self.entry_list.count() > 0:
            self.entry_list.setCurrentRow(remembered_row if remembered_row >= 0 else 0)
        self._loading_ui = False
        current = self.entry_list.currentItem()
        if current is not None and not preserve_dirty_editor:
            self._display_record(self._records[UUID(str(current.data(Qt.UserRole)))])

    def _on_entry_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        """确认未保存修改后展示新的有效条目位置。"""

        if self._loading_ui or current is None:
            return
        requested_id = UUID(str(current.data(Qt.UserRole)))
        if self._current_record is not None and requested_id == self._current_record.entry.entry_id:
            return
        if not self._resolve_unsaved_changes():
            if previous is not None:
                with QSignalBlocker(self.entry_list):
                    self.entry_list.setCurrentItem(previous)
            return
        record = self._records[requested_id]
        self._display_record(record)

    def _display_record(self, record: PackageEntryRecord) -> None:
        """根据官方、修改、隐藏或不兼容状态配置详情与操作。"""

        self._current_record = record
        if self._root_package_id is not None:
            self._session_selection[(self._root_package_id, record.entry.entry_type)] = record.entry.entry_id
        self.detail_title.setText(_entry_title(record.entry))
        owner_result = self._packages.get(record.owner_package_id)
        owner_name = (
            owner_result.manifest.display_name
            if owner_result is not None and owner_result.manifest is not None
            else record.owner_package_id
        )
        self.owner_label.setText(self.tr(f"来自：{owner_name}"))
        self.problem_label.hide()
        self.modified_host.set_available_entries(self._available_entries)
        self.official_host.set_available_entries(self._available_entries)
        self.detail_stack.setCurrentWidget(self.editor_page)
        self.restore_display_button.hide()
        self.save_button.hide()
        self.discard_button.hide()
        self.source_segment.hide()
        self.modified_badge.hide()
        self.hide_action.setEnabled(False)
        self.restore_official_action.setEnabled(False)
        self.confirm_base_action.setEnabled(False)
        self.export_action.setEnabled(False)

        if record.hidden:
            if record.official_entry is not None:
                self.official_host.load_entry(record.official_entry, True)
            self.source_stack.setCurrentWidget(self.official_scroll)
            self.restore_display_button.show()
            message = self.tr("该条目已隐藏。恢复显示后，已有的修改仍会保留。")
            if record.hidden_base_conflict:
                message += self.tr(" 官方内容在隐藏期间已有更新，恢复后请检查内容。")
            self.problem_label.setText(message)
            self.problem_label.show()
            return

        if record.issue is not None:
            if record.official_entry is not None:
                self.official_host.load_entry(record.official_entry, True)
            self.source_stack.setCurrentWidget(self.official_scroll)
            self.problem_label.setText(self.tr(f"修改暂时无法使用：{record.issue.message}"))
            self.problem_label.show()
            self.restore_official_action.setEnabled(True)
            self.export_action.setEnabled(True)
            return

        if record.effective_entry is None:
            self._show_empty(self.tr("条目暂时无法显示"), "")
            return

        effective = record.effective_entry
        read_only = effective.source == "extension"
        self.modified_host.load_entry(effective.entry, read_only)
        self.source_stack.setCurrentWidget(self.modified_scroll)
        self._set_source_key("modified")
        if record.official_entry is not None:
            self.official_host.load_entry(record.official_entry, True)
            self.hide_action.setEnabled(True)
        if effective.source == "override":
            self.modified_badge.show()
            self.source_segment.show()
            self.restore_official_action.setEnabled(True)
            self.export_action.setEnabled(True)
            self.confirm_base_action.setEnabled(effective.base_conflict)
            if effective.base_conflict:
                self.problem_label.setText(
                    self.tr("官方内容已有更新，请检查你的修改。")
                )
                self.problem_label.show()
        if not read_only and record.official_entry is not None:
            self.save_button.show()
            self.save_button.setEnabled(False)
            self.discard_button.show()
            self.discard_button.setEnabled(False)
        elif read_only:
            self.problem_label.setText(self.tr("现有的“我的条目”在第一版编辑器中只读显示。"))
            self.problem_label.show()

    def _on_source_changed(self, route_key: str) -> None:
        """在未保存保护后切换修改后内容或官方原文。"""

        if route_key == "official" and not self._resolve_unsaved_changes():
            self._set_source_key("modified")
            return
        self.source_stack.setCurrentWidget(
            self.official_scroll if route_key == "official" else self.modified_scroll
        )
        self.save_button.setVisible(route_key == "modified" and self._can_edit_current())
        self.discard_button.setVisible(route_key == "modified" and self._can_edit_current())
        if route_key == "modified":
            dirty = self._has_unsaved_changes()
            self.save_button.setEnabled(dirty)
            self.discard_button.setEnabled(dirty)

    def _set_source_key(self, route_key: str) -> None:
        """无递归信号地同步分段选择与详情页。"""

        with QSignalBlocker(self.source_segment):
            self.source_segment.setCurrentItem(route_key)
        self.source_stack.setCurrentWidget(
            self.official_scroll if route_key == "official" else self.modified_scroll
        )

    def _on_dirty_changed(self, dirty: bool) -> None:
        """根据当前脏状态同步保存与放弃操作。"""

        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)

    def _resolve_unsaved_changes(self) -> bool:
        """在导航离开前处理保存、放弃或取消。"""

        if not self._has_unsaved_changes():
            return True
        result = UnsavedChangesBox(self.window()).exec()
        if result == QDialog.Accepted:
            return self._save_current()
        if result == UnsavedChangesBox.DISCARD_RESULT:
            self._discard_current()
            return True
        return False

    def _has_unsaved_changes(self) -> bool:
        """判断当前可编辑表单是否存在脏状态。"""

        try:
            return self._can_edit_current() and self.modified_host.editor().is_dirty()
        except ValueError:
            return False

    def _can_edit_current(self) -> bool:
        """判断当前记录是否为可创建或更新 Override 的官方条目。"""

        return (
            self._current_record is not None
            and not self._current_record.hidden
            and self._current_record.issue is None
            and self._current_record.official_entry is not None
            and self._current_record.effective_entry is not None
            and self._current_record.effective_entry.source in {"official", "override"}
        )

    def _save_current(self) -> bool:
        """保存完整 Override，并在成功后异步更新搜索索引。"""

        if not self._can_edit_current() or self._current_record is None:
            return False
        record = self._current_record
        official = record.official_entry
        effective = record.effective_entry
        if official is None or effective is None:
            return False
        editor = self.modified_host.editor()
        if not editor.is_dirty():
            return False
        try:
            current_content = editor.content()
            if current_content == effective.entry.content:
                editor.mark_saved()
                return False
            self._edit_service.save_override(
                record.owner_package_id,
                official,
                effective.entry,
                current_content,
                self._available_entries,
                editor.retrieval_reviewed(),
            )
        except RetrievalSummaryReviewRequired as exc:
            self._show_error(self.tr("请确认检索摘要"), str(exc))
            return False
        except (KeyError, TypeError, ValueError, WorldbookReferenceError, WorldbookSequenceConflict) as exc:
            self._show_error(self.tr("无法保存修改"), str(exc))
            return False
        entry_id = official.entry_id
        editor.mark_saved()
        self._refresh_records(entry_id)
        self._controller.reconcile_all()
        InfoBar.success(
            self.tr("修改已保存"),
            self.tr("正在更新世界书的搜索内容。"),
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )
        return True

    def _discard_current(self) -> None:
        """从最近保存的有效内容重新加载当前表单。"""

        if self._current_record is not None:
            self._display_record(self._current_record)

    def _hide_current(self) -> None:
        """确认后隐藏官方条目身份并保留已有 Override。"""

        record = self._current_record
        if record is None or record.official_entry is None or not self._resolve_unsaved_changes():
            return
        box = MessageBox(
            self.tr("隐藏这个条目吗？"),
            self.tr("它将不再参与世界书搜索。恢复显示后，你的修改仍会保留。"),
            self.window(),
        )
        box.yesButton.setText(self.tr("隐藏条目"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._edit_service.hide_entry(record.owner_package_id, record.official_entry)
            self._refresh_records()
            self._controller.reconcile_all()

    def _restore_hidden(self) -> None:
        """恢复当前隐藏条目并保留 Override。"""

        record = self._current_record
        if record is None or not record.hidden:
            return
        self._edit_service.restore_entry(record.owner_package_id, record.entry.entry_id)
        self.show_hidden_check.setChecked(False)
        self._refresh_records(record.entry.entry_id)
        self._controller.reconcile_all()

    def _restore_official(self) -> None:
        """确认后删除当前 Override 并恢复官方内容。"""

        record = self._current_record
        if record is None or record.official_entry is None:
            return
        box = MessageBox(
            self.tr("恢复官方内容吗？"),
            self.tr("你的修改将被删除，该条目将恢复为原始内容"),
            self.window(),
        )
        box.yesButton.setText(self.tr("恢复官方内容"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._edit_service.remove_override(record.owner_package_id, record.official_entry.entry_id)
            self._refresh_records(record.official_entry.entry_id)
            self._controller.reconcile_all()

    def _confirm_current_base(self) -> None:
        """确认继续使用用户内容并接受当前官方 revision 为新基准。"""

        record = self._current_record
        if record is None or record.official_entry is None:
            return
        self._edit_service.confirm_current_base(record.owner_package_id, record.official_entry)
        self._refresh_records(record.official_entry.entry_id)
        InfoBar.success(
            self.tr("已保留修改"),
            "",
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _export_override(self) -> None:
        """把当前原始 Override 导出到用户选择的 JSON 文件。"""

        record = self._current_record
        if record is None:
            return
        override = self._edit_service.raw_override(record.owner_package_id, record.entry.entry_id)
        if override is None:
            return
        default_name = f"worldbook-override-{override.entry_id}.json"
        path_text, _filter = QFileDialog.getSaveFileName(
            self.window(), self.tr("导出原始修改"), default_name, self.tr("JSON 文件 (*.json)")
        )
        if not path_text:
            return
        try:
            Path(path_text).write_text(
                json.dumps(override.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self._show_error(self.tr("导出失败"), str(exc))

    def _confirm_rebuild(self) -> None:
        """确认后异步重新生成全部世界书搜索数据。"""

        box = MessageBox(
            self.tr("重新生成全部搜索数据？"),
            self.tr(
                "这会删除并重新生成世界书的搜索数据，可能需要一些时间。"
                "世界书内容和你的修改不会受到影响。"
            ),
            self.window(),
        )
        box.yesButton.setText(self.tr("重新生成"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._controller.rebuild()

    def _on_sync_started(self) -> None:
        """只在同步进行时显示紧凑状态。"""

        self.sync_state_label.setText(self.tr("正在更新搜索内容…"))
        self.sync_state_label.show()

    def _on_sync_progress(self, stage: str) -> None:
        """把内部进度阶段收敛为用户可理解的状态。"""

        self.sync_state_label.setText(self.tr("正在更新搜索内容…"))
        self.sync_state_label.setToolTip(stage)
        self.sync_state_label.show()

    def _on_sync_waiting(self, remaining: int) -> None:
        """提示正在等待另一个世界书更新任务。"""

        self.sync_state_label.setText(self.tr(f"正在等待其他更新任务（约 {remaining} 秒）"))
        self.sync_state_label.show()

    def _on_sync_completed(self, report: dict[str, object]) -> None:
        """隐藏健康状态，仅在有隔离内容时提示用户处理。"""

        self.sync_state_label.hide()
        readiness = str(report.get("readiness", "unavailable"))
        if readiness == "degraded":
            InfoBar.warning(
                self.tr("部分自定义内容需要处理"),
                self.tr("其他世界书内容仍然可用。"),
                duration=-1,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _on_sync_failed(self, message: str) -> None:
        """说明内容已保存但搜索索引暂未更新。"""

        self.sync_state_label.hide()
        InfoBar.error(
            self.tr("修改已保存，但搜索内容暂未更新"),
            message,
            duration=-1,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _show_empty(self, title: str, message: str) -> None:
        """显示页面级空状态或包诊断信息。"""

        self.detail_title.setText(self.tr("世界书"))
        self.modified_badge.hide()
        self.owner_label.clear()
        self.empty_title.setText(title)
        self.empty_message.setText(message)
        self.detail_stack.setCurrentWidget(self.empty_page)
        self.save_button.hide()
        self.discard_button.hide()
        self.restore_display_button.hide()

    def _show_error(self, title: str, message: str) -> None:
        """使用 Fluent InfoBar 展示保存或导出错误。"""

        InfoBar.error(
            title,
            message,
            duration=-1,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _current_entry_type(self) -> EntryType:
        """返回当前类型 ComboBox 的稳定枚举值。"""

        value = self.type_combo.currentData()
        if value in {item[0] for item in _ENTRY_TYPE_LABELS}:
            return value
        return "story_event"


def _entry_title(entry: WorldbookEntry) -> str:
    """返回类型化条目的首选可读标题。"""

    for key in ("title", "canonical_subject", "state_summary", "thought_text"):
        value = entry.content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "未命名条目"


def _entry_secondary(entry: WorldbookEntry) -> str:
    """返回适合左侧列表第二行的类型相关摘要。"""

    if entry.entry_type == "story_event":
        episode = entry.content.get("episode")
        participants = _character_names(entry.content.get("participants"))
        return f"第 {episode} 集 · {participants}" if isinstance(episode, int) else participants
    if entry.entry_type == "character_thought":
        character = _character_name(entry.content.get("character_id"))
        return f"{character} · {_time_label(entry)}"
    if entry.entry_type == "character_relation":
        subject = _character_name(entry.content.get("subject_character_id"))
        target = _character_name(entry.content.get("object_character_id"))
        return f"{subject} → {target} · {_time_label(entry)}"
    scope = entry.content.get("scope_type")
    return "适用于整个世界书包" if scope == "global" else "适用于指定系列"


def _record_search_text(record: PackageEntryRecord) -> str:
    """汇总可读正文、角色、标签和检索摘要供即时搜索。"""

    entry = record.entry
    parts = [str(value) for value in entry.content.values() if isinstance(value, (str, int))]
    for key in ("participants", "tags"):
        value = entry.content.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
            if key == "participants":
                parts.extend(_character_name(item) for item in value)
    for key in ("character_id", "subject_character_id", "object_character_id"):
        parts.append(_character_name(entry.content.get(key)))
    return " ".join(parts)


def _record_sort_key(record: PackageEntryRecord) -> tuple[int, int, str]:
    """按类型使用剧情顺序、角色或标题生成稳定排序键。"""

    entry = record.entry
    if entry.entry_type == "story_event":
        episode = entry.content.get("episode")
        order = entry.content.get("time_order")
        return (
            episode if isinstance(episode, int) else 999999,
            order if isinstance(order, int) else 999999,
            _entry_title(entry),
        )
    if entry.entry_type in {"character_thought", "character_relation"}:
        visible_from = entry.content.get("visible_from")
        return (
            visible_from if isinstance(visible_from, int) else 999999,
            0,
            _entry_secondary(entry),
        )
    return (0, 0, _entry_title(entry))


def _character_name(value: object) -> str:
    """把角色内部 ID 转换为中文常用名。"""

    try:
        return CharacterId(str(value)).common_name
    except ValueError:
        return str(value) if value is not None else "未知角色"


def _character_names(value: object) -> str:
    """把角色 ID 列表转换为中文顿号分隔文本。"""

    if not isinstance(value, list):
        return "未知角色"
    return "、".join(_character_name(item) for item in value)


def _time_label(entry: WorldbookEntry) -> str:
    """把内部开始坐标转换为集数与集内时间点。"""

    series_id = entry.content.get("series_id")
    visible_from = entry.content.get("visible_from")
    visible_to = entry.content.get("visible_to")
    if not isinstance(series_id, str) or not isinstance(visible_from, int):
        return "时间未知"
    try:
        start = decode_story_time(series_id, visible_from).display_text()
    except ValueError:
        return "时间未知"
    return f"{start} · 持续有效" if visible_to == OPEN_ENDED_TIME else start
