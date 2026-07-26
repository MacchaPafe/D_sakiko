"""世界书四类条目的 qfluentwidgets 类型化编辑组件。"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PyQt5.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLayout,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FlowLayout,
    FluentIcon,
    HeaderCardWidget,
    LineEdit,
    ListWidget,
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
    SearchLineEdit,
    SimpleCardWidget,
    SpinBox,
    TextEdit,
    TransparentToolButton,
)

from rag.models import BAND_MEMBERS, BandId, CanonBranch, SeriesId
from rag.worldbook.adapters import AdapterRegistry
from rag.worldbook.models import EntryType, WorldbookEntry
from rag.worldbook.time_coordinates import (
    EPISODE_SLOT_COUNT,
    OPEN_ENDED_TIME,
    StoryTimeCoordinate,
    decode_story_time,
    encode_story_time,
    is_open_ended_time,
)


def _configure_form_layout(form: QFormLayout) -> None:
    """让表单字段优先占满可用宽度，并在窄宽度下自动换行。"""

    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(12)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)


def _clear_flow_widgets(layout: FlowLayout) -> None:
    """立即隐藏并延迟销毁流式布局中的旧控件。"""

    while layout.count():
        widget = layout.takeAt(0)
        if isinstance(widget, QWidget):
            widget.hide()
            widget.deleteLater()


def _refresh_flow_container(container: QWidget, layout: FlowLayout) -> None:
    """立即重排动态内容，并把新的换行高度反馈给父布局。"""

    layout.invalidate()
    target_height = max(0, layout.heightForWidth(container.contentsRect().width()))
    if container.minimumHeight() != target_height:
        container.setMinimumHeight(target_height)
    layout.setGeometry(container.contentsRect())
    current: QWidget | None = container
    while current is not None:
        current.updateGeometry()
        parent = current.parentWidget()
        parent_layout = QWidget.layout(parent) if parent is not None else None
        if parent_layout is not None:
            parent_layout.invalidate()
            parent_layout.activate()
        current = parent


@dataclass(frozen=True, slots=True)
class SelectionOption:
    """表示多选对话框中的稳定值和用户可读标签。"""

    key: str
    label: str
    search_text: str = ""
    group_key: str = ""
    group_label: str = ""


@dataclass(frozen=True, slots=True)
class FormValidationIssue:
    """描述一个需要用户处理的表单问题及其聚焦控件。"""

    message: str
    focus_widget: QWidget


_OPTION_SEARCH_ROLE = Qt.UserRole + 1
_GROUP_KEY_ROLE = Qt.UserRole + 2
_ITEM_KIND_ROLE = Qt.UserRole + 3
_GROUP_SEARCH_ROLE = Qt.UserRole + 4
_OPTION_ITEM_KIND = "option"
_GROUP_ITEM_KIND = "group"


def _prioritize_selection_groups(
    options: list[SelectionOption],
    selected_keys: list[str],
) -> list[SelectionOption]:
    """按既有选择首次命中的顺序置顶完整选项分组。"""

    group_order = list(
        dict.fromkeys(option.group_key for option in options if option.group_key)
    )
    if not group_order:
        return list(options)

    group_by_key = {option.key: option.group_key for option in options}
    prioritized_groups = list(
        dict.fromkeys(
            group_by_key[key]
            for key in selected_keys
            if key in group_by_key and group_by_key[key]
        )
    )
    ordered_groups = [
        *prioritized_groups,
        *(group for group in group_order if group not in prioritized_groups),
    ]
    grouped_options = {
        group: [option for option in options if option.group_key == group]
        for group in group_order
    }
    ordered = [
        option
        for group in ordered_groups
        for option in grouped_options[group]
    ]
    ordered.extend(option for option in options if not option.group_key)
    return ordered


class MultiSelectDialog(MessageBoxBase):
    """提供搜索和复选的 Fluent 多选对话框。"""

    def __init__(
        self,
        title: str,
        options: list[SelectionOption],
        selected_keys: list[str],
        parent: QWidget | None = None,
    ) -> None:
        """创建带初始选择的多选对话框。"""

        super().__init__(parent)
        self._selected_keys = list(dict.fromkeys(selected_keys))
        self._options = _prioritize_selection_groups(options, self._selected_keys)
        self._known_keys = {option.key for option in options}
        self.widget.setMinimumWidth(560)
        self.title_label = BodyLabel(title, self)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(self.tr("搜索…"))
        self.list_widget = ListWidget(self)
        self.list_widget.setMinimumHeight(360)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.search_edit)
        self.viewLayout.addWidget(self.list_widget)
        self.yesButton.setText(self.tr("确定"))
        self.cancelButton.setText(self.tr("取消"))
        self.search_edit.textChanged.connect(self._filter_items)
        self._populate()

    def _populate(self) -> None:
        """用不可选择的分组标题和可复选选项填充列表。"""

        selected = set(self._selected_keys)
        previous_group = ""
        for option in self._options:
            if option.group_key and option.group_key != previous_group:
                header = QListWidgetItem(option.group_label)
                header.setData(_ITEM_KIND_ROLE, _GROUP_ITEM_KIND)
                header.setData(_GROUP_KEY_ROLE, option.group_key)
                header.setData(_GROUP_SEARCH_ROLE, option.group_label.casefold())
                header.setFlags(Qt.ItemIsEnabled)
                self.list_widget.addItem(header)
            previous_group = option.group_key
            item = QListWidgetItem(option.label)
            item.setData(Qt.UserRole, option.key)
            item.setData(
                _OPTION_SEARCH_ROLE,
                f"{option.label} {option.search_text}".casefold(),
            )
            item.setData(_GROUP_KEY_ROLE, option.group_key)
            item.setData(_ITEM_KIND_ROLE, _OPTION_ITEM_KIND)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if option.key in selected else Qt.Unchecked)
            self.list_widget.addItem(item)

    def _filter_items(self, text: str) -> None:
        """根据用户可读文本即时过滤列表。"""

        needle = text.strip().casefold()
        matching_groups: set[str] = set()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(_ITEM_KIND_ROLE) != _GROUP_ITEM_KIND:
                continue
            group_key = str(item.data(_GROUP_KEY_ROLE) or "")
            group_search = str(item.data(_GROUP_SEARCH_ROLE) or "")
            if needle and needle in group_search:
                matching_groups.add(group_key)

        visible_groups: set[str] = set()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(_ITEM_KIND_ROLE) != _OPTION_ITEM_KIND:
                continue
            group_key = str(item.data(_GROUP_KEY_ROLE) or "")
            haystack = str(item.data(_OPTION_SEARCH_ROLE) or "")
            visible = not needle or group_key in matching_groups or needle in haystack
            item.setHidden(not visible)
            if visible and group_key:
                visible_groups.add(group_key)

        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(_ITEM_KIND_ROLE) == _GROUP_ITEM_KIND:
                item.setHidden(str(item.data(_GROUP_KEY_ROLE) or "") not in visible_groups)

    def selected_keys(self) -> list[str]:
        """保留既有值顺序，并按当前显示顺序追加新勾选值。"""

        checked_keys: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if (
                item.data(_ITEM_KIND_ROLE) == _OPTION_ITEM_KIND
                and item.checkState() == Qt.Checked
            ):
                checked_keys.append(str(item.data(Qt.UserRole)))
        checked = set(checked_keys)
        preserved = [
            key
            for key in self._selected_keys
            if key not in self._known_keys or key in checked
        ]
        existing = set(self._selected_keys)
        appended = [key for key in checked_keys if key not in existing]
        return [*preserved, *appended]


class _InlineTagEdit(LineEdit):
    """为标签原位编辑提供失焦提交和 Esc 取消信号。"""

    commit_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def focusOutEvent(self, event: QEvent) -> None:
        """失去焦点时请求提交当前文字。"""

        super().focusOutEvent(event)
        self.commit_requested.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """按 Esc 时取消，其余按键沿用 Fluent 输入框行为。"""

        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)


class SelectionChip(SimpleCardWidget):
    """显示一个可移除、可按需原位编辑的已选值。"""

    remove_requested = pyqtSignal(str)
    edit_committed = pyqtSignal(str, str)

    def __init__(
        self,
        key: str,
        label: str,
        parent: QWidget | None = None,
        *,
        editable: bool = False,
    ) -> None:
        """创建带移除按钮及可选编辑入口的紧凑标签。"""

        super().__init__(parent)
        self._key = key
        self._editable = editable
        self._read_only = False
        self._editing = False
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 4, 3)
        layout.setSpacing(4)
        self.label = BodyLabel(label, self)
        self.label.installEventFilter(self)
        layout.addWidget(self.label)
        self.edit_field = _InlineTagEdit(self)
        self.edit_field.setText(label)
        self.edit_field.setMinimumWidth(max(80, self.label.sizeHint().width() + 16))
        self.edit_field.returnPressed.connect(self._commit_edit)
        self.edit_field.commit_requested.connect(self._commit_edit)
        self.edit_field.cancel_requested.connect(self._cancel_edit)
        self.edit_field.hide()
        layout.addWidget(self.edit_field)
        self.edit_button = TransparentToolButton(FluentIcon.EDIT, self)
        self.edit_button.setFixedSize(24, 24)
        self.edit_button.setToolTip(self.tr("编辑标签"))
        self.edit_button.clicked.connect(self._start_edit)
        self.edit_button.hide()
        layout.addWidget(self.edit_button)
        self.remove_button = TransparentToolButton(FluentIcon.CLOSE, self)
        self.remove_button.setFixedSize(24, 24)
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self._key))
        layout.addWidget(self.remove_button)

    def set_read_only(self, read_only: bool) -> None:
        """控制移除和编辑入口是否可用。"""

        if read_only and self._editing:
            self._cancel_edit()
        self._read_only = read_only
        self.remove_button.setVisible(not read_only and not self._editing)
        self.edit_button.setVisible(
            self._editable and not read_only and not self._editing and self.underMouse()
        )
        self.updateGeometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """双击标签正文时进入可编辑模式。"""

        if (
            watched is getattr(self, "label", None)
            and event.type() == QEvent.MouseButtonDblClick
            and self._editable
            and not self._read_only
        ):
            self._start_edit()
            return True
        return super().eventFilter(watched, event)

    def enterEvent(self, event: QEvent) -> None:
        """悬停时显示自由标签的编辑按钮。"""

        super().enterEvent(event)
        if self._editable and not self._read_only and not self._editing:
            self.edit_button.show()
            self.updateGeometry()

    def leaveEvent(self, event: QEvent) -> None:
        """离开标签时隐藏编辑按钮。"""

        super().leaveEvent(event)
        if not self._editing:
            self.edit_button.hide()
            self.updateGeometry()

    def _start_edit(self) -> None:
        """切换到行内文字编辑器并选中当前标签。"""

        if not self._editable or self._read_only or self._editing:
            return
        self._editing = True
        self.edit_field.setText(self.label.text())
        self.label.hide()
        self.edit_button.hide()
        self.remove_button.hide()
        self.edit_field.show()
        self.edit_field.setFocus()
        self.edit_field.selectAll()
        self.updateGeometry()

    def _commit_edit(self) -> None:
        """提交非空且实际变化的标签文字。"""

        if not self._editing:
            return
        value = self.edit_field.text().strip()
        self._finish_edit()
        if value and value != self._key:
            self.edit_committed.emit(self._key, value)

    def _cancel_edit(self) -> None:
        """取消原位编辑并恢复原标签。"""

        if not self._editing:
            return
        self._finish_edit()

    def _finish_edit(self) -> None:
        """退出原位编辑并恢复当前读写状态的按钮。"""

        self._editing = False
        self.edit_field.hide()
        self.label.show()
        self.remove_button.setVisible(not self._read_only)
        self.edit_button.setVisible(
            self._editable and not self._read_only and self.underMouse()
        )
        self.updateGeometry()


class MultiSelectField(QWidget):
    """以内嵌标签和搜索对话框编辑多个稳定值。"""

    value_changed = pyqtSignal()

    def __init__(self, button_text: str, parent: QWidget | None = None) -> None:
        """创建空的多选字段。"""

        super().__init__(parent)
        self._button_text = button_text
        self._options: list[SelectionOption] = []
        self._selected_keys: list[str] = []
        self._read_only = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.chip_container = QWidget(self)
        self.chip_container.setMinimumWidth(0)
        self.chip_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.chip_layout = FlowLayout(self.chip_container, needAni=False, isTight=True)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setHorizontalSpacing(6)
        self.chip_layout.setVerticalSpacing(6)
        layout.addWidget(self.chip_container)
        self.select_button = PushButton(button_text, self)
        self.select_button.clicked.connect(self._open_dialog)
        layout.addWidget(self.select_button, 0, Qt.AlignLeft)

    def set_options(self, options: list[SelectionOption]) -> None:
        """替换可选值，并保留仍需用户处理的未知既有选择。"""

        self._options = options
        self._render_chips()

    def option_keys(self) -> set[str]:
        """返回当前对话框允许选择的稳定值集合。"""

        return {option.key for option in self._options}

    def set_value(self, keys: list[str]) -> None:
        """设置当前选择并保持第一次出现的顺序。"""

        self._selected_keys = list(dict.fromkeys(keys))
        self._render_chips()

    def value(self) -> list[str]:
        """返回当前稳定值列表。"""

        return list(self._selected_keys)

    def set_read_only(self, read_only: bool) -> None:
        """切换选择与移除操作是否可用。"""

        self._read_only = read_only
        self.select_button.setVisible(not read_only)
        for index in range(self.chip_layout.count()):
            item = self.chip_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, SelectionChip):
                widget.set_read_only(read_only)
        self._schedule_chip_layout_refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """宽度改变后重新计算标签换行所需高度。"""

        super().resizeEvent(event)
        self._schedule_chip_layout_refresh()

    def _open_dialog(self) -> None:
        """打开搜索多选对话框并应用确认结果。"""

        if self._read_only:
            return
        dialog = MultiSelectDialog(self._button_text, self._options, self._selected_keys, self.window())
        if dialog.exec():
            selected = dialog.selected_keys()
            if selected != self._selected_keys:
                self._selected_keys = selected
                self._render_chips()
                self.value_changed.emit()

    def _remove_key(self, key: str) -> None:
        """移除一个选择并发送变化信号。"""

        if self._read_only or key not in self._selected_keys:
            return
        self._selected_keys.remove(key)
        self._render_chips()
        self.value_changed.emit()

    def _render_chips(self) -> None:
        """根据当前值重新生成紧凑标签。"""

        _clear_flow_widgets(self.chip_layout)
        labels = {option.key: option.label for option in self._options}
        for key in self._selected_keys:
            chip = SelectionChip(key, labels.get(key, key), self.chip_container)
            chip.set_read_only(self._read_only)
            chip.remove_requested.connect(self._remove_key)
            self.chip_layout.addWidget(chip)
        self._schedule_chip_layout_refresh()

    def _schedule_chip_layout_refresh(self) -> None:
        """立即并在当前事件结束后各刷新一次标签布局。"""

        _refresh_flow_container(self.chip_container, self.chip_layout)
        QTimer.singleShot(0, self._refresh_chip_layout)

    def _refresh_chip_layout(self) -> None:
        """使用事件处理完成后的最终尺寸刷新标签布局。"""

        _refresh_flow_container(self.chip_container, self.chip_layout)


class TagEditor(QWidget):
    """使用 Fluent 输入框和流式标签编辑自由文本标签。"""

    value_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建支持回车与逗号添加的标签编辑器。"""

        super().__init__(parent)
        self._tags: list[str] = []
        self._read_only = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.chip_container = QWidget(self)
        self.chip_container.setMinimumWidth(0)
        self.chip_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.chip_layout = FlowLayout(self.chip_container, needAni=False, isTight=True)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setHorizontalSpacing(6)
        self.chip_layout.setVerticalSpacing(6)
        layout.addWidget(self.chip_container)
        self.input_edit = LineEdit(self)
        self.input_edit.setPlaceholderText(self.tr("输入标签后按回车添加"))
        self.input_edit.returnPressed.connect(self._commit_input)
        self.input_edit.textEdited.connect(self._commit_on_comma)
        layout.addWidget(self.input_edit)
        self.help_label = CaptionLabel(
            self.tr(
                "添加角色、地点、事件或主题等关键词，可以让这条内容更容易在相关对话中被找到。"
            ),
            self,
        )
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)

    def set_value(self, tags: list[str]) -> None:
        """设置清理并去重后的标签。"""

        normalized = [tag.strip() for tag in tags if tag.strip()]
        self._tags = list(dict.fromkeys(normalized))
        self._render_chips()

    def value(self) -> list[str]:
        """返回当前标签列表。"""

        return list(self._tags)

    def set_read_only(self, read_only: bool) -> None:
        """控制标签输入和移除操作。"""

        self._read_only = read_only
        self.input_edit.setVisible(not read_only)
        self.help_label.setVisible(not read_only)
        for index in range(self.chip_layout.count()):
            item = self.chip_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, SelectionChip):
                widget.set_read_only(read_only)
        self._schedule_chip_layout_refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """宽度改变后重新计算标签换行所需高度。"""

        super().resizeEvent(event)
        self._schedule_chip_layout_refresh()

    def _commit_on_comma(self, text: str) -> None:
        """用户输入逗号时立即提交前面的标签。"""

        if "," in text or "，" in text:
            self._commit_input()

    def _commit_input(self) -> None:
        """把输入框中的逗号分隔文本加入标签集合。"""

        if self._read_only:
            return
        raw = self.input_edit.text().replace("，", ",")
        additions = [item.strip() for item in raw.split(",") if item.strip()]
        if not additions:
            return
        updated = list(dict.fromkeys([*self._tags, *additions]))
        changed = updated != self._tags
        self._tags = updated
        self.input_edit.clear()
        self._render_chips()
        if changed:
            self.value_changed.emit()

    def _remove_tag(self, tag: str) -> None:
        """移除一个自由标签。"""

        if self._read_only or tag not in self._tags:
            return
        self._tags.remove(tag)
        self._render_chips()
        self.value_changed.emit()

    def _edit_tag(self, old_tag: str, new_tag: str) -> None:
        """原位替换标签，并把重复目标收敛为已有标签。"""

        if self._read_only or old_tag not in self._tags:
            return
        normalized = new_tag.strip()
        if not normalized or normalized == old_tag:
            return
        if normalized in self._tags:
            self._tags.remove(old_tag)
        else:
            index = self._tags.index(old_tag)
            self._tags[index] = normalized
        self._render_chips()
        self.value_changed.emit()

    def _render_chips(self) -> None:
        """根据当前标签重新构建流式显示。"""

        _clear_flow_widgets(self.chip_layout)
        for tag in self._tags:
            chip = SelectionChip(tag, tag, self.chip_container, editable=True)
            chip.set_read_only(self._read_only)
            chip.remove_requested.connect(self._remove_tag)
            chip.edit_committed.connect(self._edit_tag)
            self.chip_layout.addWidget(chip)
        self._schedule_chip_layout_refresh()

    def _schedule_chip_layout_refresh(self) -> None:
        """立即并在当前事件结束后各刷新一次标签布局。"""

        _refresh_flow_container(self.chip_container, self.chip_layout)
        QTimer.singleShot(0, self._refresh_chip_layout)

    def _refresh_chip_layout(self) -> None:
        """使用事件处理完成后的最终尺寸刷新标签布局。"""

        _refresh_flow_container(self.chip_container, self.chip_layout)


