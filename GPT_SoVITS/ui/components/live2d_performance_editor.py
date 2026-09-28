"""以资源浏览和预览为中心的独立演出编辑界面。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)
from live2d_support.performance_catalog import (
    PerformanceCatalog, load_performance_catalog, object_mapping,
    performance_config_path, read_config, save_config, shared_config_path,
)


def confirm_description_changes(parent: QWidget, save: Callable[[], bool]) -> bool:
    """离开编辑上下文前允许保存、放弃或取消。"""
    box = QMessageBox(QMessageBox.Question, "保存修改？", "还有未保存的修改。", parent=parent)
    box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
    box.setDefaultButton(QMessageBox.Save)
    box.button(QMessageBox.Save).setText("保存")
    box.button(QMessageBox.Discard).setText("放弃修改")
    box.button(QMessageBox.Cancel).setText("取消")
    result = box.exec_()
    return save() if result == QMessageBox.Save else result == QMessageBox.Discard


class Live2DPerformanceEditor(QWidget):
    """浏览两类资源、保留逐项草稿，并将次要管理操作放入对话框。"""

    previewFeedback = pyqtSignal(str)

    def __init__(self, send_preview: Callable[[dict[str, object]], object], parent: QWidget | None = None) -> None:
        """建立双列表、说明编辑区和突出显示的重播操作。"""
        super().__init__(parent)
        self.send_preview = send_preview
        self.catalog: PerformanceCatalog | None = None
        self.selectors: dict[str, QListWidget] = {}
        self.descriptions: dict[str, QTextEdit] = {}
        self.searches: dict[str, QLineEdit] = {}
        self.drafts: dict[tuple[str, str], str] = {}
        self.direction = "C"
        self._loading = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(650)
        self.autosave_timer.timeout.connect(self.save_descriptions)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        hint = QLabel("AI 分别选择肢体动作和脸部表情")
        hint.setObjectName("muted")
        heading.addWidget(hint)
        heading.addStretch()
        self.presets_button = QPushButton("组合预设…")
        self.presets_button.clicked.connect(self.open_presets)
        heading.addWidget(self.presets_button)
        help_button = QToolButton()
        help_button.setText("？")
        help_button.setObjectName("quiet")
        help_button.setAccessibleName("组合预设说明")
        help_button.setToolTip("设置你喜欢的表情与动作组合，让 AI 在演出时选择对应搭配")
        heading.addWidget(help_button)
        layout.addLayout(heading)
        columns = QSplitter(Qt.Horizontal)
        for kind, title, description_title in (
            ("motions", "动作", "给 AI 的动作描述"),
            ("expressions", "表情", "给 AI 的表情描述"),
        ):
            panel = QFrame()
            panel.setObjectName("resourcePanel")
            column = QVBoxLayout(panel)
            column.setContentsMargins(12, 12, 12, 12)
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            column.addWidget(label)
            search = QLineEdit()
            search.setPlaceholderText("搜索名称或说明")
            search.setClearButtonEnabled(True)
            search.textChanged.connect(lambda text, key=kind: self.filter_resources(key, text))
            self.searches[kind] = search
            column.addWidget(search)
            selector = QListWidget()
            selector.setMinimumHeight(140)
            selector.setAccessibleName(title)
            selector.currentItemChanged.connect(lambda _current, _previous, key=kind: self._show_description(key))
            selector.itemClicked.connect(lambda _item, key=kind: self.preview(key == "motions"))
            selector.itemActivated.connect(lambda _item, key=kind: self.preview(key == "motions"))
            self.selectors[kind] = selector
            column.addWidget(selector, 1)
            caption = QHBoxLayout()
            caption.addWidget(QLabel(description_title))
            caption.addStretch()
            more = QToolButton()
            more.setText("⋯")
            more.setObjectName("quiet")
            more.setToolTip("说明的保存范围与继承")
            more.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(more)
            menu.addAction("设为同角色共用", lambda key=kind: self.save_shared_description(key))
            menu.addAction("恢复默认", lambda key=kind: self.restore_description(key))
            more.setMenu(menu)
            caption.addWidget(more)
            column.addLayout(caption)
            description = QTextEdit()
            description.setAcceptRichText(False)
            description.setFixedHeight(88)
            description.setPlaceholderText("预览后写下可见效果，帮助 AI 选择")
            description.textChanged.connect(lambda key=kind: self._remember_description(key))
            self.descriptions[kind] = description
            column.addWidget(description)
            columns.addWidget(panel)
        columns.setChildrenCollapsible(False)
        columns.setMinimumHeight(370)
        resources = QScrollArea()
        resources.setFrameShape(QFrame.NoFrame)
        resources.setWidgetResizable(True)
        resources.setWidget(columns)
        layout.addWidget(resources, 1)
        purpose = QLabel("描述自动保存，供 AI 选择表情和动作。")
        purpose.setObjectName("muted")
        layout.addWidget(purpose)
        preview_row = self.preview_actions = QHBoxLayout()
        self.preview_button = QPushButton("▶  查看当前搭配")
        self.preview_button.setObjectName("primary")
        self.preview_button.clicked.connect(lambda: self.preview(True))
        preview_row.addWidget(self.preview_button, 1)
        self.expression_button = QPushButton("只查看表情")
        self.expression_button.clicked.connect(lambda: self.preview(False))
        preview_row.addWidget(self.expression_button)
        self.options = QToolButton()
        self.options.setText("⋯")
        self.options.setObjectName("quiet")
        self.options.setToolTip("预览方向与保存搭配")
        self.options.setPopupMode(QToolButton.InstantPopup)
        options = QMenu(self.options)
        facing = options.addMenu("预览朝向")
        directions = QActionGroup(facing)
        self.direction_actions: dict[str, QAction] = {}
        for key, name in (("C", "正面"), ("L", "向左"), ("R", "向右")):
            action = facing.addAction(name)
            action.setCheckable(True)
            action.setChecked(key == "C")
            directions.addAction(action)
            action.triggered.connect(lambda _checked, value=key: self.set_direction(value))
            self.direction_actions[key] = action
        options.addAction("保存当前搭配…", lambda: self.open_presets(new=True))
        self.options.setMenu(options)
        preview_row.addWidget(self.options)
        layout.addLayout(preview_row)
        self._refresh_actions()

    def selected_id(self, kind: str) -> str:
        """取得当前逻辑资源 ID，不读取列表显示文案。"""
        item = self.selectors[kind].currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def select_resource(self, kind: str, resource_id: str) -> None:
        """程序化恢复选项，不隐式播放动画。"""
        selector = self.selectors[kind]
        for index in range(selector.count()):
            if selector.item(index).data(Qt.UserRole) == resource_id:
                selector.setCurrentRow(index)
                return

    def load_model(self, path: str | Path | None) -> None:
        """更换模型时重置方向；同一模型刷新目录时保留选择和草稿。"""
        previous = {kind: self.selected_id(kind) for kind in self.selectors}
        same_model = bool(self.catalog and path and self.catalog.model_path == Path(path).resolve())
        self.catalog = load_performance_catalog(path) if path else None
        if not same_model:
            self.autosave_timer.stop()
            self.drafts.clear()
            self.set_direction("C")
            self.set_preview_status("点击列表项，在模型窗口预览")
            for search in self.searches.values():
                search.clear()
        self._loading = True
        for kind, selector in self.selectors.items():
            selector.blockSignals(True)
            selector.clear()
            resources = (self.catalog.motions if kind == "motions" else self.catalog.expressions) if self.catalog else {}
            for resource_id in sorted(resources):
                item = QListWidgetItem(resource_id)
                item.setData(Qt.UserRole, resource_id)
                item.setToolTip(self.catalog.descriptions.get(kind, {}).get(resource_id, resource_id))
                selector.addItem(item)
            default = next((key for key in resources if "idle" in key.lower()), next(iter(sorted(resources)), ""))
            self.select_resource(kind, previous[kind] if same_model and previous[kind] in resources else default)
            selector.blockSignals(False)
            self._show_description(kind)
            self.filter_resources(kind, self.searches[kind].text())
        self._loading = False
        self.setEnabled(self.catalog is not None and self.catalog.version == "v3")
        self._refresh_actions()

    def filter_resources(self, kind: str, text: str) -> None:
        """按 ID 和可见效果筛选，隐藏选项不会自动触发播放。"""
        selector = self.selectors[kind]
        for index in range(selector.count()):
            item = selector.item(index)
            item.setHidden(text.casefold() not in (item.text() + " " + item.toolTip()).casefold())

    def _show_description(self, kind: str) -> None:
        """显示选中资源的草稿或继承说明，并标注缺失与复核状态。"""
        key = self.selected_id(kind)
        value = self.catalog.descriptions.get(kind, {}).get(key, "") if self.catalog else ""
        field = self.descriptions[kind]
        field.blockSignals(True)
        text = self.drafts.get((kind, key), value)
        if (kind, key) in self.drafts and not text and self.catalog:
            text = self.catalog.inherited_descriptions.get(kind, {}).get(key, "")
        field.setPlainText(text)
        field.blockSignals(False)
        field.setEnabled(bool(key))
        field.setToolTip("资源已变化，请预览复核" if self.catalog and self.catalog.description_needs_review(kind, key) else "描述自动保存")
        self._refresh_actions()

    def _remember_description(self, kind: str) -> None:
        """按资源保存内存草稿，切换列表选项不会丢失修改。"""
        if self._loading or self.catalog is None:
            return
        key = self.selected_id(kind)
        if not key:
            return
        text = self.descriptions[kind].toPlainText().strip()
        if text == self.catalog.descriptions.get(kind, {}).get(key, ""):
            self.drafts.pop((kind, key), None)
        else:
            self.drafts[(kind, key)] = text
        if self.drafts:
            self.autosave_timer.start()
        else:
            self.autosave_timer.stop()
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        """按通道是否可用及有无草稿更新按钮状态。"""
        if not hasattr(self, "expression_button"):
            return
        self.preview_button.setEnabled(bool(self.selected_id("motions")))
        self.expression_button.setEnabled(bool(self.selected_id("expressions")))
        count = len(self.catalog.presets) if self.catalog else 0
        self.presets_button.setText(f"组合预设（{count}）…" if count else "组合预设…")

    def confirm_leave(self) -> bool:
        """模型或窗口切换前处理全部说明草稿。"""
        self.autosave_timer.stop()
        if not self.drafts or self.save_descriptions():
            return True
        if not confirm_description_changes(self, self.save_descriptions):
            return False
        self.drafts.clear()
        self._refresh_actions()
        return True

    def save_descriptions(self) -> bool:
        """一次保存当前模型所有资源草稿，保留最新预设等其他配置。"""
        self.autosave_timer.stop()
        if not self.drafts:
            return True
        if self.catalog is None:
            return False
        config_path = performance_config_path(self.catalog.model_path)
        config = read_config(config_path)
        fingerprints = object_mapping(config.get("fingerprints"))
        for (kind, key), text in self.drafts.items():
            entries = object_mapping(config.get(kind))
            if text:
                entries[key] = text
            else:
                entries.pop(key, None)
            config[kind] = entries
            recorded = object_mapping(fingerprints.get(kind))
            recorded[key] = self.catalog.resource_fingerprint(kind, key)
            fingerprints[kind] = recorded
        config["fingerprints"] = fingerprints
        try:
            save_config(config_path, config)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法保存说明", str(error))
            return False
        restored = {key for key, text in self.drafts.items() if not text}
        self.drafts.clear()
        self.catalog = load_performance_catalog(self.catalog.model_path)
        for kind, selector in self.selectors.items():
            if (kind, self.selected_id(kind)) in restored:
                self._show_description(kind)
            for index in range(selector.count()):
                item = selector.item(index)
                item.setToolTip(self.catalog.descriptions.get(kind, {}).get(str(item.data(Qt.UserRole)), ""))
            self.filter_resources(kind, self.searches[kind].text())
        self._refresh_actions()
        return True

    def restore_description(self, kind: str) -> None:
        """立即回填继承说明并自动移除当前资源的本模型覆盖。"""
        key = self.selected_id(kind)
        if not self.catalog or not key:
            return
        if key not in object_mapping(self.catalog.config.get(kind)):
            self.drafts.pop((kind, key), None)
            self._show_description(kind)
            return
        self.drafts[(kind, key)] = ""
        self._show_description(kind)
        self.save_descriptions()

    def save_shared_description(self, kind: str) -> None:
        """将当前一项说明移到共享系列，不顺带保存另一列的草稿。"""
        if not self.catalog or not self.selected_id(kind):
            return
        key = self.selected_id(kind)
        text = self.descriptions[kind].toPlainText().strip()
        if not text:
            return
        shared_path = shared_config_path(self.catalog.model_path)
        shared = read_config(shared_path)
        series = object_mapping(shared.get("series"))
        target = object_mapping(series.get(self.catalog.series))
        descriptions = object_mapping(target.get(kind))
        descriptions[key] = text
        target[kind] = descriptions
        series[self.catalog.series] = target
        shared["series"] = series
        local_path = performance_config_path(self.catalog.model_path)
        config = read_config(local_path)
        local = object_mapping(config.get(kind))
        local.pop(key, None)
        config[kind] = local
        recorded = object_mapping(config.get("fingerprints"))
        entries = object_mapping(recorded.get(kind))
        entries[key] = self.catalog.resource_fingerprint(kind, key)
        recorded[kind] = entries
        config["fingerprints"] = recorded
        try:
            save_config(shared_path, shared)
            save_config(local_path, config)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "共享说明未完整保存", str(error))
            return
        self.drafts.pop((kind, key), None)
        self.load_model(self.catalog.model_path)

    def set_direction(self, direction: str) -> None:
        """只改变当前模型的预览方向，不写入消息或组合预设。"""
        self.direction = direction
        for key, action in self.direction_actions.items():
            action.setChecked(key == direction)
        self.options.setToolTip("预览朝向：" + {"C": "正面", "L": "向左", "R": "向右"}[direction])

    def set_preview_status(self, message: str) -> None:
        """由宿主的预览回执更新状态，预设窗口共享同一反馈。"""
        self.previewFeedback.emit(message)

    def preview(self, include_motion: bool) -> None:
        """动作点击重播搭配，表情点击只更新表情。"""
        self.preview_selection(self.selected_id("motions") if include_motion else "", self.selected_id("expressions"))

    def preview_selection(self, motion: str, expression: str) -> None:
        """预览给定资源，不改动主界面的当前选项或描述草稿。"""
        if self.catalog is None or not (motion or expression):
            return
        payload: dict[str, object] = {"type": "performance", "model_path": str(self.catalog.model_path),
                                     "expression": expression, "direction": self.direction}
        if motion:
            payload["motion"] = motion
        self.set_preview_status("正在加载预览…")
        self.send_preview(payload)

    def open_presets(self, _checked: bool = False, *, new: bool = False) -> None:
        """在次级对话框管理预设，关闭后保留主界面的草稿。"""
        if self.catalog is None:
            return
        dialog = PerformancePresetDialog(self, new=new)
        if dialog.exec_() == QDialog.Accepted and dialog.applied_selection:
            motion, expression = dialog.applied_selection
            self.select_resource("motions", motion)
            self.select_resource("expressions", expression)
        self.load_model(self.catalog.model_path)
        dialog.deleteLater()


class PerformancePresetDialog(QDialog):
    """管理和试播搭配，明确使用后才同步主界面选择。"""

    def __init__(self, editor: Live2DPerformanceEditor, *, new: bool = False) -> None:
        """构造预设列表和选中项详情，新增时带入主界面的搭配。"""
        super().__init__(editor)
        self.editor = editor
        self.catalog = editor.catalog
        if self.catalog is None:
            raise ValueError("请先加载模型再管理组合预设")
        self.applied_selection: tuple[str, str] | None = None
        self._dirty = False
        self._loading = False
        self._selected_id = ""
        self.setWindowTitle("组合预设")
        self.resize(650, 450)
        layout = QVBoxLayout(self)
        hint = QLabel("保存常用搭配，供 AI 参考。")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        body = QHBoxLayout()
        self.items = QListWidget()
        self.items.setMinimumWidth(180)
        self.items.currentRowChanged.connect(self._select_preset)
        body.addWidget(self.items, 1)
        detail = QVBoxLayout()
        form = QFormLayout()
        self.name, self.effect = QLineEdit(), QLineEdit()
        self.motion, self.expression = QComboBox(), QComboBox()
        self.motion.addItems(sorted(self.catalog.motions))
        self.expression.addItems(sorted(self.catalog.expressions))
        form.addRow("名称", self.name)
        form.addRow("搭配效果", self.effect)
        form.addRow("动作", self.motion)
        form.addRow("表情", self.expression)
        detail.addLayout(form)
        for field in (self.name, self.effect):
            field.textChanged.connect(self._mark_dirty)
        for field in (self.motion, self.expression):
            field.currentTextChanged.connect(self._mark_dirty)
        self.preview_button = QPushButton("▶  预览搭配")
        self.preview_button.setObjectName("primary")
        self.preview_button.clicked.connect(self.preview)
        detail.addWidget(self.preview_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        editor.previewFeedback.connect(self.status.setText)
        detail.addWidget(self.status)
        detail.addStretch()
        row = QHBoxLayout()
        self.remove = QPushButton("删除组合")
        self.remove.clicked.connect(self.delete_preset)
        self.save = QPushButton("保存组合")
        self.save.clicked.connect(self.save_preset)
        row.addWidget(self.remove)
        row.addWidget(self.save)
        detail.addLayout(row)
        body.addLayout(detail, 2)
        layout.addLayout(body, 1)
        footer = QHBoxLayout()
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        self.use = QPushButton("使用此搭配")
        self.use.clicked.connect(self.apply_selection)
        footer.addWidget(close)
        footer.addStretch()
        footer.addWidget(self.use)
        layout.addLayout(footer)
        self.reload_presets("" if new or not self.catalog.presets else str(self.catalog.presets[0].get("id", "")))

    def reload_presets(self, selected_id: str) -> None:
        """重新读取已保存列表，保留缺失资源的项目供修复。"""
        self.catalog = load_performance_catalog(self.catalog.model_path)
        self.items.blockSignals(True)
        self.items.clear()
        item = QListWidgetItem("＋ 新建组合")
        item.setData(Qt.UserRole, "")
        self.items.addItem(item)
        valid = {str(preset.get("id")) for preset in self.catalog.valid_presets()}
        row = 0
        for index, preset in enumerate(self.catalog.presets, 1):
            key = str(preset.get("id") or "")
            item = QListWidgetItem(str(preset.get("name") or key) + (" · 资源缺失" if key not in valid else ""))
            item.setData(Qt.UserRole, key)
            item.setToolTip(str(preset.get("description") or ""))
            self.items.addItem(item)
            if key == selected_id:
                row = index
        self._dirty = False
        self.items.setCurrentRow(row)
        self.items.blockSignals(False)
        self._select_preset(row)

    def _select_preset(self, row: int) -> None:
        """切换详情前保护尚未保存的预设编辑。"""
        item = self.items.item(row)
        if item is None:
            return
        key = str(item.data(Qt.UserRole) or "")
        if self._dirty and not confirm_description_changes(self, self.save_preset):
            self.items.blockSignals(True)
            for index in range(self.items.count()):
                if self.items.item(index).data(Qt.UserRole) == self._selected_id:
                    self.items.setCurrentRow(index)
                    break
            self.items.blockSignals(False)
            return
        self._selected_id = key
        self.items.blockSignals(True)
        for index in range(self.items.count()):
            if self.items.item(index).data(Qt.UserRole) == key:
                self.items.setCurrentRow(index)
                break
        self.items.blockSignals(False)
        self._loading = True
        preset = next((entry for entry in self.catalog.presets if entry.get("id") == key), {})
        self.name.setText(str(preset.get("name") or ""))
        self.effect.setText(str(preset.get("description") or ""))
        for selector, field, kind in ((self.motion, "motion", "motions"), (self.expression, "expression", "expressions")):
            selector.clear()
            selector.addItems(sorted(self.catalog.motions if kind == "motions" else self.catalog.expressions))
            value = str(preset.get(field) or self.editor.selected_id(kind))
            if value and selector.findText(value) < 0:
                selector.addItem(value)
            selector.setCurrentText(value)
        self._loading = False
        self._dirty = False
        self.remove.setEnabled(bool(key))
        self._update_actions()

    def _mark_dirty(self, _text: str) -> None:
        """编辑内容变化后启用保存并重新校验预览资源。"""
        if not self._loading:
            self._dirty = True
            self._update_actions()

    def _update_actions(self) -> None:
        """缺失资源的预设可修复，但不允许试播或使用错误搭配。"""
        valid = self.motion.currentText() in self.catalog.motions and self.expression.currentText() in self.catalog.expressions
        self.preview_button.setEnabled(valid)
        self.use.setEnabled(valid)
        self.save.setEnabled(self._dirty and bool(self.name.text().strip()))
        self.status.setText("资源缺失，请重新选择动作或表情" if not valid else "有未保存的修改" if self._dirty else "")

    def preview(self) -> None:
        """试播详情中的搭配，不改变主编辑页。"""
        self.editor.preview_selection(self.motion.currentText(), self.expression.currentText())

    def save_preset(self) -> bool:
        """只更新当前预设，主界面的说明草稿不参与保存。"""
        if not self.name.text().strip():
            self.name.setFocus()
            self.status.setText("请为组合填写名称")
            return False
        key = self._selected_id or uuid.uuid4().hex
        preset = {"id": key, "name": self.name.text().strip(), "description": self.effect.text().strip(),
                  "motion": self.motion.currentText(), "expression": self.expression.currentText()}
        path = performance_config_path(self.catalog.model_path)
        config = read_config(path)
        values = config.get("presets", [])
        config["presets"] = [entry for entry in values if isinstance(entry, dict) and entry.get("id") != key] + [preset] if isinstance(values, list) else [preset]
        try:
            save_config(path, config)
        except (OSError, ValueError) as error:
            self.status.setText(f"保存失败：{error}")
            return False
        self.reload_presets(key)
        self.status.setText("已保存 · 下轮对话生效")
        return True

    def delete_preset(self) -> None:
        """删除当前已保存预设，历史消息中的搭配不受影响。"""
        if not self._selected_id:
            return
        path = performance_config_path(self.catalog.model_path)
        config = read_config(path)
        values = config.get("presets", [])
        config["presets"] = [entry for entry in values if isinstance(entry, dict) and entry.get("id") != self._selected_id] if isinstance(values, list) else []
        try:
            save_config(path, config)
        except (OSError, ValueError) as error:
            self.status.setText(f"删除失败：{error}")
            return
        self.reload_presets("")

    def apply_selection(self) -> None:
        """明确使用搭配后才更新主界面，未保存预设先处理草稿。"""
        selection = (self.motion.currentText(), self.expression.currentText())
        if self._dirty and not confirm_description_changes(self, self.save_preset):
            return
        self.applied_selection = selection
        self._dirty = False
        self.accept()

    def reject(self) -> None:
        """关闭对话框时保护未保存的预设修改。"""
        if self._dirty and not confirm_description_changes(self, self.save_preset):
            return
        self._dirty = False
        super().reject()
