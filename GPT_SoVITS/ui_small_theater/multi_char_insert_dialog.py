"""小剧场新增台词表单：只收集消息字段，不请求 LLM 或合成音频。"""
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLayout, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget, QWidgetItem,
)
from live2d_support.multi_char_scene import arrange_cast
from ui_small_theater.multi_char_icons import color_dot_icon, delete_message_icon, set_button_icon
from ui_small_theater.multi_char_menu import fit_menu_to_contents
from ui_small_theater.multi_char_style import editor_stylesheet
from ui_small_theater.multi_char_timeline import _character_pill_color


def _role_colors(roster, role):
    """返回角色的显示文案、主题色十六进制串及适配亮度的前景文字色。"""
    name = roster.label(role)
    label = name if role in roster.characters else f"{name}（未安装）"
    color = _character_pill_color(name)
    luminance = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
    foreground = "#334155" if luminance >= 145 else "#FFFFFF"
    return label, color.name(), foreground


def role_button(roster, role, parent=None, *, active=False):
    """以统一胶囊语言绘制小剧场角色按钮：未点亮用柔灰底配主题色圆点，点亮才填满主题色。"""
    label, bg_hex, fg_hex = _role_colors(roster, role)
    button = QPushButton(label, parent)
    button.setObjectName("multiCharRolePill")
    button.setCursor(Qt.PointingHandCursor)
    # 仅允许键盘 Tab 聚焦，避免鼠标点击后残留系统焦点框干扰点亮状态判断
    button.setFocusPolicy(Qt.TabFocus)
    button.setProperty("roleColorHex", bg_hex)
    button.setProperty("roleLabel", label)
    if active:
        button.setStyleSheet(
            f"QPushButton#multiCharRolePill {{background-color: {bg_hex}; border-color: {bg_hex};"
            f" color: {fg_hex}; font-weight: bold;}}"
        )
    else:
        button.setIcon(color_dot_icon(bg_hex))
        button.setIconSize(QSize(10, 10))
        # 只覆盖选中态的主题原色，未选中与悬浮样式全部交给全局 QSS 统一控制，避免悬浮伪装成点亮
        button.setStyleSheet(
            f"QPushButton#multiCharRolePill:checked {{background-color: {bg_hex};"
            f" border-color: {bg_hex}; color: {fg_hex}; font-weight: bold;}}"
            f"QPushButton#multiCharRolePill:checked:hover {{border-color: #526884;}}"
        )
    return button