class OptionalPositiveIntegerField(QWidget):
    """编辑一个可标记为未知的正整数。"""

    value_changed = pyqtSignal()

    def __init__(self, unknown_text: str, parent: QWidget | None = None) -> None:
        """创建数值输入和未知复选框。"""

        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spin_box = SpinBox(self)
        self.spin_box.setRange(1, 999)
        self.unknown_check = CheckBox(unknown_text, self)
        self.spin_box.valueChanged.connect(lambda _value: self.value_changed.emit())
        self.unknown_check.stateChanged.connect(self._on_unknown_changed)
        layout.addWidget(self.spin_box)
        layout.addWidget(self.unknown_check)
        layout.addStretch(1)

    def set_value(self, value: int | None) -> None:
        """设置具体值或未知状态。"""

        unknown = value is None
        self.unknown_check.setChecked(unknown)
        if value is not None:
            self.spin_box.setValue(value)
        self.spin_box.setEnabled(not unknown)

    def value(self) -> int | None:
        """返回具体正整数或空值。"""

        return None if self.unknown_check.isChecked() else self.spin_box.value()

    def set_read_only(self, read_only: bool) -> None:
        """控制数值与未知状态是否可编辑。"""

        self.spin_box.setReadOnly(read_only)
        self.unknown_check.setEnabled(not read_only)

    def _on_unknown_changed(self, _state: int) -> None:
        """同步数值输入可用性并发送变化信号。"""

        self.spin_box.setEnabled(not self.unknown_check.isChecked())
        self.value_changed.emit()


