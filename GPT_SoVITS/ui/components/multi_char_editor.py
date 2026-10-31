"""小剧场独占编排会话：两个非模态窗口共用草稿和现有 Live2D 前台。"""
from __future__ import annotations

import html
import json
import uuid
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, QMimeData, QPoint, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QFont, QIcon, QImageReader
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedLayout, QFrame, QLabel, QTabBar,
                             QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QListView, QPushButton,
                             QMessageBox, QDoubleSpinBox, QSlider, QApplication, QStyle, QSizePolicy,
                             QCheckBox, QFileDialog, QMenu, QToolButton, QToolTip, QDialog)

from chat.chat import Message
from performance_types import trim_timeline, sort_timeline_events, timeline_tracks, valid_timeline_events
from live2d_support.multi_char_resources import TheaterResources, audio_span, assign_colors
from live2d_support.multi_char_scene import (
    SceneTiming, background_root, background_path, resolve_scene, transition_from_total,
)
from ui.components.multi_char_insert_dialog import MultiCharInsertDialog
from ui.components.multi_char_icons import multi_char_icon, set_button_icon
from ui.components.multi_char_timeline import MultiCharTimeline, MIME, _character_pill_color
from ui.components.multi_char_windows import MultiCharWindowLayout
from ui_constants import char_info_json


def character_badge_color(name):
    """让说话人标签与出场胶囊、轨道头使用同一角色配置原色。"""
    return _character_pill_color(name)


def character_badge_text_color(background):
    """根据背景亮度选择深色或白色文字，不为适配文字改动主题色。"""
    brightness = background.red() * 0.299 + background.green() * 0.587 + background.blue() * 0.114
    return "#334155" if brightness >= 145 else "#FFFFFF"


class SceneSummaryLabel(QLabel):
    """单行场景摘要按可用宽度省略，悬浮时显示完整内容。"""

    def set_summary(self, text):
        """同时更新完整提示与当前宽度下的摘要。"""
        self.setToolTip(text)
        self.setText(self.fontMetrics().elidedText(text, Qt.ElideRight, self.width()))

    def resizeEvent(self, event):
        """窗口宽度变化时重新省略摘要，不增加卡片高度。"""
        super().resizeEvent(event)
        self.set_summary(self.toolTip())