class FlowLayout(QLayout):
    """支持高度随宽度自适应的流式布局，用于多角色胶囊自动换行并消除横向滚动条灰块。"""

    def __init__(self, parent=None, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def insertWidget(self, index, widget):
        """在指定下标插入子控件，使末尾的「＋」按钮始终排在所有角色之后。"""
        self.addChildWidget(widget)
        self._items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            # 当前行放不下时换行，保持每行控件垂直居中对齐
            if next_x - self._h_spacing > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class CastButtons(QWidget):
    """共享两位出场上限、左右站位标识与全剧角色一键添加入口。"""

    changed = pyqtSignal()
    roster_changed = pyqtSignal()

    def __init__(self, roster, initial, save, parent=None):
        super().__init__(parent)
        self.setObjectName("multiCharCastButtons")
        self.roster = roster
        self.save = save
        self.buttons = {}
        self.baseline_cast = tuple(initial)
        # 水平伸展、垂直按流式换行后的高度自适应，避免硬编码滚动条高度
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.flow = FlowLayout(self, h_spacing=6, v_spacing=6)
        self.add_button = QPushButton("＋", self)
        self.add_button.setObjectName("multiCharAddRole")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setFocusPolicy(Qt.TabFocus)
        self.add_button.setToolTip("加入剧本角色")
        self.add_button.setAccessibleName("加入剧本角色")
        self.add_button.clicked.connect(self.add_role)
        self.flow.addWidget(self.add_button)
        self.set_selection(initial)

    def selected(self):
        return [role for role, button in self.buttons.items() if button.isChecked()]

    def set_selection(self, roles):
        """从已保存的左右槽位重新投影；更新按钮不发出草稿变更。"""
        self.baseline_cast = tuple(roles)
        self.sync_buttons(roles)

    def sync_buttons(self, selected):
        """同步可用角色，并保留本句已保存的出场角色及其草稿状态。"""
        available = self.roster.role_ids()
        roles = list(dict.fromkeys(role for role in (*available, *self.baseline_cast) if role))
        for role in self.buttons.keys() - set(roles):
            button = self.buttons.pop(role)
            self.flow.removeWidget(button)
            button.hide()
            button.deleteLater()
        for role in roles:
            if role not in self.buttons:
                button = role_button(self.roster, role, self)
                button.setCheckable(True)
                button.toggled.connect(lambda checked, item=button: self.toggle_role(item, checked))
                button.setContextMenuPolicy(Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(
                    lambda position, actor=role: self.show_role_menu(actor, position)
                )
                # 始终插在末尾「＋」按钮之前
                self.flow.insertWidget(self.flow.count() - 1, button)
                # Qt 会延后显示新子控件。先显式显示，让本轮高度计算包含它。
                button.show()
                self.buttons[role] = button
            button = self.buttons[role]
            button.setToolTip("" if role in available else "本句已保存的出场角色")
            button.blockSignals(True)
            button.setChecked(role in selected)
            button.blockSignals(False)
        self.refresh_button_states()

    def show_role_menu(self, role, position):
        """从角色胶囊打开名单操作，沿用剧本角色选择菜单的样式。"""
        button = self.buttons[role]
        menu = QMenu(self)
        menu.setObjectName("multiCharInsertMenu")
        action = menu.addAction(delete_message_icon(), "从剧本可用角色中移除")
        action.setEnabled(self.roster.can_remove(role))
        if not action.isEnabled():
            menu.setToolTipsVisible(True)
            action.setToolTip("开场角色由剧本保留" if role in self.roster.opening else "已移出可用角色名单")
        fit_menu_to_contents(menu)
        chosen = menu.exec_(button.mapToGlobal(position))
        menu.deleteLater()
        if chosen is action and self.roster.remove(role, self.save):
            self.sync_buttons(self.selected())
            self.changed.emit()
            self.roster_changed.emit()

    def refresh_button_states(self):
        """根据预期左右槽位刷新胶囊文案与圆点图标，双人时标出左/右站位。"""
        arranged = arrange_cast(self.baseline_cast, self.selected())
        left_role, right_role = arranged
        two_on_stage = bool(left_role and right_role)
        for role, button in self.buttons.items():
            label = button.property("roleLabel") or self.roster.label(role)
            color_hex = button.property("roleColorHex") or _character_pill_color(label).name()
            if button.isChecked():
                # 已点亮时胶囊填满主题色，隐藏圆点；双人出场显示紧凑的「左·/右·」站位标识
                button.setIcon(QIcon())
                if two_on_stage and role == left_role:
                    button.setText(f"左·{label}")
                elif two_on_stage and role == right_role:
                    button.setText(f"右·{label}")
                else:
                    button.setText(label)
            else:
                button.setText(label)
                button.setIcon(color_dot_icon(color_hex))
                button.setIconSize(QSize(10, 10))
        self.flow.invalidate()
        self._sync_height()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_height()

    def resizeEvent(self, event):
        """宽度变化引起流式换行时同步锁定精确高度，避免被 QHBoxLayout 裁切或被下方确认条遮挡。"""
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self):
        width = self.width()
        if width <= 0:
            width = max(1, self.sizeHint().width())
        # 先激活流式布局确保所有按钮的 sizeHint 反映最新文案，再精确锁定容器高度
        self.flow.activate()
        height = self.flow.heightForWidth(width)
        if height > 0 and (self.minimumHeight() != height or self.maximumHeight() != height):
            self.setFixedHeight(height)
        self.updateGeometry()

    def toggle_role(self, button, checked):
        if checked and len(self.selected()) > 2:
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
            QMessageBox.information(self, "出场角色", "舞台最多同时出场两位角色。")
        self.refresh_button_states()
        self.changed.emit()

    def add_role(self):
        """通过同主题弹出菜单一键加入已安装角色，替代繁琐的原生下拉弹窗。"""
        available = self.roster.role_ids()
        candidates = [role for role in self.roster.characters if role not in available]
        if not candidates:
            QMessageBox.information(self, "加入剧本角色", "已安装的角色都已加入剧本。")
            return
        menu = QMenu(self)
        menu.setObjectName("multiCharInsertMenu")
        actions = {}
        for role in candidates:
            label, color_hex, _ = _role_colors(self.roster, role)
            action = menu.addAction(color_dot_icon(color_hex), label)
            actions[action] = role
        fit_menu_to_contents(menu)
        anchor = self.add_button.mapToGlobal(QPoint(0, self.add_button.height() + 2))
        chosen = menu.exec_(anchor)
        role = actions.get(chosen)
        if role is not None and self.roster.add(role, self.save):
            # 保留当前已点选的草稿状态，只把新角色以未点亮形式追加到流式列表
            self.sync_buttons(self.selected())
            self.roster_changed.emit()


class MultiCharInsertDialog(QDialog):
    """允许插入角色台词或不绑定角色的静音文本。"""

    def __init__(self, names, current_character, parent=None, *, roster=None, previous=None, save=None):
        """沿用编排器主题，初始化角色、原文、翻译和静音控件。"""
        super().__init__(parent)
        self.setObjectName("multiCharInsertDialog")
        self.setWindowTitle("添加台词")
        # 主控和编排器使用同一表单主题，避免主控的大号字体改变弹窗布局。
        screen = QWidget.screen(parent) if parent is not None else QApplication.primaryScreen()
        screen = screen or QApplication.primaryScreen()
        font_pixels = max(14, min(36, round(screen.availableGeometry().height() * 0.016)))
        font = QFont(QApplication.font())
        font.setPixelSize(font_pixels)
        self.setFont(font)
        self.setStyleSheet(editor_stylesheet(font_pixels, names))
        # 先应用自己的 QSS，再据字体计算固定输入框与图标尺寸，防止沿用父窗字号。
        self.ensurePolished()
        self.resize(round(self.fontMetrics().height() * 25), 0)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.speaker = QComboBox()
        self.roster = roster
        self.previous = previous
        self.speaker.setObjectName("multiCharInsertSpeaker")
        if roster is None:
            for name in names:
                self.speaker.addItem(name, name)
            self.speaker.addItem("无", "")
            self.speaker.setCurrentIndex(max(0, self.speaker.findData(current_character)))
        else:
            self.update_speakers(current_character)
        form.addRow("说话角色", self.speaker)
        if roster is not None:
            self.cast_buttons = CastButtons(roster, previous, save, self)
            self.cast_buttons.roster_changed.connect(self.refresh_speakers)
            form.addRow("出场角色", self.cast_buttons)
        self.original = QPlainTextEdit()
        self.original.setObjectName("multiCharOriginalInput")
        self.original.setPlaceholderText("填写原文（必填）")
        self.translation = QPlainTextEdit()
        self.translation.setObjectName("multiCharTranslationInput")
        self.translation.setPlaceholderText("翻译（可选）")
        for field in (self.original, self.translation):
            field.setFixedHeight(round(self.fontMetrics().height() * 4))
        form.addRow("原文", self.original)
        form.addRow("翻译", self.translation)
        self.muted = QCheckBox("本句静音")
        set_button_icon(self.muted, "mute", self)
        self.muted.setObjectName("multiCharMuted")
        self.reset_mute_default()
        form.addRow("", self.muted)
        root.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.confirm = self.buttons.button(QDialogButtonBox.Ok)
        self.confirm.setText("添加")
        set_button_icon(self.confirm, "add_white", self)
        self.confirm.setObjectName("multiCharSave")
        self.confirm.setEnabled(False)
        cancel = self.buttons.button(QDialogButtonBox.Cancel)
        cancel.setText("取消")
        set_button_icon(cancel, "discard", self)
        cancel.setProperty("discardAction", True)
        cancel.setObjectName("multiCharControl")
        root.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.original.textChanged.connect(self.validate_text)
        self.speaker.currentIndexChanged.connect(self.reset_mute_default)
        self.original.setFocus()

    def reset_mute_default(self, *_):
        """切换角色时使用对应默认值，无角色默认静音而角色台词默认有声。"""
        self.muted.setChecked(not bool(self.speaker.currentData()))

    def validate_text(self):
        """原文只有空白时禁止确认，避免插入没有内容的占位消息。"""
        self.confirm.setEnabled(bool(self.original.toPlainText().strip()))

    def message_data(self):
        """返回普通消息的数据，不生成额外的旁白标志或伪角色。"""
        speaker = self.speaker.currentData()
        return {
            "character_name": self.roster.label(speaker) if self.roster and speaker else speaker,
            **({"speaker_id": speaker} if self.roster and speaker else {}),
            "text": self.original.toPlainText(),
            "translation": self.translation.toPlainText(),
            "muted": self.muted.isChecked(),
            "emotion": "normal",
            "audio_path": "",
        }

    def selected_cast(self):
        return arrange_cast(self.previous, self.cast_buttons.selected())

    def refresh_speakers(self):
        """名单移除使当前说话人失效时，回到可重新选择的无角色状态。"""
        current = self.speaker.currentData()
        self.update_speakers(current)
        if self.speaker.findData(current) < 0:
            self.speaker.setCurrentIndex(self.speaker.findData(""))

    def update_speakers(self, current):
        """用稳定身份维持选择，出场角色优先列在前面。"""
        roles = list(dict.fromkeys(role for role in (*self.previous, *self.roster.role_ids()) if role))
        self.speaker.blockSignals(True)
        self.speaker.clear()
        for role in roles:
            self.speaker.addItem(self.roster.label(role), role)
        self.speaker.addItem("无", "")
        index = self.speaker.findData(current)
        self.speaker.setCurrentIndex(index if index >= 0 else max(0, self.speaker.findText(current)))
        self.speaker.blockSignals(False)