class TimeRangeEditor(QWidget):
    """使用集数和集内时间点编辑剧情时间闭区间。"""

    value_changed = pyqtSignal()

    def __init__(self, allow_open_start: bool = False, parent: QWidget | None = None) -> None:
        """创建可选开放起点和持续有效终点的时间编辑器。"""

        super().__init__(parent)
        self._series_id: str | None = None
        self._preserved_start: int | None = None
        self._preserved_end: int | None = None
        self._allow_open_start = allow_open_start
        form = QFormLayout(self)
        _configure_form_layout(form)
        self.start_widget, self.start_episode, self.start_offset = self._create_coordinate_row()
        self.end_widget, self.end_episode, self.end_offset = self._create_coordinate_row()
        self.open_start_check = CheckBox(self.tr("不限制开始时间"), self)
        self.open_start_check.setVisible(allow_open_start)
        self.open_end_check = CheckBox(self.tr("持续有效"), self)
        form.addRow(BodyLabel(self.tr("开始"), self), self.start_widget)
        if allow_open_start:
            form.addRow("", self.open_start_check)
        form.addRow(BodyLabel(self.tr("结束"), self), self.end_widget)
        form.addRow("", self.open_end_check)
        for spin_box in (self.start_episode, self.start_offset, self.end_episode, self.end_offset):
            spin_box.valueChanged.connect(lambda _value: self.value_changed.emit())
        self.open_start_check.stateChanged.connect(self._update_enabled_state)
        self.open_end_check.stateChanged.connect(self._update_enabled_state)

    def _create_coordinate_row(self) -> tuple[QWidget, SpinBox, SpinBox]:
        """创建一行集数与集内时间点输入。"""

        widget = QWidget(self)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        episode = SpinBox(widget)
        episode.setRange(1, 999)
        offset = SpinBox(widget)
        offset.setRange(0, EPISODE_SLOT_COUNT - 1)
        layout.addWidget(BodyLabel(self.tr("第"), widget))
        layout.addWidget(episode)
        layout.addWidget(BodyLabel(self.tr("集，时间点"), widget))
        layout.addWidget(offset)
        layout.addStretch(1)
        return widget, episode, offset

    def set_series_id(self, series_id: str | None) -> None:
        """设置用于时间编码的单一系列，并尽量恢复已保存坐标。"""

        self._series_id = series_id or None
        if self._series_id is not None:
            self._apply_preserved_values()
        self._update_enabled_state()

    def change_series_id(self, series_id: str | None) -> None:
        """切换系列并保留当前界面上的集数与时间点。"""

        self._series_id = series_id or None
        self._update_enabled_state()

    def set_values(self, start: int | None, end: int | None) -> None:
        """加载可空开始时间与开放式结束时间。"""

        self._preserved_start = start
        self._preserved_end = end
        self.open_start_check.setChecked(start is None and self._allow_open_start)
        self.open_end_check.setChecked(is_open_ended_time(end))
        self._apply_preserved_values()
        self._update_enabled_state()

    def values(self) -> tuple[int | None, int | None]:
        """返回编码后的内部剧情时间闭区间。"""

        if self._series_id is None:
            return self._preserved_start, self._preserved_end
        start = None if self._allow_open_start and self.open_start_check.isChecked() else encode_story_time(
            self._series_id,
            StoryTimeCoordinate(self.start_episode.value(), self.start_offset.value()),
        )
        end = OPEN_ENDED_TIME if self.open_end_check.isChecked() else encode_story_time(
            self._series_id,
            StoryTimeCoordinate(self.end_episode.value(), self.end_offset.value()),
        )
        return start, end

    def set_read_only(self, read_only: bool) -> None:
        """控制时间区间是否可编辑。"""

        for spin_box in (self.start_episode, self.start_offset, self.end_episode, self.end_offset):
            spin_box.setReadOnly(read_only)
        self.open_start_check.setEnabled(not read_only)
        self.open_end_check.setEnabled(not read_only)

    def _apply_preserved_values(self) -> None:
        """把保存的内部坐标转换为当前系列的用户输入值。"""

        if self._series_id is None:
            return
        if self._preserved_start is not None:
            try:
                start = decode_story_time(self._series_id, self._preserved_start)
                self.start_episode.setValue(start.episode)
                self.start_offset.setValue(start.episode_offset)
            except ValueError:
                pass
        if self._preserved_end is not None and self._preserved_end != OPEN_ENDED_TIME:
            try:
                end = decode_story_time(self._series_id, self._preserved_end)
                self.end_episode.setValue(end.episode)
                self.end_offset.setValue(end.episode_offset)
            except ValueError:
                pass

    def _update_enabled_state(self, _state: int = 0) -> None:
        """同步开放选项和缺少单一系列时的输入可用性。"""

        has_series = self._series_id is not None
        self.start_widget.setEnabled(
            has_series and not (self._allow_open_start and self.open_start_check.isChecked())
        )
        self.end_widget.setEnabled(has_series and not self.open_end_check.isChecked())
        self.setToolTip("" if has_series else self.tr("选择单一适用系列后才能编辑具体时间"))
        self.value_changed.emit()