class ResourceList(QListWidget):
    """资源单击试播，超过拖动阈值后只传递资源引用。"""

    def __init__(self, session):
        """复用 Qt 原生单击与拖拽判定，避免按下时抢先试播。"""
        super().__init__()
        self.session = session
        self.column_timer = QTimer(self)
        self.column_timer.setSingleShot(True)
        self.column_timer.timeout.connect(self.update_columns)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setMovement(QListView.Static)
        # Static 会关闭 dragEnabled；布局选项设置后再开启跨窗口拖出，
        # 同时禁止资源列表接收拖入，避免改变资源顺序。
        self.setDragDropMode(QListView.DragOnly)
        self.setResizeMode(QListView.Adjust)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSpacing(0)
        self.itemClicked.connect(self.preview)
        self.drag_hint = QLabel("可拖入下方轨道编排演出", self.viewport())
        self.drag_hint.setObjectName("multiCharHint")
        self.drag_hint.setAlignment(Qt.AlignCenter)
        self.drag_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.drag_hint.hide()

    def resizeEvent(self, event):
        """合并视口尺寸变化，在本轮 Qt 布局结束后更新两列。"""
        super().resizeEvent(event)
        self.column_timer.start(0)
        self.position_drag_hint()

    def position_drag_hint(self):
        """将提示叠在视口底边，不占资源行且不截获拖放鼠标事件。"""
        height = self.drag_hint.sizeHint().height()
        viewport = self.viewport()
        self.drag_hint.setGeometry(0, max(0, viewport.height() - height), viewport.width(), height)
        self.drag_hint.raise_()

    def update_columns(self):
        """始终预留右侧滚动条宽度，消除显隐引起的两列布局反馈循环。"""
        self.column_timer.stop()
        # IconMode 换行会预留滚动条空间。用不含滚动条的最大视口减去
        # 固定槽宽，避免当前视口宽度随滚动条显隐来回改变列宽。
        scroll_width = self.verticalScrollBar().sizeHint().width()
        width = max(1, (self.maximumViewportSize().width() - scroll_width - 2) // 2)
        height = round(self.fontMetrics().height() * 1.8)
        grid = QSize(width, height)
        changed = self.gridSize() != grid
        if changed:
            self.setGridSize(grid)
        item_size = QSize(max(1, width - 2), height)
        for index in range(self.count()):
            item = self.item(index)
            if item.sizeHint() != item_size:
                item.setSizeHint(item_size)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                changed = True
        if changed:
            self.doItemsLayout()

    def preview(self, item):
        """将点击资源交给同一 Live2D 预览会话。"""
        payload = item.data(Qt.UserRole)
        self.session.preview_resource(payload)

    def startDrag(self, actions):
        """向另一个 Qt 顶层窗口发起带会话标识的资源拖放。"""
        item = self.currentItem()
        if item is None:
            return
        self.session.prepare_edit()
        payload = dict(item.data(Qt.UserRole), session=self.session.session_id)
        mime = QMimeData()
        mime.setData(MIME, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.session.drag_hint.setVisible(True)
        try:
            drag.exec_(Qt.CopyAction)
        finally:
            self.session.drag_hint.setVisible(False)


class EditorWindow(QWidget):
    """资源和时间轴共用关闭入口，不允许只关闭一半会话。"""

    def __init__(self, session, frameless=False):
        """建立独立非模态窗口，原控制台被禁用不会阻塞此窗口。"""
        super().__init__(None, Qt.Window | (Qt.FramelessWindowHint if frameless else Qt.WindowFlags()))
        self.session = session
        self.setWindowTitle("演出编排")
        self.setWindowIcon(session.owner.windowIcon())
        self.setObjectName("multiCharTimelineWindow" if frameless else "multiCharResourceWindow")
        screen = QWidget.screen(session.owner) or QApplication.primaryScreen()
        font_pixels = max(14, min(36, round(screen.availableGeometry().height() * 0.016)))
        font = QFont(session.owner.font())
        font.setPixelSize(font_pixels)
        self.setFont(font)
        stylesheet = Path(__file__).parents[1] / "styles" / "multi_char_editor.qss"
        # 使用逻辑像素，由 Qt 负责 DPI 缩放，避免继承全局大号点字体。
        style = stylesheet.read_text(encoding="utf-8-sig").replace("@FONT_PX@", str(font_pixels))
        style = style.replace("@SMALL_PX@", str(round(font_pixels * 0.85)))
        style = style.replace("@PAD_Y@", str(round(font_pixels * 0.25)))
        style = style.replace("@PAD_X@", str(round(font_pixels * 0.6)))
        for index, name in enumerate(session.names):
            color = _character_pill_color(name)
            style = style.replace(f"@ROLE_{index}_COLOR@", color.name())
            style = style.replace(f"@ROLE_{index}_TEXT@", character_badge_text_color(color))
        check_icon = Path(__file__).parents[2] / "icons" / "multi_char_check.svg"
        style = style.replace("@CHECK_ICON@", check_icon.as_posix())
        self.setStyleSheet(session.owner.styleSheet() + "\n" + style)

    def closeEvent(self, event):
        """任何系统关闭请求均由会话处理保存与双窗口退出。"""
        if self.session.closed:
            event.accept()
        else:
            event.ignore()
            self.session.request_close()

    def changeEvent(self, event):
        """同步两个新窗口的最小化状态，避免孤立的无标题栏时间轴。"""
        super().changeEvent(event)
        if event.type() == event.WindowStateChange and not self.session.closed:
            other = self.session.timeline_window if self is self.session.resource_window else self.session.resource_window
            if other.isMinimized() != self.isMinimized():
                other.setWindowState(self.windowState())
            if self is self.session.resource_window and hasattr(self.session, "layout"):
                self.session.layout.minimize_stage(self.isMinimized())


class MultiCharEditor(QObject):
    """管理进入即初始化、编辑草稿、跨进程预览及退出恢复。"""

    expressionResult = pyqtSignal(object)

    def __init__(self, owner, chat, message, slots, facing="screen"):
        """建立共享会话并持久初始化首次时间轴，失败不进入编辑。"""
        super().__init__(owner)
        self.owner = owner
        self.chat = chat
        self.message = message
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.names = [slot["character_name"] for slot in slots]
        self.slots = slots
        self.resources = {}
        for index, slot in enumerate(slots):
            direction = ("L" if index == 0 else "R") if facing == "face_to_face" else "C"
            self.resources[slot["character_name"]] = TheaterResources(slot.get("model_json_path"), direction)
        self.palette = [item["theme_color"] for item in char_info_json.values() if item.get("theme_color")]
        self.span = audio_span(message.audio_path, message.text)
        self.initialize_message(message)
        self.baseline = deepcopy(message.performance_timeline)
        self.draft = deepcopy(self.baseline)
        assign_colors(self.draft, self.palette)
        self.closed = False
        self.closing = False
        self.playing = False
        self.transition_playing = False
        self.can_resume = False
        self.position = 0
        self.seek_resume = None
        self.selection = None
        self.stage_available = True
        self.audio_busy = False
        self.text_editing = False
        self.expression_dialog = None
        self.resource_window = EditorWindow(self)
        self.timeline_window = EditorWindow(self, True)
        self.build_resources()
        self.build_timeline()
        for window in (self.resource_window, self.timeline_window):
            for button in window.findChildren(QPushButton):
                if not button.objectName():
                    button.setObjectName("multiCharSave" if button.text() == "保存" else "multiCharControl")
                button.style().unpolish(button)
                button.style().polish(button)
        self.layout = MultiCharWindowLayout(owner, self.resource_window, self.timeline_window, self.send)
        owner.setEnabled(False)
        self.resource_window.show()
        self.timeline_window.show()
        self.layout.bind(None)
        self.refresh_resources()
        self.read_waveform()
        self.send("enter")
        if self.span.missing:
            self.status.setText("音频缺失/损坏：临时无声预览，保留所有原编排。")
        else:
            self.show_validation()

    def initialize_message(self, message):
        """校验目标消息并持久初始化首次编排，失败时保留原消息。"""
        if message.performance_timeline is None:
            message.performance_timeline = self.initial_timeline(message)
            if not self.owner._save_chat():
                message.performance_timeline = None
                raise RuntimeError("时间轴初始化未能保存")
        if not isinstance(message.performance_timeline, dict):
            raise ValueError("时间轴格式损坏；已保留原数据，请先修复存档。")
        groups = message.performance_timeline.get("characters", {})
        if not isinstance(groups, dict):
            raise ValueError("角色轨道格式损坏；已保留原数据。")
        for name in self.names:
            tracks = groups.get(name, {})
            if not isinstance(tracks, dict) or any(
                not isinstance(tracks.get(kind, []), list) for kind in ("motions", "expressions")
            ):
                raise ValueError(f"{name} 的轨道格式损坏；已保留原数据。")

    def initial_timeline(self, message):
        """沿用原自动演出生成首次双人轨道，无角色消息从空轨道开始。"""
        timeline = {"characters": {name: {"motions": [], "expressions": []} for name in self.names}}
        if message.character_name in self.resources:
            performance = message.performance.as_dict() if message.performance else None
            timeline["characters"][message.character_name] = self.resources[message.character_name].defaults(
                performance, message.emotion.as_string(),
            )
        assign_colors(timeline, self.palette)
        return timeline

    def _transport_icon(self, kind):
        """复用主题蓝 SVG 图标表示播放、暂停和停止状态。"""
        return multi_char_icon(kind)

    def message_index(self):
        """按对象身份定位当前句，避免内容相同的消息被误认为同一句。"""
        return next((index for index, message in enumerate(self.chat.message_list)
                     if message is self.message), -1)

    def update_navigation(self):
        """首尾禁用对应切句按钮，消息被删除时禁用两端。"""
        index = self.message_index()
        self.previous.setEnabled(not self.audio_busy and index > 0)
        self.next.setEnabled(not self.audio_busy and 0 <= index < len(self.chat.message_list) - 1)
        self.add_message_button.setEnabled(not self.audio_busy and index >= 0)
        self.message_count.setText(f"{index + 1 if index >= 0 else 0}/{len(self.chat.message_list)}")

    def resolve_message_drafts(self):
        """切句或插句前处理文本与时间轴草稿，取消时留在原句。"""
        if self.audio_busy or not self.resolve_text_draft():
            return False
        self.prepare_edit()
        if self.draft != self.baseline:
            dialog = QMessageBox(self.resource_window)
            dialog.setWindowTitle("切换台词")
            dialog.setText("当前句有未保存的编排。")
            save = dialog.addButton("保存并切换", QMessageBox.AcceptRole)
            discard = dialog.addButton("放弃并切换", QMessageBox.DestructiveRole)
            dialog.addButton("取消", QMessageBox.RejectRole)
            dialog.exec_()
            chosen = dialog.clickedButton()
            if chosen is save:
                if not self.save_current():
                    return False
            elif chosen is not discard:
                return False
        return True

    def switch_message(self, step):
        """处理当前草稿后原位切句，更新音频与四轨并隔离旧预览回执。"""
        index = self.message_index()
        target_index = index + step
        if index < 0 or not 0 <= target_index < len(self.chat.message_list):
            self.update_navigation()
            return
        if self.resolve_message_drafts():
            self.show_message(self.chat.message_list[target_index])

    def show_message(self, target):
        """在现有两个窗口中切换已确认的消息，保持同一舞台编辑会话。"""
        try:
            self.initialize_message(target)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self.resource_window, "无法切换台词", str(error))
            return
        self.send("stop")
        self.message = target
        self.span = audio_span(target.audio_path, target.text)
        self.baseline = deepcopy(target.performance_timeline)
        self.draft = deepcopy(self.baseline)
        assign_colors(self.draft, self.palette)
        # 新句使用新预览标识，旧句的迟到位置和错误回执均不再接收。
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.position = 0
        self.playing = False
        self.transition_playing = False
        self.can_resume = False
        self.seek_resume = None
        self.select_event(None)
        self.view.selection = None
        self.view.drag = None
        self.view.press = None
        self.view.ghost = None
        self.view.snap_x = None
        self.view.ruler_drag = False
        self.time_input.setRange(0, max(0, (self.span.duration_ms - 1) / 1000))
        self.time_input.setValue(0)
        self.status.clear()
        self.show_saved_text()
        self.sync_scene_controls()
        self.update_navigation()
        self.read_waveform()
        self.view.rebuild()
        self.view.horizontalScrollBar().setValue(0)
        self.update_clock()
        self.show_validation()
        if self.span.missing:
            self.status.setText("音频缺失/损坏：临时无声预览，保留所有原编排。")
        self.send("enter")

    def show_insert_menu(self):
        """在添加按钮旁选择插入位置，不增加常驻的两组按钮。"""
        if self.audio_busy:
            return
        menu = QMenu(self.resource_window)
        menu.setObjectName("multiCharInsertMenu")
        above = menu.addAction("本句之前")
        below = menu.addAction("本句之后")
        anchor = self.add_message_button.mapToGlobal(QPoint(0, self.add_message_button.height()))
        chosen = menu.exec_(anchor)
        if chosen is above or chosen is below:
            self.insert_message(after=chosen is below)

    def insert_message(self, after):
        """确认草稿和表单后插入普通消息，保存成功才切到新台词。"""
        if self.audio_busy or self.message_index() < 0 or not self.resolve_message_drafts():
            return
        dialog = MultiCharInsertDialog(self.names, self.message.character_name, self.resource_window)
        try:
            if dialog.exec_() != QDialog.Accepted:
                return
            data = dialog.message_data()
        finally:
            dialog.deleteLater()
        if not data["text"].strip():
            return
        index = self.message_index() + (1 if after else 0)
        target = Message.from_dict(data)
        # 新句沿用首次编排的初始化规则，场景保持缺省，从前句自然继承。
        target.performance_timeline = self.initial_timeline(target)
        self.chat.message_list.insert(index, target)
        if not self.owner._save_chat():
            self.chat.message_list.pop(index)
            return
        self.owner.refresh_chat_display()
        self.owner.refresh_chat_list()
        self.show_message(target)

    def build_resources(self):
        """搭建紧凑资源窗口，省去逐行预览/插入/替换按钮。"""
        root = QVBoxLayout(self.resource_window)
        spacing = round(self.resource_window.font().pixelSize() * 0.42)
        root.setContentsMargins(spacing, spacing, spacing, spacing)
        root.setSpacing(spacing)
        card = QFrame()
        card.setObjectName("multiCharDialogueCard")
        card_layout = QVBoxLayout(card)
        card_pad = round(spacing * 0.75)
        card_layout.setContentsMargins(card_pad, card_pad, card_pad, card_pad)
        card_layout.setSpacing(round(spacing * 0.35))
        title = QLabel(self.message.character_name)
        self.speaker_label = title
        title.setObjectName("multiCharSpeaker")
        title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        title.setVisible(bool(self.message.character_name))
        badge_bg = character_badge_color(self.message.character_name)
        title.setStyleSheet(
            f"background-color: {badge_bg.name()}; color: {character_badge_text_color(badge_bg)};"
            "border-radius: 8px; padding: 2px 12px; font-weight: bold;"
        )
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        self.previous = QToolButton()
        self.next = QToolButton()
        self.add_message_button = QToolButton()
        navigation = (
            (self.previous, "previous", "上一句", lambda: self.switch_message(-1)),
            (self.next, "next", "下一句", lambda: self.switch_message(1)),
            (self.add_message_button, "add", "添加台词", self.show_insert_menu),
        )
        for button, icon, description, callback in navigation:
            button.setObjectName("multiCharNavigation")
            button.setToolTip(description)
            button.setAccessibleName(description)
            button.setCursor(Qt.PointingHandCursor)
            set_button_icon(button, icon, self.resource_window)
            button.clicked.connect(callback)
        self.add_message_button.setText("添加台词")
        self.add_message_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.message_count = QLabel()
        self.message_count.setObjectName("multiCharMessageCount")
        self.message_count.setAlignment(Qt.AlignCenter)
        self.message_count.setAccessibleName("当前台词位置")
        header.addWidget(self.previous)
        header.addWidget(self.message_count)
        header.addWidget(self.next)
        header.addWidget(self.add_message_button)
        card_layout.addLayout(header)
        text_html = f"<div style='color:#243046; line-height:1.25;'>{html.escape(self.message.text or '')}</div>"
        if self.message.translation:
            text_html += (
                f"<div style='color:#6B7C96; margin-top:3px; line-height:1.25;'>"
                f"{html.escape(self.message.translation)}</div>"
            )
        label = QLabel(text_html)
        self.dialogue_label = label
        label.setObjectName("multiCharDialogue")
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        card_layout.addWidget(label)
        self.original_input = QPlainTextEdit()
        self.original_input.setObjectName("multiCharOriginalInput")
        self.original_input.setPlaceholderText("原文")
        self.translation_input = QPlainTextEdit()
        self.translation_input.setObjectName("multiCharTranslationInput")
        self.translation_input.setPlaceholderText("中文翻译")
        for field in (self.original_input, self.translation_input):
            field.setFixedHeight(round(self.resource_window.font().pixelSize() * 3.8))
            card_layout.addWidget(field)
            field.hide()
        self.text_edit_actions = QWidget()
        self.text_edit_actions.setObjectName("multiCharTextEditActions")
        self.text_edit_layout = QHBoxLayout(self.text_edit_actions)
        self.text_edit_layout.setContentsMargins(0, 0, 0, 0)
        self.discard_text_button = QPushButton("放弃编辑")
        self.discard_text_button.setObjectName("multiCharControl")
        set_button_icon(self.discard_text_button, "discard", self.resource_window)
        self.discard_text_button.setProperty("discardAction", True)
        self.discard_text_button.clicked.connect(self.discard_text_edit)
        self.text_edit_layout.addWidget(self.discard_text_button)
        self.text_edit_layout.addStretch(1)
        self.discard_text_button.hide()
        card_layout.addWidget(self.text_edit_actions)
        self.text_edit_actions.hide()
        self.text_actions = QHBoxLayout()
        self.text_button = QPushButton("编辑文本")
        self.text_button.setObjectName("multiCharTextAction")
        set_button_icon(self.text_button, "edit", self.resource_window)
        self.text_button.clicked.connect(self.toggle_text_edit)
        self.text_actions.addWidget(self.text_button)
        self.regenerate_button = QPushButton("重新生成音频")
        self.regenerate_button.setObjectName("multiCharTextAction")
        set_button_icon(self.regenerate_button, "audio", self.resource_window)
        self.regenerate_button.setEnabled(bool(self.message.character_name))
        self.regenerate_button.clicked.connect(self.regenerate_audio)
        self.text_actions.addWidget(self.regenerate_button)
        self.muted_check = QToolButton()
        self.muted_check.setText("本句静音")
        self.muted_check.setObjectName("multiCharMuteAction")
        self.muted_check.setCheckable(True)
        self.muted_check.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.muted_check.setToolTip("仅静音本句，保留原音频和时长")
        set_button_icon(self.muted_check, "mute", self.resource_window)
        self.muted_check.toggled.connect(self.change_mute)
        self.text_actions.addWidget(self.muted_check)
        self.text_actions.addStretch(1)
        card_layout.addLayout(self.text_actions)
        root.addWidget(card)
        root.addWidget(self.build_scene_settings())
        self.characters = QTabBar()
        self.characters.setObjectName("multiCharCharacters")
        self.characters.setDrawBase(False)
        self.characters.setExpanding(True)
        self.characters.setCursor(Qt.PointingHandCursor)
        for name in self.names:
            self.characters.addTab(name)
        root.addWidget(self.characters)
        bar = QHBoxLayout()
        bar.setSpacing(round(spacing * 0.6))
        self.types = QTabBar()
        self.types.setObjectName("multiCharKinds")
        self.types.setDrawBase(False)
        self.types.setCursor(Qt.PointingHandCursor)
        self.types.addTab("动作")
        self.types.addTab("表情")
        bar.addWidget(self.types)
        self.search = QLineEdit()
        self.search.setObjectName("multiCharSearch")
        self.search.setPlaceholderText("搜索名称、文件名、描述")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(multi_char_icon("search"), QLineEdit.LeadingPosition)
        bar.addWidget(self.search, 1)
        self.refresh_button = QToolButton()
        self.refresh_button.setObjectName("multiCharRefresh")
        self.refresh_button.setToolTip("刷新资源")
        self.refresh_button.setAccessibleName("刷新资源")
        icon_path = Path(__file__).parents[2] / "icons" / "refresh.svg"
        self.refresh_button.setIcon(QIcon(str(icon_path)))
        icon_size = round(self.resource_window.font().pixelSize() * 1.15)
        self.refresh_button.setIconSize(QSize(icon_size, icon_size))
        self.refresh_button.clicked.connect(self.reload_resources)
        bar.addWidget(self.refresh_button)
        self.custom = QToolButton()
        self.custom.setObjectName("multiCharCustomExpression")
        self.custom.setToolTip("自定义表情")
        self.custom.setAccessibleName("自定义表情")
        set_button_icon(self.custom, "add", self.resource_window)
        self.custom.clicked.connect(self.custom_expression)
        bar.addWidget(self.custom)
        root.addLayout(bar)
        self.list = ResourceList(self)
        self.list.setObjectName("multiCharResourceList")
        root.addWidget(self.list, 1)
        self.drag_hint = self.list.drag_hint
        self.update_navigation()
        self.characters.currentChanged.connect(self.refresh_resources)
        self.types.currentChanged.connect(self.refresh_resources)
        self.search.textChanged.connect(self.refresh_resources)

    def build_scene_settings(self):
        """建立默认折叠的场景卡；展开后三行控件按固定标签列对齐。"""
        card = QFrame()
        card.setObjectName("multiCharSceneCard")
        root = QVBoxLayout(card)
        pad = round(self.resource_window.font().pixelSize() * 0.35)
        root.setContentsMargins(pad, pad, pad, pad)
        root.setSpacing(pad)
        header = QHBoxLayout()
        self.scene_toggle = QToolButton()
        self.scene_toggle.setObjectName("multiCharSceneToggle")
        self.scene_toggle.setText("场景配置")
        self.scene_toggle.setCheckable(True)
        self.scene_toggle.setArrowType(Qt.RightArrow)
        self.scene_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.scene_toggle.setToolTip("展开本句场景设置")
        self.scene_toggle.toggled.connect(self.toggle_scene_details)
        header.addWidget(self.scene_toggle, 0, Qt.AlignLeft)
        self.scene_summary = SceneSummaryLabel()
        self.scene_summary.setObjectName("multiCharSceneSummary")
        self.scene_summary.setTextFormat(Qt.PlainText)
        self.scene_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.scene_summary, 1)
        root.addLayout(header)
        self.scene_body = QWidget()
        self.scene_body.setObjectName("multiCharSceneBody")
        grid = QGridLayout(self.scene_body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(pad * 2)
        grid.setVerticalSpacing(pad)
        grid.setColumnStretch(1, 1)
        for row, (text, icon) in enumerate((
                ("出场角色", "actors"), ("切换背景", "background"), ("句前转场", "transition"))):
            label_row = QHBoxLayout()
            image = QLabel()
            image.setObjectName("multiCharSceneIcon")
            icon_size = round(self.resource_window.fontMetrics().height() * 0.85)
            image.setPixmap(multi_char_icon(icon).pixmap(icon_size, icon_size))
            label = QLabel(text)
            label.setObjectName("multiCharSceneLabel")
            label_row.addWidget(image)
            label_row.addWidget(label)
            label_row.addStretch(1)
            grid.addLayout(label_row, row, 0)
        visible_row = QHBoxLayout()
        visible_row.setSpacing(pad)
        self.visible_checks = {}
        for index, name in enumerate(self.names):
            check = QPushButton(name)
            check.setObjectName("multiCharVisiblePill")
            check.setProperty("characterSlot", str(index))
            check.setCheckable(True)
            check.setCursor(Qt.PointingHandCursor)
            check.toggled.connect(self.change_visible_characters)
            self.visible_checks[name] = check
            visible_row.addWidget(check)
        self.visibility_help = QToolButton()
        self.visibility_help.setObjectName("multiCharVisibilityHelp")
        self.visibility_help.setAccessibleName("出场角色沿用规则")
        self.visibility_help.setAutoRaise(True)
        help_icon = Path(__file__).parents[2] / "icons" / "multi_char_help.svg"
        self.visibility_help.setIcon(QIcon(str(help_icon)))
        icon_size = round(self.resource_window.font().pixelSize() * 1.15)
        self.visibility_help.setIconSize(QSize(icon_size, icon_size))
        self.visibility_help.setToolTip(
            "本句的出场角色设置会延续到后面的所有台词，直到后面的某句再次设置。\n"
            "但本句之前的台词不会受影响。"
        )
        self.visibility_help.clicked.connect(self.show_visibility_help)
        visible_row.addWidget(self.visibility_help)
        visible_row.addStretch(1)
        grid.addLayout(visible_row, 0, 1)

        background_row = QHBoxLayout()
        background_row.setSpacing(pad * 2)
        self.background_check = QCheckBox("选择图片")
        self.background_check.setObjectName("multiCharBackground")
        self.background_check.toggled.connect(self.change_background_enabled)
        background_row.addWidget(self.background_check)
        self.background_file = QToolButton()
        set_button_icon(self.background_file, "background", self.resource_window)
        self.background_file.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.background_file.setObjectName("multiCharBackgroundFile")
        self.background_file.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.background_file.setToolTip("重新选择背景图片")
        self.background_file.clicked.connect(self.choose_background)
        background_row.addWidget(self.background_file, 1)
        grid.addLayout(background_row, 1, 1)

        transition_row = QHBoxLayout()
        transition_row.setSpacing(pad * 2)
        self.black_check = QCheckBox("黑场过渡")
        self.black_check.setObjectName("multiCharBlack")
        self.black_check.toggled.connect(self.change_black_enabled)
        transition_row.addWidget(self.black_check)
        self.black_duration = QSlider(Qt.Horizontal)
        self.black_duration.setObjectName("multiCharBlackDuration")
        self.black_duration.setRange(500, 4000)
        self.black_duration.setSingleStep(100)
        self.black_duration.setPageStep(500)
        self.black_duration.setValue(2000)
        self.black_duration.setFixedWidth(round(self.resource_window.font().pixelSize() * 9))
        self.black_duration.setToolTip("黑场总时长，包含淡出、全黑和淡入")
        self.black_duration.valueChanged.connect(self.update_black_duration_label)
        self.black_duration.sliderReleased.connect(self.commit_black_duration)
        transition_row.addWidget(self.black_duration)
        self.black_duration_label = QLabel("2.0 秒")
        self.black_duration_label.setObjectName("multiCharBlackDurationLabel")
        self.black_duration_label.setMinimumWidth(round(self.resource_window.font().pixelSize() * 3.5))
        transition_row.addWidget(self.black_duration_label)
        transition_row.addStretch(1)
        self.transition_preview = QToolButton()
        self.transition_preview.setObjectName("multiCharTransitionPreview")
        self.transition_preview.setText("预览")
        self.transition_preview.setToolTip("预览整体效果")
        set_button_icon(self.transition_preview, "play", self.resource_window)
        self.transition_preview.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.transition_preview.clicked.connect(self.preview_transition)
        transition_row.addWidget(self.transition_preview)
        grid.addLayout(transition_row, 2, 1)
        root.addWidget(self.scene_body)
        self.scene_body.hide()
        self.sync_scene_controls()
        return card

    def toggle_scene_details(self, expanded):
        """切换场景卡详细控件与摘要，不修改数据或打断正在进行的预览。"""
        self.scene_body.setVisible(expanded)
        self.scene_summary.setVisible(not expanded)
        self.scene_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.scene_toggle.setToolTip("折叠本句场景设置" if expanded else "展开本句场景设置")

    def show_visibility_help(self):
        """点击问号时主动弹出与悬浮相同的出场角色沿用说明。"""
        button = self.visibility_help
        position = button.mapToGlobal(QPoint(0, button.height() + 4))
        QToolTip.showText(position, button.toolTip(), button)

    def sync_scene_controls(self):
        """只投影当前句的覆盖配置和继承后的上场状态，不自动补写字段。"""
        scene = self.message.scene_before if isinstance(self.message.scene_before, dict) else {}
        effective = resolve_scene(self.chat.message_list, self.message_index(), self.names)
        for name, check in self.visible_checks.items():
            check.blockSignals(True)
            check.setChecked(name in effective["visible_characters"])
            check.blockSignals(False)
        self.muted_check.blockSignals(True)
        self.muted_check.setChecked(self.message.muted)
        self.muted_check.blockSignals(False)
        filename = scene.get("background", "")
        self.background_check.blockSignals(True)
        self.background_check.setChecked(bool(filename))
        self.background_check.blockSignals(False)
        self.background_file.setText(filename)
        self.background_file.setToolTip(f"重新选择背景图片\n{filename}" if filename else "重新选择背景图片")
        self.background_file.setVisible(bool(filename))
        transition = scene.get("transition")
        transition = transition if isinstance(transition, dict) else {}
        enabled = transition.get("type") == "black"
        first = self.message_index() == 0
        self.black_check.blockSignals(True)
        self.black_check.setChecked(enabled)
        self.black_check.setEnabled(not first)
        self.black_check.setToolTip("首句不能添加黑场过渡" if first else "")
        self.black_check.blockSignals(False)
        self.black_duration.blockSignals(True)
        duration = SceneTiming.from_scene(scene).duration_ms
        self.black_duration.setValue(duration if enabled else 2000)
        self.black_duration.setVisible(enabled)
        self.black_duration.setEnabled(enabled and not first)
        self.black_duration.blockSignals(False)
        self.black_duration_label.setText(f"{self.black_duration.value() / 1000:.1f} 秒")
        self.black_duration_label.setVisible(enabled)
        self.black_duration_label.setEnabled(enabled and not first)
        self.transition_preview.setEnabled(not first and self.stage_available)
        self.regenerate_button.setEnabled(not self.audio_busy and bool(self.message.character_name))
        self.regenerate_button.setToolTip("本句是无角色消息，未指定语音角色" if not self.message.character_name else "")
        visible_names = "、".join(effective["visible_characters"]) or "无"
        background = filename or "沿用"
        black = f"黑场 {duration / 1000:.1f}s" if enabled and not first else "无"
        if first and enabled:
            black = "首句不可用"
        self.scene_summary.set_summary(f"出场：{visible_names} ｜ 背景图片：{background} ｜ 转场：{black}")

    def save_scene_value(self, key, value):
        """立即保存单项场景覆盖，失败回滚该次写入，不触碰时间轴草稿。"""
        old = self.message.scene_before
        scene = deepcopy(old) if isinstance(old, dict) else {}
        if value is None:
            scene.pop(key, None)
        else:
            scene[key] = value
        self.message.scene_before = scene or None
        if not self.owner._save_chat():
            self.message.scene_before = old
            self.sync_scene_controls()
            return False
        self.sync_scene_controls()
        self.owner.refresh_chat_display()
        self.apply_scene_preview()
        return True

    def apply_scene_preview(self):
        """取消正在进行的预览并显示新目标场景，保留游标且不自动续播。"""
        self.playing = False
        self.transition_playing = False
        self.can_resume = False
        self.seek_resume = None
        self.send("scene", position=self.position)
        self.update_clock()

    def change_visible_characters(self, *_):
        """保存明确的上场名单，全部取消时保留空数组表示零人。"""
        names = [name for name, check in self.visible_checks.items() if check.isChecked()]
        self.save_scene_value("visible_characters", names)

    def change_mute(self, checked):
        """立即切换本句静音，保留原音频和时间轴并取消当前预览。"""
        old = self.message.muted
        self.message.muted = checked
        if not self.owner._save_chat():
            self.message.muted = old
            self.sync_scene_controls()
            return
        self.owner.refresh_chat_display()
        self.apply_scene_preview()

    def change_background_enabled(self, checked):
        """勾选时打开系统选择器，取消勾选恢复继承背景。"""
        if checked:
            self.choose_background()
        else:
            self.save_scene_value("background", None)

    def choose_background(self):
        """校验根目录、图片类型与解码结果，只持久保存背景文件名。"""
        filename, _ = QFileDialog.getOpenFileName(
            self.resource_window, "选择背景图片", str(background_root()), "背景图片 (*.png *.jpg)",
        )
        if not filename:
            self.sync_scene_controls()
            return
        try:
            selected = Path(filename).resolve()
            if selected.parent != background_root().resolve():
                raise ValueError("请选择 live2d_related 根目录内的 PNG/JPG 图片。")
            path = background_path(selected.name)
            if QImageReader(str(path)).read().isNull():
                raise ValueError("图片无法读取，请选择有效的 PNG/JPG 图片。")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.resource_window, "背景图片不可用", str(error))
            self.sync_scene_controls()
            return
        self.save_scene_value("background", selected.name)

    def change_black_enabled(self, checked):
        """仅配置句前黑场，不因勾选而自动演示过渡。"""
        if self.message_index() == 0:
            return
        value = transition_from_total(self.black_duration.value()) if checked else None
        self.save_scene_value("transition", value)

    def update_black_duration_label(self, milliseconds):
        """拖动时仅实时更新秒数；键盘和滑轨点击完成一次调整后保存。"""
        self.black_duration_label.setText(f"{milliseconds / 1000:.1f} 秒")
        if not self.black_duration.isSliderDown():
            self.change_black_duration(milliseconds)

    def commit_black_duration(self):
        """拖动释放后一次提交黑场时长，避免拖动期间反复保存或停止预览。"""
        self.change_black_duration(self.black_duration.value())

    def change_black_duration(self, milliseconds):
        """以 100ms 为步长保存三阶段总时长，相同数值不重复写盘。"""
        if self.black_check.isChecked() and self.message_index() > 0:
            milliseconds = round(milliseconds / 100) * 100
            value = transition_from_total(milliseconds)
            self.black_duration.blockSignals(True)
            self.black_duration.setValue(milliseconds)
            self.black_duration.blockSignals(False)
            self.black_duration_label.setText(f"{milliseconds / 1000:.1f} 秒")
            current = self.message.scene_before or {}
            if current.get("transition") != value:
                self.save_scene_value("transition", value)

    def preview_transition(self):
        """只演示上一句场景到本句的句前阶段，结束后不启动本句语音。"""
        if self.audio_busy or not self.stage_available or self.message_index() <= 0:
            return
        self.playing = False
        self.can_resume = False
        self.transition_playing = True
        self.send("transition", position=self.position)
        self.update_clock()

    def show_saved_text(self):
        """恢复台词只读展示，不回滚已保存文本或时间轴草稿。"""
        self.text_editing = False
        self.text_edit_layout.removeWidget(self.text_button)
        self.text_actions.insertWidget(0, self.text_button)
        self.text_button.setText("编辑文本")
        self.text_button.setIcon(multi_char_icon("edit"))
        self.text_button.setObjectName("multiCharTextAction")
        self.text_button.style().unpolish(self.text_button)
        self.text_button.style().polish(self.text_button)
        self.text_button.show()
        self.discard_text_button.hide()
        self.text_edit_actions.hide()
        self.original_input.hide()
        self.translation_input.hide()
        self.dialogue_label.show()
        self.speaker_label.setText(self.message.character_name)
        self.speaker_label.setVisible(bool(self.message.character_name))
        badge = character_badge_color(self.message.character_name)
        self.speaker_label.setStyleSheet(
            f"background-color: {badge.name()}; color: {character_badge_text_color(badge)};"
            "border-radius: 8px; padding: 2px 12px; font-weight: bold;"
        )
        text = f"<div style='color:#243046;'>{html.escape(self.message.text or '')}</div>"
        if self.message.translation:
            text += f"<div style='color:#6B7C96;'>{html.escape(self.message.translation)}</div>"
        self.dialogue_label.setText(text)

    def discard_text_edit(self):
        """丢弃未保存的文本输入，恢复已保存内容，不改动时间轴或音频。"""
        if self.audio_busy:
            return
        self.original_input.setPlainText(self.message.text or "")
        self.translation_input.setPlainText(self.message.translation or "")
        self.show_saved_text()

    def toggle_text_edit(self):
        """将编辑按钮移入输入框下方作为保存入口，退出编辑时移回操作行。"""
        if self.audio_busy:
            return
        if self.text_editing:
            self.save_text()
            return
        self.prepare_edit()
        self.text_editing = True
        self.original_input.setPlainText(self.message.text or "")
        self.translation_input.setPlainText(self.message.translation or "")
        self.dialogue_label.hide()
        self.original_input.show()
        self.translation_input.show()
        self.text_actions.removeWidget(self.text_button)
        self.text_edit_layout.insertWidget(1, self.text_button)
        self.text_button.setText("保存文本")
        self.text_button.setIcon(multi_char_icon("save_white"))
        self.text_button.setObjectName("multiCharSave")
        self.text_button.style().unpolish(self.text_button)
        self.text_button.style().polish(self.text_button)
        self.discard_text_button.show()
        self.text_button.show()
        self.text_edit_actions.show()
        self.original_input.setFocus()

    def save_text(self, ask_audio=True):
        """独立保存文本；无声句按新文字长度校验已保存编排和当前草稿。"""
        if self.audio_busy:
            return False
        old_text = self.message.text
        old_translation = self.message.translation
        old_timeline = self.message.performance_timeline
        new_text = self.original_input.toPlainText()
        self.message.text = new_text
        self.message.translation = self.translation_input.toPlainText()
        span = audio_span(self.message.audio_path, new_text)
        if not self.message.audio_path:
            self.message.performance_timeline, _ = trim_timeline(old_timeline, span.duration_ms)
        if not self.owner._save_chat():
            self.message.text = old_text
            self.message.translation = old_translation
            self.message.performance_timeline = old_timeline
            return False
        self.show_saved_text()
        self.refresh_sentence_span()
        self.owner.refresh_chat_display()
        self.owner.refresh_chat_list()
        if ask_audio and new_text != old_text and self.message.character_name:
            answer = QMessageBox.question(self.resource_window, "重新生成音频",
                                          "检测到原文修改，是否重新生成音频？",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self.regenerate_audio()
        return True

    def resolve_text_draft(self, ask_audio=True):
        """切句、退出或合成前处理文本草稿，取消时保留编辑状态。"""
        if not self.text_editing:
            return True
        changed = (self.original_input.toPlainText() != (self.message.text or "")
                   or self.translation_input.toPlainText() != (self.message.translation or ""))
        if changed:
            answer = QMessageBox.question(self.resource_window, "文本尚未保存", "是否保存本次文本修改？",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Save:
                return self.save_text(ask_audio=ask_audio) and not self.audio_busy
            if answer != QMessageBox.Discard:
                return False
        self.show_saved_text()
        return True

    def refresh_sentence_span(self):
        """新文本或音频生效后更新时长波形，独立保留尚未保存的时间轴草稿。"""
        self.stop()
        self.span = audio_span(self.message.audio_path, self.message.text)
        self.baseline = deepcopy(self.message.performance_timeline)
        if not self.span.missing:
            self.draft, _ = trim_timeline(self.draft, self.span.duration_ms)
        self.select_event(None)
        self.view.selection = None
        self.time_input.setRange(0, max(0, (self.span.duration_ms - 1) / 1000))
        self.read_waveform()
        self.view.rebuild()
        self.update_clock()

    def set_audio_busy(self, busy):
        """合成期间锁定切句、文本及时间轴保存退出，完成或失败后恢复。"""
        self.audio_busy = busy
        self.update_navigation()
        self.text_button.setEnabled(not busy)
        self.discard_text_button.setEnabled(not busy)
        self.regenerate_button.setEnabled(not busy and bool(self.message.character_name))
        self.original_input.setEnabled(not busy)
        self.translation_input.setEnabled(not busy)
        self.timeline_window.setEnabled(not busy)
        self.resource_window.setEnabled(not busy)

    def regenerate_audio(self):
        """保存或放弃文本草稿后，复用主控制台合成并立即持久更新音频。"""
        if self.audio_busy or not self.message.character_name or not self.resolve_text_draft(ask_audio=False):
            return
        self.stop()
        self.set_audio_busy(True)
        self.status.setText("正在生成音频…")
        started = self.owner.regenerate_audio(self.message_index(), on_complete=self.audio_regenerated)
        if not started:
            self.set_audio_busy(False)
            self.status.setText("未开始音频合成，原音频已保留。")

    def audio_regenerated(self, success, reason):
        """成功时重载时长与裁剪后基线，失败仅解锁而不改音频和编排。"""
        self.set_audio_busy(False)
        if success:
            self.refresh_sentence_span()
        self.status.setText(reason)

    def build_timeline(self):
        """将波形、四轨及一体式胶囊控制栏放入无标题栏窗口。"""
        root = QVBoxLayout(self.timeline_window)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        self.view = MultiCharTimeline(self)
        self.view.setObjectName("multiCharTimeline")
        self.view.editing.connect(self.prepare_edit)
        self.view.dragging.connect(self.drag_hint.setVisible)
        self.view.changed.connect(self.changed)
        self.view.selected.connect(self.select_event)
        self.view.seeking.connect(self.seek)
        self.view.trackSelected.connect(self.select_track)
        root.addWidget(self.view, 1)
        self.status = QLabel("")
        self.status.setObjectName("multiCharStatus")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.view.notice.connect(self.status.setText)

        font_px = self.timeline_window.font().pixelSize()
        toolbar = QFrame()
        toolbar.setObjectName("multiCharToolbarCard")
        bar = QHBoxLayout(toolbar)
        pad_x = round(font_px * 0.45)
        pad_y = round(font_px * 0.24)
        bar.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        bar.setSpacing(round(font_px * 0.4))

        # 左区：一体式播放与时间码胶囊组
        transport = QFrame()
        transport.setObjectName("multiCharTransportGroup")
        t_lay = QHBoxLayout(transport)
        t_lay.setContentsMargins(4, 2, 6, 2)
        t_lay.setSpacing(4)
        self.play = QPushButton()
        icon_size = round(font_px * 1.05)
        self.play.setIconSize(QSize(icon_size, icon_size))
        self.play.setToolTip("播放/暂停")
        self.play.clicked.connect(self.toggle_play)
        t_lay.addWidget(self.play)
        stop = QPushButton()
        stop.setIconSize(QSize(icon_size, icon_size))
        stop.setIcon(self._transport_icon("stop"))
        stop.setToolTip("停止并回句首")
        stop.clicked.connect(self.stop)
        t_lay.addWidget(stop)
        self.clock = QLabel()
        self.clock.setObjectName("multiCharClock")
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setMinimumWidth(round(font_px * 6.8))
        t_lay.addWidget(self.clock)
        bar.addWidget(transport)

        # 中左区：一体式开始时间胶囊组
        time_group = QFrame()
        time_group.setObjectName("multiCharPillGroup")
        tg_lay = QHBoxLayout(time_group)
        tg_lay.setContentsMargins(8, 2, 4, 2)
        tg_lay.setSpacing(5)
        start_tag = QLabel("开始")
        start_tag.setObjectName("multiCharFieldTag")
        tg_lay.addWidget(start_tag)
        self.time_input = QDoubleSpinBox()
        self.time_input.setObjectName("multiCharStartTime")
        self.time_input.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.time_input.setAlignment(Qt.AlignCenter)
        self.time_input.setDecimals(3)
        self.time_input.setSuffix(" s")
        self.time_input.setMinimumWidth(round(font_px * 4.8))
        self.time_input.setRange(0, max(0, (self.span.duration_ms - 1) / 1000))
        self.time_input.setKeyboardTracking(False)
        self.time_input.setEnabled(False)
        self.time_input.valueChanged.connect(self.change_time)
        tg_lay.addWidget(self.time_input)
        bar.addWidget(time_group)

        # 中右区：一体式缩放滑块胶囊组
        zoom_group = QFrame()
        zoom_group.setObjectName("multiCharPillGroup")
        zg_lay = QHBoxLayout(zoom_group)
        zg_lay.setContentsMargins(8, 2, 8, 2)
        zg_lay.setSpacing(6)
        zoom_tag = QLabel("缩放")
        zoom_tag.setObjectName("multiCharFieldTag")
        zg_lay.addWidget(zoom_tag)
        zoom = QSlider(Qt.Horizontal)
        zoom.setObjectName("multiCharZoom")
        zoom.setFixedWidth(round(font_px * 5.8))
        zoom.setRange(10, 100)
        zoom.setValue(10)
        zoom.setToolTip("时间轴缩放（也可 Ctrl + 滚轮）")
        zoom.valueChanged.connect(self.zoom)
        zg_lay.addWidget(zoom)
        bar.addWidget(zoom_group)

        # 复用底栏中央空白，拖动时替换状态提示，不挤动两端的播放与保存控件。
        feedback = QWidget()
        feedback_stack = QStackedLayout(feedback)
        feedback_stack.setContentsMargins(0, 0, 0, 0)
        feedback_stack.addWidget(self.status)
        trash_page = QWidget()
        trash_layout = QHBoxLayout(trash_page)
        trash_layout.setContentsMargins(0, 0, 0, 0)
        trash = QToolButton()
        trash.setObjectName("multiCharTrash")
        trash.setText("拖动至此删除")
        trash.setToolButtonStyle(Qt.ToolButtonTextOnly)
        trash.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        trash.setFocusPolicy(Qt.NoFocus)
        # 此控件只是投放提示，鼠标释放继续由时间轴处理，点击本身不执行删除。
        trash.setAttribute(Qt.WA_TransparentForMouseEvents)
        trash_layout.addWidget(trash)
        feedback_stack.addWidget(trash_page)
        self.view.trash_target = trash
        self.view.dragging.connect(lambda active: feedback_stack.setCurrentIndex(int(active)))
        bar.addWidget(feedback, 1)
        discard = QPushButton("放弃本次时间轴修改")
        set_button_icon(discard, "discard", self.timeline_window)
        discard.setProperty("discardAction", True)
        self.discard_button = discard
        discard.clicked.connect(self.discard)
        bar.addWidget(discard)
        save = QPushButton("保存")
        set_button_icon(save, "save_white", self.timeline_window)
        self.save_button = save
        save.clicked.connect(self.save)
        bar.addWidget(save)
        root.addWidget(toolbar)
        self.update_clock()
        desired_view_height = self.view.top + len(self.view.rows) * self.view.row_height + self.view.group_gap + 16
        controls_height = root.minimumSize().height() - self.view.minimumHeight()
        self.timeline_window.setProperty("preferredHeight", desired_view_height + controls_height)

    def send(self, action, **payload):
        """发送同一编辑会话的命令，避免旧窗口消息干扰新会话。"""
        if action in ("enter", "play", "seek", "stop", "scene", "transition", "resource"):
            payload["scene_context"] = {
                "messages": [message.as_dict() for message in self.chat.message_list],
                "index": self.message_index(),
            }
        if action in ("enter", "scene", "transition"):
            payload["turn"] = self.turn()
        self.sequence += 1
        self.owner.to_live2d_change_character_queue.put(
            dict(payload, type="multi_char_preview", action=action, session=self.session_id, sequence=self.sequence)
        )

    def turn(self):
        """建立预览传输副本，不修改消息也不加入正式播放列表。"""
        turn = self.message.as_dict()
        turn["performance_timeline"] = deepcopy(self.draft)
        return turn

    def receive(self, result):
        """接收渲染位置、窗口句柄和表情参数，不写入聊天消息。"""
        if self.closed:
            return
        if result.get("type") == "expression_editor":
            self.expressionResult.emit(result)
            return
        if result.get("session") != self.session_id:
            return
        if result.get("notice"):
            self.status.setText(result["notice"])
            return
        if result.get("error"):
            self.status.setText(result["error"])
            self.playing = False
            self.can_resume = False
            self.transition_playing = False
            self.update_clock()
            return
        if result.get("type") == "multi_char_window":
            self.layout.bind(result.get("handle"), result.get("geometry"))
        elif result.get("type") == "multi_char_transition":
            if result.get("sequence", self.sequence) == self.sequence:
                self.transition_playing = bool(result.get("playing"))
        elif self.seek_resume is None and result.get("sequence", self.sequence) == self.sequence:
            self.position = min(self.span.duration_ms, max(0, int(result.get("position", 0))))
            self.playing = bool(result.get("playing"))
            if self.position >= self.span.duration_ms:
                self.can_resume = False
            self.update_clock()

    def refresh_resources(self, *_):
        """根据目标角色和标签显示资源，不禁用其他角色已有轨道。"""
        name = self.names[self.characters.currentIndex()]
        resources = self.resources[name]
        self.types.setTabEnabled(1, resources.version == "v3")
        kind = "motions" if self.types.currentIndex() == 0 else "expressions"
        self.custom.setVisible(resources.version == "v3" and kind == "expressions")
        self.custom.setEnabled(self.stage_available and resources.version == "v3" and kind == "expressions")
        term = self.search.text().strip().casefold()
        self.list.clear()
        items = list(resources.items[kind])
        if kind == "expressions" and resources.version == "v3":
            items.insert(0, {"name": "恢复默认", "resource": None, "file": "", "description": "重置回默认表情"})
        for item in items:
            searchable = " ".join(str(item.get(key, "")) for key in ("name", "file", "description"))
            if term and term not in searchable.casefold():
                continue
            row = QListWidgetItem(item["name"])
            row.setToolTip((item["description"] + "\n" + item["file"]).strip())
            row.setData(Qt.UserRole, {"character": name, "kind": kind, "resource": item["resource"]})
            if kind == "expressions" and item["resource"] is None:
                row.setForeground(QColor("#4A6FD8"))
            self.list.addItem(row)
        self.list.update_columns()
        if hasattr(self, "view"):
            self.view.rebuild()

    def reload_resources(self):
        """仅重读当前角色资源，保留预览、游标、筛选和可恢复的列表选中项。"""
        name = self.names[self.characters.currentIndex()]
        old = self.resources[name]
        current = self.list.currentItem()
        selected = current.data(Qt.UserRole) if current else None
        scroll = self.list.verticalScrollBar().value()
        self.resources[name] = TheaterResources(str(old.path) if old.path else "", old.direction)
        self.send("refresh", character=name)
        self.refresh_resources()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.UserRole) == selected:
                self.list.setCurrentItem(item)
                break
        self.list.verticalScrollBar().setValue(scroll)
        self.show_validation()

    def preview_resource(self, payload):
        """清理整句预览，独占试播当前资源并保留游标。"""
        self.playing = False
        self.transition_playing = False
        self.can_resume = False
        self.send("resource", position=self.position, **payload)
        self.update_clock()

    def prepare_edit(self):
        """修改前暂停并保留游标，修改后使用定位规则重新开始。"""
        if self.transition_playing:
            self.send("scene", position=self.position)
            self.transition_playing = False
        elif self.playing:
            self.send("pause")
        self.playing = False
        self.can_resume = False
        self.update_clock()

    def changed(self):
        """为新动作分配颜色，并刷新时间轴及未保存状态。"""
        assign_colors(self.draft, self.palette)
        self.status.setText("未保存修改")
        self.view.rebuild()

    def show_validation(self):
        """提示越界、失效、不在场角色及损坏事件，校验不会修写原数据。"""
        unavailable = 0
        beyond = 0
        malformed = 0
        for name, tracks in timeline_tracks(self.draft).items():
            for kind in ("motions", "expressions"):
                events = tracks.get(kind, [])
                valid = valid_timeline_events(events, kind)
                malformed += len(events) - len(valid) if isinstance(events, list) else 1
                for event in valid:
                    beyond += event["at_ms"] >= self.span.duration_ms
                    resource = event["resource"]
                    if name not in self.resources:
                        unavailable += 1
                    elif resource is not None and self.resources[name].resolve(kind, resource) is None:
                        unavailable += 1
        if unavailable or beyond or malformed:
            self.status.setText(f"已保留：{unavailable} 项资源不可用，{beyond} 项起点越界，{malformed} 项格式异常。")

    def select_event(self, selection):
        """选中事件时更新精确开始时间，不触发资源试播。"""
        self.selection = selection
        self.time_input.setEnabled(selection is not None)
        self.time_input.blockSignals(True)
        if selection:
            self.time_input.setValue(selection[1]["at_ms"] / 1000)
        self.time_input.blockSignals(False)

    def change_time(self, seconds):
        """精确修改起点，不对数字输入施加隐式吸附。"""
        if self.selection is None:
            return
        row, event = self.selection
        at = round(seconds * 1000)
        if any(other is not event and other["at_ms"] == at for other in self.view.events(row)):
            self.status.setText("同轨已有相同开始时间。")
            self.select_event(self.selection)
            return
        self.prepare_edit()
        event["at_ms"] = at
        name, kind = self.view.rows[row]
        sort_timeline_events(self.draft["characters"][name][kind])
        self.changed()

    def select_track(self, name, kind):
        """点击轨道标题切换资源列表，四轨编辑能力保持独立。"""
        self.characters.setCurrentIndex(self.names.index(name))
        self.types.setCurrentIndex(0 if kind == "motions" else 1)

    def zoom(self, value):
        """改变横向缩放但不改变事件时间及颜色。"""
        self.view.zoom = value / 10
        self.view.rebuild()

    def seek(self, position, released):
        """拖动游标时只重置预览，松开按先前播放状态决定是否继续。"""
        if self.seek_resume is None:
            self.seek_resume = self.playing
            self.send("stop")
        self.position = position
        self.playing = False
        self.can_resume = False
        self.update_clock()
        if released:
            resume = self.seek_resume
            self.seek_resume = None
            self.send("seek", turn=self.turn(), position=position, resume=resume)
            self.playing = resume

    def toggle_play(self):
        """普通暂停恢复保留模型进度，编辑/定位后的播放重建事件游标。"""
        self.transition_playing = False
        if self.playing:
            self.send("pause")
            self.playing = False
            self.can_resume = True
        elif self.can_resume:
            self.send("resume")
            self.playing = True
            self.can_resume = False
        else:
            if self.position >= self.span.duration_ms:
                self.position = 0
            self.send("play", turn=self.turn(), position=self.position)
            self.playing = True
        self.update_clock()

    def stop(self):
        """停止双人预览并回句首，不改变草稿。"""
        self.send("stop")
        self.position = 0
        self.playing = False
        self.transition_playing = False
        self.can_resume = False
        self.update_clock()

    def update_clock(self):
        """更新游标与播放按钮，条块不显示持续秒数。"""
        self.clock.setText(f"{self.position / 1000:.2f} / {self.span.duration_ms / 1000:.2f}")
        self.play.setIcon(self._transport_icon("pause" if self.playing else "play"))
        self.view.playhead = self.position
        self.view.viewport().update()

    def read_waveform(self):
        """只读取音频峰值用于显示，无声保留平直波形。"""
        self.view.waveform = []
        if not self.span.path:
            return
        import numpy as np
        import soundfile as sf
        try:
            samples, _ = sf.read(self.span.path, always_2d=True, dtype="float32")
            mono = np.max(np.abs(samples), axis=1)
            self.view.waveform = [float(np.max(chunk)) for chunk in np.array_split(mono, min(800, len(mono))) if len(chunk)]
            self.view.rebuild()
        except (OSError, RuntimeError):
            self.status.setText("波形读取失败；原音频和编排保留。")

    def custom_expression(self):
        """复用现有表情编辑器，实时参数发送给同一舞台的目标角色。"""
        from ui.components.live2d_expression_editor import CustomExpressionDialog
        from ui.components.live2d_performance_editor import confirm_description_changes
        self.stop()
        name = self.names[self.characters.currentIndex()]
        resources = self.resources[name]
        current = self.list.currentItem()
        resource = current.data(Qt.UserRole)["resource"] if current else None
        selected = resource.get("logical_id", "") if resource else ""

        def send(command):
            """给现有表情协议补充目标角色，不新建预览窗口。"""
            self.owner.to_live2d_change_character_queue.put(dict(command, character=name))

        dialog = CustomExpressionDialog(resources.path, selected, send, confirm_description_changes, self.resource_window)
        self.expression_dialog = dialog
        self.expressionResult.connect(dialog.receive)
        try:
            dialog.exec_()
        finally:
            self.expressionResult.disconnect(dialog.receive)
            self.expression_dialog = None
            self.reload_resources()
            dialog.deleteLater()

    def stage_closed(self):
        """舞台意外关闭后停止预览状态，保留草稿和保存入口。"""
        self.playing = False
        self.stage_available = False
        self.can_resume = False
        self.play.setEnabled(False)
        self.custom.setEnabled(False)
        self.transition_preview.setEnabled(False)
        self.status.setText("Live2D 前台已关闭；编排仍可保存或放弃。")
        self.update_clock()

    def save_current(self):
        """按实际时长校验后保存原消息，写盘失败回滚消息但保留草稿。"""
        if self.audio_busy:
            return False
        if not any(message is self.message for message in self.chat.message_list):
            QMessageBox.warning(self.resource_window, "无法保存", "原消息已删除。")
            return False
        self.span = audio_span(self.message.audio_path, self.message.text)
        candidate = deepcopy(self.draft)
        removed = 0
        if not self.span.missing:
            candidate, removed = trim_timeline(candidate, self.span.duration_ms)
        old = self.message.performance_timeline
        self.message.performance_timeline = candidate
        if not self.owner._save_chat():
            self.message.performance_timeline = old
            return False
        if removed:
            QMessageBox.information(self.resource_window, "时长校验", f"已移除 {removed} 个起点不早于句末的事件。")
        self.baseline = deepcopy(candidate)
        self.draft = deepcopy(candidate)
        return True

    def save(self):
        """保存当前消息并关闭整个编排会话。"""
        if self.audio_busy or not self.resolve_text_draft():
            return
        if self.save_current():
            self.finish()

    def discard(self):
        """放弃草稿，保留进入时已持久保存的时间轴。"""
        if self.audio_busy or not self.resolve_text_draft():
            return
        self.finish()

    def request_close(self):
        """两个窗口共用一次未保存确认，取消时保留完整编辑会话。"""
        if self.closing or self.audio_busy or not self.resolve_text_draft():
            return
        self.closing = True
        self.send("pause")
        if self.draft != self.baseline:
            answer = QMessageBox.question(self.resource_window, "未保存修改", "是否保存本次编排？",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Save:
                self.save()
            elif answer == QMessageBox.Discard:
                self.finish()
        else:
            self.finish()
        self.closing = False

    def finish(self):
        """结束预览、关闭两个新窗口并恢复原双窗口布局，不改变 BGM。"""
        if self.closed or self.audio_busy:
            return
        self.closed = True
        self.send("exit")
        self.layout.restore()
        self.resource_window.close()
        self.timeline_window.close()
        self.owner.setEnabled(True)
        self.owner.raise_()
        self.owner.performance_editor = None
        self.resource_window.deleteLater()
        self.timeline_window.deleteLater()
        self.deleteLater()
