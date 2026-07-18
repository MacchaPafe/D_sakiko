"""世界书只读管理页面。"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TextEdit,
)

from rag.worldbook.adapters import AdapterRegistry, create_default_registry
from rag.worldbook.effective_entries import entry_revision, merge_effective_entries
from rag.worldbook.models import EffectiveWorldbookEntry, PackageLoadResult, WorldbookTombstone
from rag.worldbook.package_loader import WorldbookPackageLoader
from rag.worldbook.paths import WorldbookPaths
from rag.worldbook.user_state import WorldbookUserStateRepository
from ui.controllers.worldbook_sync_controller import WorldbookSyncController


class WorldbookArea(QWidget):
    """展示官方包、有效条目、来源冲突和异步同步状态。"""

    def __init__(self, parent: QWidget | None = None, app_root: Path | None = None) -> None:
        """创建不依赖 Qdrant 可用性的三栏查看器。"""

        super().__init__(parent)
        self.setObjectName("WorldbookArea")
        resolved_root = app_root or Path(__file__).resolve().parents[3]
        self._paths = WorldbookPaths(resolved_root)
        self._loader = WorldbookPackageLoader(self._paths.official_packages)
        self._states = WorldbookUserStateRepository(self._paths.user_state)
        self._registry: AdapterRegistry = create_default_registry()
        self._packages: dict[str, PackageLoadResult] = {}
        self._entries: dict[UUID, EffectiveWorldbookEntry] = {}
        self._current_package_id: str | None = None
        self._controller = WorldbookSyncController(resolved_root, self)
        self._build_ui()
        self._connect_signals()
        self.reload_packages()

    def _build_ui(self) -> None:
        """构建包、条目和详情三栏以及底部同步区。"""

        root_layout = QVBoxLayout(self)
        title = SubtitleLabel(self.tr("世界书管理"), self)
        root_layout.addWidget(title)
        splitter = QSplitter(Qt.Horizontal, self)
        self.package_list = ListWidget(splitter)
        self.entry_list = ListWidget(splitter)
        self.detail_view = TextEdit(splitter)
        self.detail_view.setReadOnly(True)
        splitter.setSizes([220, 320, 500])
        root_layout.addWidget(splitter, 1)
        action_layout = QHBoxLayout()
        self.hide_button = PushButton(self.tr("隐藏官方条目"), self)
        self.restore_button = PushButton(self.tr("恢复条目"), self)
        self.sync_button = PrimaryPushButton(self.tr("重新同步"), self)
        self.rebuild_button = PushButton(self.tr("全量重建"), self)
        action_layout.addWidget(self.hide_button)
        action_layout.addWidget(self.restore_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.sync_button)
        action_layout.addWidget(self.rebuild_button)
        root_layout.addLayout(action_layout)
        self.status_label = BodyLabel(self.tr("索引状态：尚未同步"), self)
        root_layout.addWidget(self.status_label)

    def _connect_signals(self) -> None:
        """连接列表、用户状态操作和异步同步 signals。"""

        self.package_list.currentItemChanged.connect(self._on_package_selected)
        self.entry_list.currentItemChanged.connect(self._on_entry_selected)
        self.hide_button.clicked.connect(self._hide_selected)
        self.restore_button.clicked.connect(self._restore_selected)
        self.sync_button.clicked.connect(self._controller.reconcile_all)
        self.rebuild_button.clicked.connect(self._confirm_rebuild)
        self._controller.progress_signal.connect(lambda stage: self.status_label.setText(f"索引状态：{stage}"))
        self._controller.waiting_signal.connect(lambda remaining: self.status_label.setText(f"索引状态：等待其他进程（剩余 {remaining} 秒）"))
        self._controller.completed_signal.connect(self._on_sync_completed)
        self._controller.failed_signal.connect(lambda message: self.status_label.setText(f"索引不可用：{message}"))

    def reload_packages(self) -> None:
        """重新读取权威 JSON，并刷新包列表。"""

        self._packages = self._loader.discover()
        self.package_list.clear()
        for package_id, result in sorted(self._packages.items()):
            name = result.manifest.display_name if result.manifest is not None else package_id
            item = QListWidgetItem(f"{name}\n{result.readiness.value}")
            item.setData(Qt.UserRole, package_id)
            self.package_list.addItem(item)
        if self.package_list.count():
            self.package_list.setCurrentRow(0)

    def _on_package_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """显示所选包的有效条目和隐藏条目。"""

        self.entry_list.clear()
        self._entries.clear()
        if current is None:
            return
        package_id = str(current.data(Qt.UserRole))
        self._current_package_id = package_id
        result = self._packages[package_id]
        if result.manifest is None:
            self.detail_view.setPlainText("manifest 无法加载\n" + "\n".join(issue.message for issue in result.issues))
            return
        try:
            state = self._states.load(package_id)
        except (OSError, ValueError, TypeError) as exc:
            self.detail_view.setPlainText(f"用户状态损坏：{exc}")
            return
        effective, issues = merge_effective_entries(package_id, result.entries, state)
        self._entries = {item.entry.entry_id: item for item in effective}
        for item in effective:
            label = _entry_title(item)
            row = QListWidgetItem(f"[{item.entry.entry_type}] {label} · {item.source}{' · 冲突' if item.base_conflict else ''}")
            row.setData(Qt.UserRole, str(item.entry.entry_id))
            row.setData(Qt.UserRole + 1, False)
            self.entry_list.addItem(row)
        official_map = {entry.entry_id: entry for entry in result.entries}
        for tombstone in state.tombstones:
            official = official_map.get(tombstone.entry_id)
            if official is None:
                continue
            row = QListWidgetItem(f"[{official.entry_type}] {_content_title(official.content)} · 已隐藏")
            row.setData(Qt.UserRole, str(official.entry_id))
            row.setData(Qt.UserRole + 1, True)
            self.entry_list.addItem(row)
        package_lines = [f"包：{result.manifest.display_name}", f"版本：{result.manifest.package_version}", f"时间线：{result.manifest.timeline_id}"]
        package_lines.extend(f"问题：{issue.message}" for issue in [*result.issues, *issues])
        self.detail_view.setPlainText("\n".join(package_lines))

    def _on_entry_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """通过 Type Module 显示通用只读字段。"""

        if current is None or bool(current.data(Qt.UserRole + 1)):
            return
        entry_id = UUID(str(current.data(Qt.UserRole)))
        effective = self._entries.get(entry_id)
        if effective is None:
            return
        try:
            module = self._registry.module(effective.entry.entry_type)
            lines = [f"entry_id: {entry_id}", f"来源: {effective.source}", f"revision: {effective.revision}", ""]
            lines.extend(f"{name}: {value}" for name, value in module.display_fields(effective.entry))
            lines.extend(["", "当前条目格式尚未开放编辑。"])
            self.detail_view.setPlainText("\n".join(lines))
        except (TypeError, ValueError, KeyError) as exc:
            self.detail_view.setPlainText(f"条目无法解释：{exc}")

    def _hide_selected(self) -> None:
        """保存 tombstone 后异步请求对账。"""

        current = self.entry_list.currentItem()
        if current is None or self._current_package_id is None or bool(current.data(Qt.UserRole + 1)):
            return
        entry_id = UUID(str(current.data(Qt.UserRole)))
        effective = self._entries.get(entry_id)
        if effective is None or effective.source == "extension":
            return
        result = self._packages[self._current_package_id]
        official = next((entry for entry in result.entries if entry.entry_id == entry_id), None)
        if official is None:
            return
        self._states.hide(self._current_package_id, WorldbookTombstone(entry_id=entry_id, base_revision=entry_revision(official)))
        self._refresh_current_package()
        self._controller.reconcile_all()

    def _restore_selected(self) -> None:
        """移除 tombstone 后异步请求对账。"""

        current = self.entry_list.currentItem()
        if current is None or self._current_package_id is None or not bool(current.data(Qt.UserRole + 1)):
            return
        self._states.restore(self._current_package_id, UUID(str(current.data(Qt.UserRole))))
        self._refresh_current_package()
        self._controller.reconcile_all()

    def _refresh_current_package(self) -> None:
        """保持当前包选择并重新生成条目列表。"""

        current = self.package_list.currentItem()
        self._on_package_selected(current, None)

    def _confirm_rebuild(self) -> None:
        """确认后异步重建世界书索引。"""

        box = MessageBox(
            self.tr("确认重建世界书索引吗？"),
            self.tr("只会删除并重建三类世界书索引，不会修改 JSON 内容。"),
            self.window(),
        )
        box.yesButton.setText(self.tr("重建"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._controller.rebuild()

    def _on_sync_completed(self, report: dict[str, object]) -> None:
        """显示实时同步报告，不持久化运行结果。"""

        self.status_label.setText(
            f"索引状态：{report.get('readiness', 'unknown')}，写入 {report.get('indexed_count', 0)}，删除 {report.get('deleted_count', 0)}"
        )


def _content_title(content: dict[str, object]) -> str:
    """从尚未稳定的内容中提取安全列表标题。"""

    for key in ("title", "state_summary", "thought_text", "canonical_subject"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "未命名条目"


def _entry_title(entry: EffectiveWorldbookEntry) -> str:
    """返回有效条目的列表标题。"""

    return _content_title(entry.entry.content)