class CollapsibleCard(HeaderCardWidget):
    """点击 Fluent 卡片标题区域即可控制内容显隐。"""

    def __init__(self, title: str, expanded: bool, parent: QWidget | None = None) -> None:
        """创建指定初始展开状态的卡片。"""

        HeaderCardWidget.__init__(self, parent)
        self.setTitle(title)
        self._expanded = expanded
        self.toggle_button = TransparentToolButton(self)
        self.toggle_button.setFixedSize(32, 32)
        self.toggle_button.clicked.connect(self.toggle)
        self.headerLayout.addWidget(self.toggle_button)
        self.headerView.setCursor(Qt.PointingHandCursor)
        self.headerLabel.setCursor(Qt.PointingHandCursor)
        self.headerView.installEventFilter(self)
        self.headerLabel.installEventFilter(self)
        self.set_expanded(expanded)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """在鼠标左键释放于标题区域时切换展开状态。"""

        header_view = getattr(self, "headerView", None)
        header_label = getattr(self, "headerLabel", None)
        if watched in (header_view, header_label) and isinstance(event, QMouseEvent):
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.toggle()
                return True
        return super().eventFilter(watched, event)

    def set_expanded(self, expanded: bool) -> None:
        """设置内容可见性和标题图标。"""

        self._expanded = expanded
        self.view.setVisible(expanded)
        self.separator.setVisible(expanded)
        self.toggle_button.setIcon(
            FluentIcon.CARE_UP_SOLID if expanded else FluentIcon.CARE_DOWN_SOLID
        )

    def toggle(self) -> None:
        """切换卡片展开状态。"""

        self.set_expanded(not self._expanded)


