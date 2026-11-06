"""自定义表情对话框：参数草稿与磁盘资源显式分离。"""

from __future__ import annotations

import uuid
import re
from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider,
    QStyle, QTabBar, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from live2d_support.custom_expressions import CustomExpressionStore, ExpressionParameter, read_document
from live2d_support.performance_catalog import load_performance_catalog, object_mapping


class ParameterScrollArea(QScrollArea):
    """参数控件上的滚轮和触控板事件只用于浏览列表。"""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """转发完整滚动事件到视口，保留触控板像素增量和滚动阶段。"""
        if isinstance(event, QWheelEvent) and isinstance(watched, (QSlider, QDoubleSpinBox, QComboBox)):
            pixels = event.pixelDelta()
            if not pixels.isNull():
                # Qt 5 的滚动条在部分平台只消费角度增量，触控板需按像素移动。
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - pixels.y())
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - pixels.x())
                event.accept()
                return True
            forwarded = QWheelEvent(QPointF(self.viewport().mapFromGlobal(event.globalPos())),
                event.globalPosF(), event.pixelDelta(), event.angleDelta(), event.buttons(),
                event.modifiers(), event.phase(), event.inverted(), event.source())
            QApplication.sendEvent(self.viewport(), forwarded)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class ParameterRow(QWidget):
    """提供纳入开关、精确数值及混合方式，滑动即选中参数。"""

    def __init__(self, spec: ExpressionParameter, changed: Callable[[], None], parent: QWidget) -> None:
        """按真实模型范围建立固定高度的参数行。"""
        super().__init__(parent)
        self.spec, self.changed = spec, changed
        self.updating = False
        self.previous_blend = "Overwrite"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        self.included = QCheckBox()
        self.included.setToolTip("纳入表情")
        self.included.setAccessibleName(f"纳入 {spec.name}")
        layout.addWidget(self.included)
        label = QLabel(self)
        label.setFixedWidth(170)
        label.ensurePolished()
        label.setText(label.fontMetrics().elidedText(spec.name, Qt.ElideMiddle, 170))
        label.setToolTip(spec.name + "\n" + spec.id)
        layout.addWidget(label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setMinimumWidth(100)
        self.slider.setAttribute(Qt.WA_StyledBackground, True)
        self.slider.setStyleSheet("""
            QSlider { background: #F3F5F9; min-height: 28px; }
            QSlider::groove:horizontal { height: 4px; background: #CDD6E3; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #426BAA; border-radius: 2px; }
            QSlider::handle:horizontal {
                width: 14px; margin: -6px 0; background: #FFFFFF;
                border: 1px solid #426BAA; border-radius: 7px;
            }
            QSlider::handle:horizontal:hover { background: #DFE9F8; }
            QSlider::handle:horizontal:pressed { background: #426BAA; }
            QSlider::handle:horizontal:disabled { border-color: #98A4B5; background: #E5EAF1; }
        """)
        self.slider.valueChanged.connect(self.slider.update)
        layout.addWidget(self.slider, 1)
        self.value = QDoubleSpinBox()
        self.value.setDecimals(6)
        self.value.setKeyboardTracking(False)
        self.value.setFixedWidth(115)
        layout.addWidget(self.value)
        self.blend = QComboBox()
        for label_text, mode in (("覆盖", "Overwrite"), ("叠加", "Add"), ("相乘", "Multiply")):
            self.blend.addItem(label_text, mode)
        self.blend.setFixedWidth(76)
        layout.addWidget(self.blend)
        reset = QToolButton()
        reset.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        reset.setToolTip("恢复参数默认值，保留纳入状态")
        reset.clicked.connect(self.reset_value)
        layout.addWidget(reset)
        self.value.valueChanged.connect(self.value_changed)
        self.slider.valueChanged.connect(self.slider_changed)
        self.included.toggled.connect(self.notify)
        self.blend.currentIndexChanged.connect(self.blend_changed)
        self.load(None)

    def mode(self) -> str:
        """返回标准 Cubism 混合标识。"""
        return str(self.blend.currentData())

    def bounds(self, mode: str) -> tuple[float, float]:
        """将模型目标范围转换为当前操作数范围。"""
        spec = self.spec
        if mode == "Add":
            return spec.minimum - spec.default, spec.maximum - spec.default
        if mode == "Multiply":
            if abs(spec.default) < 1e-12:
                return -1000.0, 1000.0
            return tuple(sorted((spec.minimum / spec.default, spec.maximum / spec.default)))
        return spec.minimum, spec.maximum

    def sync_slider(self) -> None:
        """仅更新滑条显示，避免精度量化回写数值。"""
        low, high = self.value.minimum(), self.value.maximum()
        self.slider.blockSignals(True)
        self.slider.setValue(round((self.value.value() - low) / (high - low) * 10000) if high > low else 0)
        self.slider.blockSignals(False)
        self.slider.update()

    def load(self, entry: dict[str, object] | None) -> None:
        """装载草稿，不触发修改或自动勾选。"""
        self.updating = True
        mode = str(entry.get("Blend", "Add")) if entry else "Overwrite"
        self.blend.setCurrentIndex(max(0, self.blend.findData(mode)))
        self.previous_blend = self.mode()
        low, high = self.bounds(self.mode())
        value = float(entry["Value"]) if entry else self.spec.default
        # 现有资源可能使用会被运行库钳制的操作数；加载时保留原值。
        self.value.setRange(min(low, value), max(high, value))
        self.value.setSingleStep(max((high - low) / 100, 0.001))
        self.value.setValue(value)
        self.included.setChecked(entry is not None)
        self.sync_slider()
        self.updating = False

    def notify(self, _checked: bool = False) -> None:
        """向草稿报告显式用户操作。"""
        if not self.updating:
            self.changed()

    def value_changed(self, _value: float) -> None:
        """数值修改自动勾选，保持滑条与输入框同步。"""
        self.sync_slider()
        if not self.updating:
            self.included.blockSignals(True)
            self.included.setChecked(True)
            self.included.blockSignals(False)
            self.changed()

    def slider_changed(self, value: int) -> None:
        """把滑条位置映射到实际范围。"""
        self.value.setValue(self.value.minimum() + value / 10000 * (self.value.maximum() - self.value.minimum()))

    def blend_changed(self, _index: int) -> None:
        """转换混合操作数，保持默认基准上的可见目标值。"""
        if self.updating:
            return
        base, value = self.spec.default, self.value.value()
        old, new = self.previous_blend, self.mode()
        target = value if old == "Overwrite" else base + value if old == "Add" else base * value
        if new == "Multiply" and abs(base) < 1e-12 and abs(target) > 1e-6:
            self.blend.blockSignals(True)
            self.blend.setCurrentIndex(self.blend.findData(old))
            self.blend.blockSignals(False)
            QMessageBox.information(self, "无法保持当前效果", "此参数默认值为 0，相乘无法产生当前目标值。")
            return
        value = target if new == "Overwrite" else target - base if new == "Add" else target / base if base else 1.0
        self.load({"Id": self.spec.id, "Value": value, "Blend": new})
        self.changed()

    def reset_value(self) -> None:
        """恢复当前混合方式的中性数值，不改变是否纳入。"""
        included = self.included.isChecked()
        value = self.spec.default if self.mode() == "Overwrite" else 0.0 if self.mode() == "Add" else 1.0
        self.load({"Value": value, "Blend": self.mode()})
        self.updating = True
        self.included.setChecked(included)
        self.updating = False
        self.changed()

    def entry(self) -> dict[str, object]:
        """导出此行的标准表情参数。"""
        return {"Id": self.spec.id, "Value": self.value.value(), "Blend": self.mode()}


class CustomExpressionDialog(QDialog):
    """管理原表情的复制及自定义表情的新建、保存和删除。"""

    def __init__(self, model_path: Path, selected: str, send: Callable[[dict[str, object]], object],
                 confirm: Callable[[QWidget, Callable[[], bool]], bool], parent: QWidget) -> None:
        """建立模态编辑器，参数通过现有渲染进程异步获取。"""
        super().__init__(parent)
        self.setWindowTitle("自定义表情")
        self.setModal(True)
        self.resize(830, 720)
        self.store = CustomExpressionStore(model_path)
        self.send, self.confirm = send, confirm
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.ready = self.dirty = self.loading = self.closed = False
        self.selected = selected
        self.specs: list[ExpressionParameter] = []
        self.rows: list[ParameterRow] = []
        self.unknown: list[dict[str, object]] = []
        self.catalog = load_performance_catalog(model_path)
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setMinimumWidth(200)
        bar.addWidget(self.selector, 1)
        for attr, text, action in (("new_button", "从默认新建", self.new_expression),
                                   ("copy_button", "另存为新表情", self.copy_expression)):
            button = QPushButton(text)
            button.clicked.connect(action)
            setattr(self, attr, button)
            bar.addWidget(button)
        self.delete_button = QToolButton()
        self.delete_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete_button.setToolTip("删除自定义表情")
        self.delete_button.clicked.connect(self.delete_expression)
        bar.addWidget(self.delete_button)
        root.addLayout(bar)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.name = QLineEdit()
        self.name.setPlaceholderText("restrained_smile")
        self.description = QTextEdit()
        self.description.setAcceptRichText(False)
        self.description.setFixedHeight(65)
        form.addRow("英文名称", self.name)
        form.addRow("中文描述", self.description)
        root.addLayout(form)
        fade = QHBoxLayout()
        self.fade_in, self.fade_out = QDoubleSpinBox(), QDoubleSpinBox()
        for label, field in (("淡入", self.fade_in), ("淡出", self.fade_out)):
            field.setRange(0, 60)
            field.setDecimals(2)
            field.setSingleStep(0.1)
            field.setSuffix(" 秒")
            field.setValue(0.5)
            fade.addWidget(QLabel(label))
            fade.addWidget(field)
            field.valueChanged.connect(self.changed)
        fade.addStretch()
        root.addLayout(fade)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索参数名称或 ID")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)
        self.group = QComboBox()
        self.group.addItems(["全部参数", "眼睛", "眉毛", "嘴", "其他"])
        filters.addWidget(self.group)
        self.selected_only = QCheckBox("只看已选")
        filters.addWidget(self.selected_only)
        root.addLayout(filters)
        self.scroll = ParameterScrollArea()
        self.scroll.setWidgetResizable(True)
        self.parameter_widget = QWidget()
        self.parameter_layout = QVBoxLayout(self.parameter_widget)
        self.parameter_layout.setContentsMargins(6, 2, 6, 2)
        self.parameter_layout.setSpacing(0)
        self.scroll.setWidget(self.parameter_widget)
        root.addWidget(self.scroll, 1)
        playback = QHBoxLayout()
        self.mode = QTabBar()
        self.mode.setExpanding(False)
        self.mode.setDrawBase(False)
        self.mode.setElideMode(Qt.ElideNone)
        for label, value in (("静态编辑", "static"), ("演出预览", "performance")):
            self.mode.setTabData(self.mode.addTab(label), value)
        self.mode.setStyleSheet("""
            QTabBar::tab { background: #FFFFFF; color: #263449; border: 1px solid #DCE3EC;
                padding: 8px 12px; min-width: 62px; }
            QTabBar::tab:first { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
            QTabBar::tab:last { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
            QTabBar::tab:selected { background: #426BAA; color: #FFFFFF; border-color: #426BAA; }
            QTabBar::tab:!selected:hover { background: #EAF0FA; }
        """)
        playback.addWidget(self.mode)
        self.motion = QComboBox()
        self.motion.addItem("无搭配动作", "")
        for key in sorted(self.catalog.motions):
            self.motion.addItem(key, key)
        self.motion.setEnabled(False)
        playback.addWidget(self.motion, 1)
        self.mouth_preview = QCheckBox("口型试播")
        self.mouth_preview.setEnabled(False)
        self.mouth_preview.toggled.connect(self.preview_mode_changed)
        playback.addWidget(self.mouth_preview)
        self.replay = QPushButton("重播")
        self.replay.clicked.connect(self.preview)
        playback.addWidget(self.replay)
        root.addLayout(playback)
        self.status = QLabel("正在读取模型参数…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        footer = QHBoxLayout()
        footer.addStretch()
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save_expression)
        footer.addWidget(self.save_button)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        root.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.preview)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(15000)
        self.timeout.timeout.connect(self.timed_out)
        self.name.textChanged.connect(self.metadata_changed)
        self.description.textChanged.connect(self.metadata_changed)
        self.search.textChanged.connect(self.filter_rows)
        self.group.currentIndexChanged.connect(self.filter_rows)
        self.selected_only.toggled.connect(self.filter_rows)
        self.mode.currentChanged.connect(self.preview_mode_changed)
        self.motion.currentIndexChanged.connect(self.preview_mode_changed)
        self.selector.currentIndexChanged.connect(self.select_expression)
        self.refresh_selector(selected)
        self.set_controls()
        QTimer.singleShot(0, self.begin)

    def command(self, action: str, **values: object) -> None:
        """附加会话身份和递增序号，复用宿主预览队列。"""
        self.sequence += 1
        self.send({"type": "expression_editor", "action": action, "session_id": self.session_id,
                   "sequence": self.sequence, **values})

    def begin(self) -> None:
        """请求真实参数信息并启动有限等待。"""
        if not self.closed:
            self.command("begin")
            self.timeout.start()

    def timed_out(self) -> None:
        """明确提示渲染进程尚未响应，不允许保存空参数。"""
        self.status.setText("预览窗口未响应，请关闭后检查模型并重新打开。")

    def receive(self, result: object) -> None:
        """只消费本会话当前请求的结果。"""
        if not isinstance(result, dict) or result.get("session_id") != self.session_id or self.closed:
            return
        if result.get("action") == "begin":
            self.timeout.stop()
            if not result.get("ok"):
                self.status.setText(str(result.get("message")))
                return
            self.specs = [ExpressionParameter(**item) for item in result.get("parameters", [])]
            self.specs.sort(key=lambda item: (self.parameter_group(item), item.id))
            for spec in self.specs:
                row = ParameterRow(spec, self.changed, self.parameter_widget)
                self.rows.append(row)
                for control in (row.slider, row.value, row.blend):
                    control.installEventFilter(self.scroll)
                self.parameter_layout.addWidget(row)
            self.parameter_layout.addStretch()
            self.ready = True
            self.load_selection()
        elif result.get("sequence") == self.sequence:
            self.status.setText(str(result.get("message")) + (" · 未保存" if self.dirty else ""))

    @staticmethod
    def parameter_group(spec: ExpressionParameter) -> int:
        """按常见 ID 保守归类，未知参数始终保留。"""
        key = spec.id.lower()
        return 2 if "brow" in key else 1 if "eye" in key else 3 if "mouth" in key else 4

    def filter_rows(self, _value: object = None) -> None:
        """筛选只影响可见性，不改变参数草稿。"""
        query = self.search.text().casefold()
        group = self.group.currentIndex()
        for row in self.rows:
            row.setVisible((not query or query in (row.spec.id + " " + row.spec.name).casefold())
                           and (not group or group == self.parameter_group(row.spec))
                           and (not self.selected_only.isChecked() or row.included.isChecked()))

    def editable(self) -> bool:
        """原资源只读，自定义资源及新草稿可修改。"""
        return not self.selected or self.selected in object_mapping(self.catalog.config.get("custom_expressions"))

    def set_controls(self) -> None:
        """固定按钮位置，仅改变可用状态。"""
        editable = self.ready and self.editable()
        self.selector.setEnabled(self.ready)
        self.new_button.setEnabled(self.ready)
        self.copy_button.setEnabled(self.ready and bool(self.selected))
        self.delete_button.setEnabled(editable and bool(self.selected))
        self.name.setReadOnly(bool(self.selected))
        self.name.setEnabled(self.ready)
        self.description.setReadOnly(not editable)
        self.fade_in.setEnabled(editable)
        self.fade_out.setEnabled(editable)
        self.save_button.setEnabled(editable and not self.unknown)
        self.save_button.setText("保存修改" if self.selected else "创建表情")
        self.replay.setEnabled(self.ready)
        for row in self.rows:
            row.setEnabled(editable)

    def refresh_selector(self, selected: str) -> None:
        """刷新最新资源，使用稳定名称恢复选择。"""
        self.catalog = load_performance_catalog(self.store.model_path)
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItem("新表情", "")
        owners = object_mapping(self.catalog.config.get("custom_expressions"))
        for key in sorted(self.catalog.expressions):
            self.selector.addItem(key + (" · 自定义" if key in owners else " · 原模型"), key)
        self.selector.setCurrentIndex(max(0, self.selector.findData(selected)))
        self.selected = str(self.selector.currentData() or "")
        self.selector.blockSignals(False)

    def can_leave(self) -> bool:
        """切换、关闭或删除前先处理未保存草稿。"""
        return not self.dirty or self.confirm(self, self.save_expression)

    def select_expression(self, _index: int) -> None:
        """切换失败时恢复选择，保留原草稿。"""
        candidate = str(self.selector.currentData() or "")
        if not self.can_leave():
            self.selector.blockSignals(True)
            self.selector.setCurrentIndex(self.selector.findData(self.selected))
            self.selector.blockSignals(False)
            return
        self.selected = candidate
        self.refresh_selector(candidate)
        self.load_selection()

    def load_selection(self) -> bool:
        """读取表情原始操作数，未知参数阻止保存以免静默丢失。"""
        if not self.ready:
            return False
        try:
            data = read_document(self.store.model_path.parent / self.catalog.expressions[self.selected]) if self.selected else {}
            entries = {str(item["Id"]): item for item in data.get("Parameters", [])}
            ids = {spec.id for spec in self.specs}
            self.unknown = [item for key, item in entries.items() if key not in ids]
            self.loading = True
            self.name.setText(self.selected)
            self.description.setPlainText(self.catalog.descriptions.get("expressions", {}).get(self.selected, ""))
            self.fade_in.setValue(float(data.get("FadeInTime", 0.5)))
            self.fade_out.setValue(float(data.get("FadeOutTime", 0.5)))
            for row in self.rows:
                row.load(entries.get(row.spec.id))
            self.dirty = False
            if self.selected and not self.editable():
                self.start_copy_draft(self.selected, dirty=False)
            self.set_controls()
            self.filter_rows()
            self.status.setText("包含模型缺失的参数，无法保存" if self.unknown else "原模型表情，可复制后编辑" if not self.editable() else "")
            if not self.unknown:
                self.timer.start()
            return True
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.setText(f"无法读取表情：{error}")
            self.save_button.setEnabled(False)
            return False
        finally:
            self.loading = False

    def new_expression(self) -> None:
        """从模型默认状态创建新草稿。"""
        if self.can_leave():
            self.refresh_selector("")
            self.load_selection()
            self.name.setFocus()

    def copy_expression(self) -> None:
        """复制已保存资源，保留混合方式及说明并生成新的名称。"""
        if not self.can_leave():
            return
        source = self.selected
        if not self.load_selection():
            return
        self.start_copy_draft(source, dirty=True)
        self.set_controls()
        self.name.selectAll()
        self.name.setFocus()

    def start_copy_draft(self, source: str, dirty: bool) -> None:
        """原表情作为编辑起点，实际保存前不创建文件或更改来源资源。"""
        was_loading = self.loading
        self.loading = True
        self.refresh_selector("")
        self.selector.setItemText(0, f"新表情 · 基于 {source}")
        stem = re.sub(r"[^A-Za-z0-9_]", "_", source).strip("_")[:48]
        if not stem or not stem[0].isalpha():
            stem = "expression"
        base = stem + "_custom"
        candidate, index = base, 2
        names = {name.casefold() for name in self.catalog.expressions}
        while candidate.casefold() in names or self.store.target(candidate).exists():
            candidate = f"{base}_{index}"
            index += 1
        self.name.setText(candidate)
        self.dirty = dirty
        self.loading = was_loading

    def changed(self, _value: object = None) -> None:
        """草稿修改仅触发节流预览，不自动保存模型文件。"""
        if self.loading or not self.ready:
            return
        self.dirty = True
        self.status.setText("未保存")
        self.filter_rows()
        self.timer.start()

    def metadata_changed(self, _value: object = None) -> None:
        """名称和描述只标记草稿，不打断正在进行的演出预览。"""
        if self.ready and not self.loading:
            self.dirty = True
            self.status.setText("未保存")

    def document(self) -> dict[str, object]:
        """使用同一份草稿数据驱动试播和保存。"""
        return {"Type": "Live2D Expression", "FadeInTime": self.fade_in.value(), "FadeOutTime": self.fade_out.value(),
                "Parameters": [row.entry() for row in self.rows if row.included.isChecked()] + self.unknown}

    def preview_mode_changed(self, _index: int) -> None:
        """预览设置不改变草稿保存状态。"""
        self.motion.setEnabled(self.preview_mode() == "performance")
        self.mouth_preview.setEnabled(self.preview_mode() == "performance"
                                      and any(spec.id == "ParamMouthOpenY" for spec in self.specs))
        self.timer.start()

    def preview_mode(self) -> str:
        """从可见的分段选项读取运行模式。"""
        return str(self.mode.tabData(self.mode.currentIndex()))

    def preview(self) -> None:
        """提交完整参数快照，避免局部命令遗漏取消勾选。"""
        self.timer.stop()
        if self.ready and not self.closed:
            self.command("preview", document=self.document(), mode=self.preview_mode(),
                         motion=self.motion.currentData(), mouth=self.mouth_preview.isChecked())

    def save_expression(self) -> bool:
        """保存成功才清除草稿，并立即从资源重新载入验证预览。"""
        if not self.ready or not self.editable():
            return False
        try:
            name = self.name.text().strip()
            self.store.save(name, self.description.toPlainText(), self.document(), self.specs, bool(self.selected))
            self.dirty = False
            self.refresh_selector(name)
            self.load_selection()
            self.status.setText("已保存")
            return True
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "无法保存表情", str(error))
            return False

    def delete_expression(self) -> None:
        """列出受影响预设，经确认后删除自定义文件。"""
        if not self.selected or not self.can_leave():
            return
        try:
            affected = self.store.referenced_presets(self.selected)
            message = f"删除表情 {self.selected}？"
            if affected:
                message += "\n以下组合将缺少表情，需要重新选择：\n" + "\n".join(affected)
            if QMessageBox.question(self, "删除自定义表情", message, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return
            self.store.delete(self.selected)
            self.dirty = False
            self.refresh_selector("")
            self.load_selection()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法删除表情", str(error))

    def done(self, result: int) -> None:
        """所有关闭路径统一处理草稿和渲染端会话释放。"""
        if self.closed or not self.can_leave():
            return
        self.closed = True
        self.timer.stop()
        self.timeout.stop()
        self.command("end")
        super().done(result)