class BaseEntryEditor(QWidget):
    """提供检索摘要提示和脏状态管理的类型编辑基类。"""

    dirty_changed = pyqtSignal(bool)

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建尚未加载条目的编辑器骨架。"""

        super().__init__(parent)
        self._registry = registry
        self._entry: WorldbookEntry | None = None
        self._loading = False
        self._dirty = False
        self._semantic_dirty = False
        self._read_only = False
        self._extension_mode = False
        self._editable_widgets: list[QWidget] = []
        self.extension_scope_card: HeaderCardWidget | None = None
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(12)

        self.retrieval_card = CollapsibleCard(self.tr("检索摘要"), False, self)
        retrieval_form = self._card_form(self.retrieval_card)
        self.retrieval_warning = CaptionLabel(
            self.tr("语义内容已经变化，请确认检索摘要仍然准确。"), self.retrieval_card
        )
        self.retrieval_warning.setWordWrap(True)
        self.retrieval_edit = TextEdit(self.retrieval_card)
        self.retrieval_edit.setMinimumHeight(100)
        self.use_basic_button = PushButton(self.tr("使用当前内容作为基础文本"), self.retrieval_card)
        self.retrieval_help_label = CaptionLabel(
            self.tr(
                "检索摘要用于帮助搜索找到这条内容。通常不需要修改；当标题、正文、观点或关系发生较大变化，且摘要已不能准确概括内容时再调整。"
            ),
            self.retrieval_card,
        )
        self.retrieval_help_label.setWordWrap(True)
        retrieval_form.addRow(self.retrieval_warning)
        retrieval_form.addRow(self.retrieval_edit)
        retrieval_form.addRow(self.use_basic_button)
        retrieval_form.addRow(self.retrieval_help_label)
        self.root_layout.addWidget(self.retrieval_card)
        self.retrieval_edit.textChanged.connect(self._mark_dirty)
        self.use_basic_button.clicked.connect(self._use_basic_retrieval_text)
        self._editable_widgets.extend([self.retrieval_edit, self.use_basic_button])

    def _create_card(self, title: str) -> HeaderCardWidget:
        """创建带垂直表单容器的 Fluent 卡片。"""

        card = HeaderCardWidget(title, self)
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        inner = QWidget(card)
        inner.setMinimumWidth(0)
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form = QFormLayout(inner)
        _configure_form_layout(form)
        card.viewLayout.addWidget(inner, 1)
        inner.setProperty("worldbookForm", True)
        return card

    def _card_form(self, card: HeaderCardWidget) -> QFormLayout:
        """返回卡片中唯一的表单布局。"""

        for child in card.view.findChildren(QWidget):
            if bool(child.property("worldbookForm")) and isinstance(child.layout(), QFormLayout):
                return child.layout()
        inner = QWidget(card)
        inner.setMinimumWidth(0)
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form = QFormLayout(inner)
        _configure_form_layout(form)
        inner.setProperty("worldbookForm", True)
        card.viewLayout.addWidget(inner, 1)
        return form

    def add_editor_card(self, card: HeaderCardWidget) -> None:
        """把类型专属卡片插入检索摘要卡片之前。"""

        index = self.root_layout.indexOf(self.retrieval_card)
        self.root_layout.insertWidget(index, card)

    def create_extension_scope_card(self) -> tuple[HeaderCardWidget, QFormLayout]:
        """创建仅供用户扩展显示且不可折叠的基础范围卡片。"""

        card = self._create_card(self.tr("基础范围"))
        card.hide()
        self.extension_scope_card = card
        self.add_editor_card(card)
        return card, self._card_form(card)

    def register_widget(self, widget: QWidget, semantic: bool) -> None:
        """注册可编辑控件并连接其变化信号。"""

        self._editable_widgets.append(widget)
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Expanding, widget.sizePolicy().verticalPolicy())
        callback = self._mark_semantic_dirty if semantic else self._mark_dirty
        if isinstance(widget, LineEdit):
            widget.textChanged.connect(callback)
        elif isinstance(widget, (TextEdit, PlainTextEdit)):
            widget.textChanged.connect(callback)
        elif isinstance(widget, ComboBox):
            widget.currentIndexChanged.connect(lambda _index: callback())
        elif isinstance(widget, SpinBox):
            widget.valueChanged.connect(lambda _value: callback())
        elif isinstance(widget, CheckBox):
            widget.stateChanged.connect(lambda _state: callback())
        elif isinstance(widget, TagEditor):
            widget.value_changed.connect(callback)
        elif isinstance(widget, MultiSelectField):
            widget.value_changed.connect(callback)
        elif isinstance(widget, OptionalPositiveIntegerField):
            widget.value_changed.connect(callback)
        elif isinstance(widget, TimeRangeEditor):
            widget.value_changed.connect(callback)

    def load_entry(
        self,
        entry: WorldbookEntry,
        read_only: bool,
        extension_mode: bool = False,
    ) -> None:
        """加载完整条目并重置脏状态和检索摘要提示。"""

        self._loading = True
        self._extension_mode = extension_mode
        self._entry = entry.model_copy(deep=True)
        self.retrieval_edit.setPlainText(_string_value(entry.content, "retrieval_text"))
        self.retrieval_warning.hide()
        self._load_fields(entry.content)
        self._semantic_dirty = False
        self._dirty = False
        self._loading = False
        if self.extension_scope_card is not None:
            self.extension_scope_card.setVisible(extension_mode)
        self.set_read_only(read_only)
        self.dirty_changed.emit(False)

    def set_available_entries(self, entries: list[WorldbookEntry]) -> None:
        """提供跨条目选择所需的当前依赖闭包内容。"""

        self._set_available_entries(entries)

    def content(self) -> dict[str, object]:
        """返回保留隐藏字段并应用表单值的完整 content。"""

        if self._entry is None:
            raise ValueError("编辑器尚未加载条目")
        content = dict(self._entry.content)
        content.update(self._field_content())
        content["retrieval_text"] = self.retrieval_edit.toPlainText().strip()
        return content

    def candidate_entry(self) -> WorldbookEntry:
        """构造保留 envelope 身份的当前候选条目。"""

        if self._entry is None:
            raise ValueError("编辑器尚未加载条目")
        return WorldbookEntry(
            entry_id=self._entry.entry_id,
            entry_type=self._entry.entry_type,
            schema_version=self._entry.schema_version,
            content=self.content(),
        )

    def is_dirty(self) -> bool:
        """返回当前表单是否有未保存变化。"""

        return self._dirty

    def mark_saved(self) -> None:
        """在外部持久化成功后清除脏状态和复核提醒。"""

        self._loading = True
        self._dirty = False
        self._semantic_dirty = False
        self.retrieval_warning.hide()
        self._loading = False
        self.dirty_changed.emit(False)

    def retrieval_reviewed(self) -> bool:
        """返回界面已通过展开提醒完成检索摘要复核提示。"""

        return True

    def set_read_only(self, read_only: bool) -> None:
        """控制类型表单和检索摘要是否允许修改。"""

        self._read_only = read_only
        for widget in self._editable_widgets:
            if isinstance(widget, (LineEdit, TextEdit, PlainTextEdit, SpinBox)):
                widget.setReadOnly(read_only)
            elif isinstance(widget, (TagEditor, MultiSelectField, OptionalPositiveIntegerField, TimeRangeEditor)):
                widget.set_read_only(read_only)
            else:
                widget.setEnabled(not read_only)

    def reveal_validation_fields(self) -> None:
        """展开全部折叠卡片，便于用户定位保存校验错误。"""

        for card in self.findChildren(CollapsibleCard):
            card.set_expanded(True)

    def validate_form(self) -> str | None:
        """返回面向用户的汇总校验提示，并聚焦第一个问题字段。"""

        issues = self._validation_issues()
        if not issues:
            return None
        self.reveal_validation_fields()
        issues[0].focus_widget.setFocus(Qt.OtherFocusReason)
        return self.tr("请完善以下内容：") + "；".join(
            issue.message for issue in issues
        ) + "。"

    def _validation_issues(self) -> list[FormValidationIssue]:
        """返回当前类型编辑器需要用户处理的表单问题。"""

        return []

    def _mark_dirty(self) -> None:
        """在非加载阶段记录普通未保存变化。"""

        if self._loading or self._read_only:
            return
        if not self._dirty:
            self._dirty = True
            self.dirty_changed.emit(True)

    def _mark_semantic_dirty(self) -> None:
        """记录语义变化并展开检索摘要供用户按需检查。"""

        if self._loading or self._read_only:
            return
        self._semantic_dirty = True
        self.retrieval_warning.show()
        self.retrieval_card.set_expanded(True)
        self._mark_dirty()

    def _use_basic_retrieval_text(self) -> None:
        """使用 Type Module 的透明基础文本替换当前检索摘要。"""

        if self._read_only:
            return
        try:
            text = self._registry.module(self.candidate_entry().entry_type).basic_retrieval_text(
                self.candidate_entry()
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.retrieval_warning.setText(self.tr(f"无法生成基础文本：{exc}"))
            self.retrieval_warning.show()
            return
        self.retrieval_edit.setPlainText(text)

    def _load_fields(self, content: dict[str, object]) -> None:
        """由子类把 content 加载到类型专属控件。"""

        raise NotImplementedError

    def _field_content(self) -> dict[str, object]:
        """由子类返回类型专属字段值。"""

        raise NotImplementedError

    def _set_available_entries(self, entries: list[WorldbookEntry]) -> None:
        """允许子类接收跨条目选择上下文。"""


class StoryEventEditor(BaseEntryEditor):
    """编辑 Story Event 的普通内容和高级时间字段。"""

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建 Story Event 表单。"""

        super().__init__(registry, parent)
        scope_card, scope = self.create_extension_scope_card()
        self.extension_series_combo = _series_combo(scope_card)
        self.extension_canon_combo = _canon_combo(scope_card)
        self.extension_timeline_label = CaptionLabel(scope_card)
        scope.addRow(BodyLabel(self.tr("系列"), scope_card), self.extension_series_combo)
        scope.addRow(BodyLabel(self.tr("剧情分支"), scope_card), self.extension_canon_combo)
        scope.addRow(BodyLabel(self.tr("剧情时间线"), scope_card), self.extension_timeline_label)
        normal_card = self._create_card(self.tr("剧情事件"))
        normal = self._card_form(normal_card)
        self.title_edit = LineEdit(self)
        self.summary_edit = TextEdit(self)
        self.summary_edit.setMinimumHeight(120)
        self.participants_field = MultiSelectField(self.tr("选择参与角色"), self)
        self.participants_field.set_options(_character_options())
        self.tags_field = TagEditor(self)
        normal.addRow(BodyLabel(self.tr("标题"), self), self.title_edit)
        normal.addRow(BodyLabel(self.tr("摘要"), self), self.summary_edit)
        normal.addRow(BodyLabel(self.tr("参与角色"), self), self.participants_field)
        normal.addRow(BodyLabel(self.tr("标签（必填）"), self), self.tags_field)
        self.add_editor_card(normal_card)
        advanced = CollapsibleCard(self.tr("高级设置"), False, self)
        advanced_form = self._card_form(advanced)
        self.story_year_field = OptionalPositiveIntegerField(self.tr("未知"), self)
        self.time_range_field = TimeRangeEditor(False, self)
        advanced_form.addRow(BodyLabel(self.tr("发生学年"), self), self.story_year_field)
        advanced_form.addRow(BodyLabel(self.tr("剧情时间"), self), self.time_range_field)
        self.add_editor_card(advanced)
        self.extension_series_combo.currentIndexChanged.connect(
            lambda _index: self.time_range_field.change_series_id(
                _combo_text(self.extension_series_combo)
            )
        )
        for widget, semantic in (
            (self.extension_series_combo, False),
            (self.extension_canon_combo, False),
            (self.title_edit, True),
            (self.summary_edit, True),
            (self.participants_field, True),
            (self.tags_field, False),
            (self.story_year_field, False),
            (self.time_range_field, False),
        ):
            self.register_widget(widget, semantic)

    def _load_fields(self, content: dict[str, object]) -> None:
        """加载剧情事件字段。"""

        _select_combo_value(self.extension_series_combo, _string_value(content, "series_id"))
        _select_combo_value(
            self.extension_canon_combo, _string_value(content, "canon_branch")
        )
        self.extension_timeline_label.setText(self.tr("使用当前世界书的剧情时间线"))
        self.title_edit.setText(_string_value(content, "title"))
        self.summary_edit.setPlainText(_string_value(content, "summary"))
        self.participants_field.set_value(_string_list(content, "participants"))
        self.tags_field.set_value(_string_list(content, "tags"))
        self.story_year_field.set_value(_optional_integer(content, "occurred_story_year"))
        series_id = _string_value(content, "series_id")
        self.time_range_field.set_series_id(series_id)
        self.time_range_field.set_values(
            _optional_integer(content, "visible_from"),
            _optional_integer(content, "visible_to"),
        )

    def _field_content(self) -> dict[str, object]:
        """返回剧情事件可编辑字段并同步事件时间身份。"""

        start, end = self.time_range_field.values()
        if start is None or end is None:
            raise ValueError("剧情事件必须具有完整可见时间区间")
        series_id = _combo_text(self.extension_series_combo)
        coordinate = decode_story_time(series_id, start)
        return {
            "series_id": series_id,
            "canon_branch": _combo_text(self.extension_canon_combo),
            "title": self.title_edit.text().strip(),
            "summary": self.summary_edit.toPlainText().strip(),
            "participants": self.participants_field.value(),
            "tags": self.tags_field.value(),
            "occurred_story_year": self.story_year_field.value(),
            "episode": coordinate.episode,
            "time_order": start,
            "visible_from": start,
            "visible_to": end,
        }

    def _validation_issues(self) -> list[FormValidationIssue]:
        """返回剧情事件缺少的必填内容。"""

        issues: list[FormValidationIssue] = []
        if not self.title_edit.text().strip():
            issues.append(FormValidationIssue(self.tr("事件标题"), self.title_edit))
        if not self.summary_edit.toPlainText().strip():
            issues.append(FormValidationIssue(self.tr("事件摘要"), self.summary_edit))
        if not self.participants_field.value():
            issues.append(
                FormValidationIssue(
                    self.tr("至少一名参与角色"),
                    self.participants_field.select_button,
                )
            )
        if not self.tags_field.value():
            issues.append(
                FormValidationIssue(self.tr("至少一个标签"), self.tags_field.input_edit)
            )
        return issues

    def content_base(self) -> dict[str, object]:
        """返回不经过表单递归构造的原始 content。"""

        if self._entry is None:
            raise ValueError("编辑器尚未加载条目")
        return self._entry.content


class CharacterThoughtEditor(BaseEntryEditor):
    """编辑 Character Thought 内容、认知状态与事件引用。"""

    _STATUS_LABELS: tuple[tuple[str, str], ...] = (
        ("knows", "确知"),
        ("believes", "相信"),
        ("suspects", "怀疑"),
        ("uncertain", "不确定"),
        ("rejects", "否认"),
    )

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建 Character Thought 表单。"""

        super().__init__(registry, parent)
        self._available_context_entries: list[WorldbookEntry] = []
        scope_card, scope = self.create_extension_scope_card()
        self.extension_character_combo = _character_combo(scope_card)
        self.extension_series_combo = _series_combo(scope_card)
        self.extension_canon_combo = _canon_combo(scope_card)
        self.extension_timeline_label = CaptionLabel(scope_card)
        scope.addRow(
            BodyLabel(self.tr("所属角色"), scope_card), self.extension_character_combo
        )
        scope.addRow(BodyLabel(self.tr("系列"), scope_card), self.extension_series_combo)
        scope.addRow(BodyLabel(self.tr("剧情分支"), scope_card), self.extension_canon_combo)
        scope.addRow(BodyLabel(self.tr("剧情时间线"), scope_card), self.extension_timeline_label)
        normal_card = self._create_card(self.tr("角色想法"))
        normal = self._card_form(normal_card)
        self.subject_edit = LineEdit(self)
        self.aspect_edit = LineEdit(self)
        self.thought_edit = TextEdit(self)
        self.thought_edit.setMinimumHeight(120)
        self.status_combo = ComboBox(self)
        for key, label in self._STATUS_LABELS:
            self.status_combo.addItem(label, userData=key)
        self.tags_field = TagEditor(self)
        normal.addRow(BodyLabel(self.tr("规范化主题"), self), self.subject_edit)
        normal.addRow(BodyLabel(self.tr("认知方面"), self), self.aspect_edit)
        normal.addRow(BodyLabel(self.tr("观点正文"), self), self.thought_edit)
        normal.addRow(BodyLabel(self.tr("认知状态"), self), self.status_combo)
        normal.addRow(BodyLabel(self.tr("标签（可选）"), self), self.tags_field)
        self.add_editor_card(normal_card)
        advanced = CollapsibleCard(self.tr("高级设置"), False, self)
        advanced_form = self._card_form(advanced)
        self.event_field = MultiSelectField(self.tr("选择关联剧情事件"), self)
        self.event_warning = CaptionLabel(
            self.tr("已有的关联剧情事件与当前系列或剧情分支不兼容，请移除后重新选择。"),
            advanced,
        )
        self.event_warning.setWordWrap(True)
        self.event_warning.hide()
        self.time_range_field = TimeRangeEditor(False, self)
        advanced_form.addRow(BodyLabel(self.tr("关联剧情事件"), self), self.event_field)
        advanced_form.addRow(self.event_warning)
        advanced_form.addRow(BodyLabel(self.tr("可见区间"), self), self.time_range_field)
        self.add_editor_card(advanced)
        self.extension_series_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_extension_context()
        )
        self.extension_canon_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_extension_context()
        )
        self.event_field.value_changed.connect(self._update_event_warning)
        for widget, semantic in (
            (self.extension_character_combo, False),
            (self.extension_series_combo, False),
            (self.extension_canon_combo, False),
            (self.subject_edit, True),
            (self.aspect_edit, True),
            (self.thought_edit, True),
            (self.status_combo, True),
            (self.tags_field, False),
            (self.event_field, False),
            (self.time_range_field, False),
        ):
            self.register_widget(widget, semantic)

    def _load_fields(self, content: dict[str, object]) -> None:
        """加载角色想法字段。"""

        _select_combo_value(
            self.extension_character_combo, _string_value(content, "character_id")
        )
        _select_combo_value(self.extension_series_combo, _string_value(content, "series_id"))
        _select_combo_value(
            self.extension_canon_combo, _string_value(content, "canon_branch")
        )
        self.extension_timeline_label.setText(self.tr("使用当前世界书的剧情时间线"))
        self.subject_edit.setText(_string_value(content, "canonical_subject"))
        self.aspect_edit.setText(_string_value(content, "thought_aspect"))
        self.thought_edit.setPlainText(_string_value(content, "thought_text"))
        self.status_combo.setCurrentIndex(
            max(0, self.status_combo.findData(_string_value(content, "epistemic_status")))
        )
        self.tags_field.set_value(_string_list(content, "tags"))
        self.event_field.set_value(_string_list(content, "story_event_entry_ids"))
        self.time_range_field.set_series_id(_string_value(content, "series_id"))
        self.time_range_field.set_values(
            _optional_integer(content, "visible_from"),
            _optional_integer(content, "visible_to"),
        )

    def _set_available_entries(self, entries: list[WorldbookEntry]) -> None:
        """把范围相容的剧情事件提供给引用选择器。"""

        self._available_context_entries = entries
        if self._entry is None:
            return
        content = self._entry.content
        series_id = (
            _combo_text(self.extension_series_combo)
            if self._extension_mode
            else _string_value(content, "series_id")
        )
        canon_branch = (
            _combo_text(self.extension_canon_combo)
            if self._extension_mode
            else _string_value(content, "canon_branch")
        )
        options: list[SelectionOption] = []
        for entry in entries:
            if entry.entry_type != "story_event":
                continue
            if (
                entry.content.get("series_id") != series_id
                or entry.content.get("timeline_id") != content.get("timeline_id")
                or entry.content.get("canon_branch") != canon_branch
            ):
                continue
            title = _string_value(entry.content, "title") or str(entry.entry_id)
            episode = _optional_integer(entry.content, "episode")
            prefix = f"第 {episode} 集 · " if episode is not None else ""
            options.append(
                SelectionOption(str(entry.entry_id), f"{prefix}{title}", _string_value(entry.content, "summary"))
            )
        self.event_field.set_options(options)
        self._update_event_warning()

    def _field_content(self) -> dict[str, object]:
        """返回角色想法可编辑字段。"""

        start, end = self.time_range_field.values()
        if start is None or end is None:
            raise ValueError("角色想法必须具有完整可见时间区间")
        return {
            "character_id": _combo_text(self.extension_character_combo),
            "series_id": _combo_text(self.extension_series_combo),
            "canon_branch": _combo_text(self.extension_canon_combo),
            "canonical_subject": self.subject_edit.text().strip(),
            "thought_aspect": self.aspect_edit.text().strip(),
            "thought_text": self.thought_edit.toPlainText().strip(),
            "epistemic_status": str(self.status_combo.currentData()),
            "tags": self.tags_field.value(),
            "story_event_entry_ids": self.event_field.value(),
            "visible_from": start,
            "visible_to": end,
        }

    def _validation_issues(self) -> list[FormValidationIssue]:
        """返回角色想法缺少的必填内容。"""

        issues: list[FormValidationIssue] = []
        if not self.subject_edit.text().strip():
            issues.append(FormValidationIssue(self.tr("规范化主题"), self.subject_edit))
        if not self.aspect_edit.text().strip():
            issues.append(FormValidationIssue(self.tr("认知方面"), self.aspect_edit))
        if not self.thought_edit.toPlainText().strip():
            issues.append(FormValidationIssue(self.tr("观点正文"), self.thought_edit))
        return issues

    def _refresh_extension_context(self) -> None:
        """保留时间坐标并刷新当前范围可选的剧情事件。"""

        if not self._extension_mode:
            return
        self.time_range_field.change_series_id(_combo_text(self.extension_series_combo))
        self._set_available_entries(self._available_context_entries)

    def _update_event_warning(self) -> None:
        """提示仍保留但已不属于当前范围的剧情事件引用。"""

        compatible = self.event_field.option_keys()
        incompatible = set(self.event_field.value()) - compatible
        self.event_warning.setVisible(self._extension_mode and bool(incompatible))


class CharacterRelationEditor(BaseEntryEditor):
    """编辑 Character Relation 的关系摘要、说话方式与称呼。"""

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建 Character Relation 表单。"""

        super().__init__(registry, parent)
        scope_card, scope = self.create_extension_scope_card()
        self.extension_subject_combo = _character_combo(scope_card)
        self.extension_object_combo = _character_combo(scope_card)
        self.extension_series_combo = _series_combo(scope_card)
        self.extension_canon_combo = _canon_combo(scope_card)
        self.extension_timeline_label = CaptionLabel(scope_card)
        scope.addRow(
            BodyLabel(self.tr("主体角色"), scope_card), self.extension_subject_combo
        )
        scope.addRow(
            BodyLabel(self.tr("目标角色"), scope_card), self.extension_object_combo
        )
        scope.addRow(BodyLabel(self.tr("系列"), scope_card), self.extension_series_combo)
        scope.addRow(BodyLabel(self.tr("剧情分支"), scope_card), self.extension_canon_combo)
        scope.addRow(BodyLabel(self.tr("剧情时间线"), scope_card), self.extension_timeline_label)
        normal_card = self._create_card(self.tr("角色关系"))
        normal = self._card_form(normal_card)
        self.summary_edit = TextEdit(self)
        self.summary_edit.setMinimumHeight(120)
        self.speech_edit = TextEdit(self)
        self.speech_edit.setMinimumHeight(80)
        self.nickname_edit = LineEdit(self)
        self.tags_field = TagEditor(self)
        normal.addRow(BodyLabel(self.tr("关系状态摘要"), self), self.summary_edit)
        normal.addRow(BodyLabel(self.tr("说话方式"), self), self.speech_edit)
        normal.addRow(BodyLabel(self.tr("目标角色称呼"), self), self.nickname_edit)
        normal.addRow(BodyLabel(self.tr("标签（可选）"), self), self.tags_field)
        self.add_editor_card(normal_card)
        advanced = CollapsibleCard(self.tr("高级设置"), False, self)
        advanced_form = self._card_form(advanced)
        self.time_range_field = TimeRangeEditor(False, self)
        advanced_form.addRow(BodyLabel(self.tr("可见区间"), self), self.time_range_field)
        self.add_editor_card(advanced)
        self.extension_series_combo.currentIndexChanged.connect(
            lambda _index: self.time_range_field.change_series_id(
                _combo_text(self.extension_series_combo)
            )
        )
        for widget, semantic in (
            (self.extension_subject_combo, False),
            (self.extension_object_combo, False),
            (self.extension_series_combo, False),
            (self.extension_canon_combo, False),
            (self.summary_edit, True),
            (self.speech_edit, True),
            (self.nickname_edit, True),
            (self.tags_field, False),
            (self.time_range_field, False),
        ):
            self.register_widget(widget, semantic)

    def _load_fields(self, content: dict[str, object]) -> None:
        """加载角色关系字段。"""

        _select_combo_value(
            self.extension_subject_combo,
            _string_value(content, "subject_character_id"),
        )
        _select_combo_value(
            self.extension_object_combo,
            _string_value(content, "object_character_id"),
        )
        _select_combo_value(self.extension_series_combo, _string_value(content, "series_id"))
        _select_combo_value(
            self.extension_canon_combo, _string_value(content, "canon_branch")
        )
        self.extension_timeline_label.setText(self.tr("使用当前世界书的剧情时间线"))
        self.summary_edit.setPlainText(_string_value(content, "state_summary"))
        self.speech_edit.setPlainText(_string_value(content, "speech_hint"))
        self.nickname_edit.setText(_string_value(content, "object_character_nickname"))
        self.tags_field.set_value(_string_list(content, "tags"))
        self.time_range_field.set_series_id(_string_value(content, "series_id"))
        self.time_range_field.set_values(
            _optional_integer(content, "visible_from"),
            _optional_integer(content, "visible_to"),
        )

    def _field_content(self) -> dict[str, object]:
        """返回角色关系可编辑字段。"""

        start, end = self.time_range_field.values()
        if start is None or end is None:
            raise ValueError("角色关系必须具有完整可见时间区间")
        return {
            "subject_character_id": _combo_text(self.extension_subject_combo),
            "object_character_id": _combo_text(self.extension_object_combo),
            "series_id": _combo_text(self.extension_series_combo),
            "canon_branch": _combo_text(self.extension_canon_combo),
            "state_summary": self.summary_edit.toPlainText().strip(),
            "speech_hint": self.speech_edit.toPlainText().strip(),
            "object_character_nickname": self.nickname_edit.text().strip(),
            "tags": self.tags_field.value(),
            "visible_from": start,
            "visible_to": end,
        }

    def _validation_issues(self) -> list[FormValidationIssue]:
        """返回角色关系需要用户处理的表单问题。"""

        issues: list[FormValidationIssue] = []
        if not self.summary_edit.toPlainText().strip():
            issues.append(
                FormValidationIssue(self.tr("关系状态摘要"), self.summary_edit)
            )
        if _combo_text(self.extension_subject_combo) == _combo_text(
            self.extension_object_combo
        ):
            issues.append(
                FormValidationIssue(
                    self.tr("主体角色和目标角色不能相同"),
                    self.extension_object_combo,
                )
            )
        return issues


class LoreEntryEditor(BaseEntryEditor):
    """编辑 Lore Entry 的正文、适用范围和时间限制。"""

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建 Lore Entry 表单。"""

        super().__init__(registry, parent)
        scope_card, extension_scope = self.create_extension_scope_card()
        self.extension_canon_combo = _canon_combo(scope_card)
        self.extension_timeline_label = CaptionLabel(scope_card)
        extension_scope.addRow(
            BodyLabel(self.tr("剧情分支"), scope_card), self.extension_canon_combo
        )
        extension_scope.addRow(
            BodyLabel(self.tr("剧情时间线"), scope_card), self.extension_timeline_label
        )
        normal_card = self._create_card(self.tr("名词解释"))
        normal = self._card_form(normal_card)
        self.title_edit = LineEdit(self)
        self.content_edit = TextEdit(self)
        self.content_edit.setMinimumHeight(160)
        self.tags_field = TagEditor(self)
        normal.addRow(BodyLabel(self.tr("标题"), self), self.title_edit)
        normal.addRow(BodyLabel(self.tr("正文"), self), self.content_edit)
        normal.addRow(BodyLabel(self.tr("标签（必填）"), self), self.tags_field)
        self.add_editor_card(normal_card)
        advanced = CollapsibleCard(self.tr("高级设置"), False, self)
        advanced_form = self._card_form(advanced)
        self.scope_combo = ComboBox(self)
        self.scope_combo.addItem(self.tr("适用于指定系列"), userData="series")
        self.scope_combo.addItem(self.tr("适用于整个世界书包"), userData="global")
        self.series_field = MultiSelectField(self.tr("选择适用系列"), self)
        self.series_field.set_options(_series_options())
        self.story_years_edit = LineEdit(self)
        self.story_years_edit.setPlaceholderText(self.tr("留空表示不限制；多个学年用逗号分隔"))
        self.time_range_field = TimeRangeEditor(True, self)
        advanced_form.addRow(BodyLabel(self.tr("适用范围"), self), self.scope_combo)
        advanced_form.addRow(BodyLabel(self.tr("适用系列"), self), self.series_field)
        advanced_form.addRow(BodyLabel(self.tr("适用学年"), self), self.story_years_edit)
        advanced_form.addRow(BodyLabel(self.tr("可见区间"), self), self.time_range_field)
        self.add_editor_card(advanced)
        self.series_field.value_changed.connect(self._sync_lore_time_series)
        self.scope_combo.currentIndexChanged.connect(lambda _index: self._sync_scope_state())
        for widget, semantic in (
            (self.extension_canon_combo, False),
            (self.title_edit, True),
            (self.content_edit, True),
            (self.tags_field, False),
            (self.scope_combo, False),
            (self.series_field, False),
            (self.story_years_edit, False),
            (self.time_range_field, False),
        ):
            self.register_widget(widget, semantic)

    def _load_fields(self, content: dict[str, object]) -> None:
        """加载名词解释字段。"""

        _select_combo_value(
            self.extension_canon_combo, _string_value(content, "canon_branch")
        )
        self.extension_timeline_label.setText(self.tr("使用当前世界书的剧情时间线"))
        self.title_edit.setText(_string_value(content, "title"))
        self.content_edit.setPlainText(_string_value(content, "content"))
        self.tags_field.set_value(_string_list(content, "tags"))
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(_string_value(content, "scope_type"))))
        self.series_field.set_value(_string_list(content, "series_ids"))
        years = _integer_list(content, "applicable_story_years")
        self.story_years_edit.setText(", ".join(str(year) for year in years))
        self.time_range_field.set_values(
            _optional_integer(content, "visible_from"),
            _optional_integer(content, "visible_to"),
        )
        self._sync_scope_state()
        self._sync_lore_time_series()

    def _field_content(self) -> dict[str, object]:
        """返回名词解释可编辑字段。"""

        raw_years = self.story_years_edit.text().replace("，", ",").strip()
        years = None if not raw_years else list(
            dict.fromkeys(int(item.strip()) for item in raw_years.split(",") if item.strip())
        )
        start, end = self.time_range_field.values()
        scope_type = str(self.scope_combo.currentData())
        series_ids = self.series_field.value() if scope_type == "series" else []
        return {
            "canon_branch": _combo_text(self.extension_canon_combo),
            "title": self.title_edit.text().strip(),
            "content": self.content_edit.toPlainText().strip(),
            "tags": self.tags_field.value(),
            "scope_type": scope_type,
            "series_ids": series_ids or None,
            "applicable_story_years": years,
            "visible_from": start,
            "visible_to": None if end == OPEN_ENDED_TIME else end,
        }

    def _validation_issues(self) -> list[FormValidationIssue]:
        """返回名词解释缺少的必填内容。"""

        issues: list[FormValidationIssue] = []
        if not self.title_edit.text().strip():
            issues.append(FormValidationIssue(self.tr("条目标题"), self.title_edit))
        if not self.content_edit.toPlainText().strip():
            issues.append(FormValidationIssue(self.tr("条目正文"), self.content_edit))
        if not self.tags_field.value():
            issues.append(
                FormValidationIssue(self.tr("至少一个标签"), self.tags_field.input_edit)
            )
        if self.scope_combo.currentData() == "series" and not self.series_field.value():
            issues.append(
                FormValidationIssue(
                    self.tr("至少一个适用系列"),
                    self.series_field.select_button,
                )
            )
        return issues

    def _sync_scope_state(self) -> None:
        """全包范围时隐藏不适用的系列选择。"""

        self.series_field.setVisible(self.scope_combo.currentData() == "series")
        self._sync_lore_time_series()

    def _sync_lore_time_series(self) -> None:
        """仅在单一系列时开放可逆的剧情时间坐标编辑。"""

        series_ids = self.series_field.value() if self.scope_combo.currentData() == "series" else []
        self.time_range_field.set_series_id(series_ids[0] if len(series_ids) == 1 else None)


class EntryEditorHost(QWidget):
    """按 entry_type 创建并托管当前类型化编辑器。"""

    dirty_changed = pyqtSignal(bool)

    def __init__(self, registry: AdapterRegistry, parent: QWidget | None = None) -> None:
        """创建空编辑器宿主。"""

        super().__init__(parent)
        self._registry = registry
        self._editor: BaseEntryEditor | None = None
        self._available_entries: list[WorldbookEntry] = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSizeConstraint(QLayout.SetMinimumSize)

    def set_available_entries(self, entries: list[WorldbookEntry]) -> None:
        """保存依赖闭包条目并更新当前跨条目选择器。"""

        self._available_entries = entries
        if self._editor is not None:
            self._editor.set_available_entries(entries)

    def load_entry(
        self,
        entry: WorldbookEntry,
        read_only: bool,
        extension_mode: bool = False,
    ) -> None:
        """为指定条目创建正确表单并加载内容。"""

        if self._editor is not None:
            self.layout.removeWidget(self._editor)
            self._editor.hide()
            self._editor.deleteLater()
        editor_class = _EDITOR_CLASSES[entry.entry_type]
        self._editor = editor_class(self._registry, self)
        self._editor.load_entry(entry, read_only, extension_mode)
        self._editor.set_available_entries(self._available_entries)
        self._editor.dirty_changed.connect(self.dirty_changed)
        self.layout.addWidget(self._editor)

    def editor(self) -> BaseEntryEditor:
        """返回当前类型编辑器。"""

        if self._editor is None:
            raise ValueError("尚未选择世界书条目")
        return self._editor

    def clear(self) -> None:
        """移除当前表单。"""

        if self._editor is not None:
            self.layout.removeWidget(self._editor)
            self._editor.hide()
            self._editor.deleteLater()
            self._editor = None


_EDITOR_CLASSES: dict[EntryType, type[BaseEntryEditor]] = {
    "story_event": StoryEventEditor,
    "character_thought": CharacterThoughtEditor,
    "character_relation": CharacterRelationEditor,
    "lore_entry": LoreEntryEditor,
}


def _character_combo(parent: QWidget) -> ComboBox:
    """创建要求用户选择单个角色的 Fluent ComboBox。"""

    combo = ComboBox(parent)
    combo.setPlaceholderText(combo.tr("请选择角色"))
    for option in _character_options():
        combo.addItem(option.label, userData=option.key)
    combo.setCurrentIndex(-1)
    return combo


def _series_combo(parent: QWidget) -> ComboBox:
    """创建要求用户选择单个作品系列的 Fluent ComboBox。"""

    combo = ComboBox(parent)
    combo.setPlaceholderText(combo.tr("请选择系列"))
    for option in _series_options():
        combo.addItem(option.label, userData=option.key)
    combo.setCurrentIndex(-1)
    return combo


def _canon_combo(parent: QWidget) -> ComboBox:
    """创建剧情分支 Fluent ComboBox。"""

    labels: dict[CanonBranch, str] = {
        CanonBranch.MAIN: "动画主线",
        CanonBranch.GAME: "手游补充剧情",
    }
    combo = ComboBox(parent)
    combo.setPlaceholderText(combo.tr("请选择剧情分支"))
    for branch in CanonBranch:
        combo.addItem(labels[branch], userData=branch.value)
    combo.setCurrentIndex(-1)
    return combo


def _select_combo_value(combo: ComboBox, value: str) -> None:
    """按稳定值选择 ComboBox，不存在时保持未选择。"""

    combo.setCurrentIndex(combo.findData(value))


def _combo_text(combo: ComboBox) -> str:
    """返回 ComboBox 当前稳定字符串值。"""

    value = combo.currentData()
    return value if isinstance(value, str) else ""


def _character_options() -> list[SelectionOption]:
    """返回按角色乐队及固定团内顺序排列的中文角色选项。"""

    return [
        SelectionOption(
            character.value,
            character.common_name,
            character.value,
            band.value,
            band.display_name,
        )
        for band in BandId
        for character in BAND_MEMBERS[band]
    ]


def _series_options() -> list[SelectionOption]:
    """返回世界书支持的作品系列选项。"""

    labels: dict[SeriesId, str] = {
        SeriesId.BANG_DREAM_1: "BanG Dream! 第一季",
        SeriesId.BANG_DREAM_2: "BanG Dream! 第二季",
        SeriesId.BANG_DREAM_3: "BanG Dream! 第三季",
        SeriesId.ITS_MYGO: "BanG Dream! It’s MyGO!!!!!",
        SeriesId.AVE_MUJICA: "BanG Dream! Ave Mujica",
    }
    return [SelectionOption(series.value, labels[series], series.value) for series in SeriesId]


def _string_value(content: dict[str, object], key: str) -> str:
    """安全读取字符串字段。"""

    value = content.get(key)
    return value if isinstance(value, str) else ""


def _optional_integer(content: dict[str, object], key: str) -> int | None:
    """安全读取可空整数字段并排除布尔值。"""

    value = content.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_list(content: dict[str, object], key: str) -> list[str]:
    """安全读取字符串列表。"""

    value = content.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _integer_list(content: dict[str, object], key: str) -> list[int]:
    """安全读取整数列表并排除布尔值。"""

    value = content.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
