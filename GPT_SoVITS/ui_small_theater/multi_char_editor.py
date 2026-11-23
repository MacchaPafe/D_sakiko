"""小剧场独占编排会话：两个非模态窗口共用草稿和现有 Live2D 前台。"""
from __future__ import annotations

import html
import json
import uuid
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, QMimeData, QPoint, QRect, QTimer, QSize, QEvent, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QIcon, QImageReader, QPalette
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedLayout, QFrame, QLabel, QTabBar,
                             QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QListView, QPushButton,
                             QMessageBox, QDoubleSpinBox, QSlider, QApplication, QStyle, QSizePolicy,
                             QCheckBox, QFileDialog, QMenu, QToolButton, QToolTip, QDialog, QButtonGroup, QWidgetAction,
                             QStyleOptionToolButton, QStylePainter)

from chat.chat import Message
from emotion_enum import EMOTION_LABELS_ZH, EmotionEnum
from performance_types import trim_timeline, sort_timeline_events, timeline_tracks, valid_timeline_events
from live2d_support.multi_char_resources import TheaterResources, audio_span, assign_colors, initial_message_timeline
from live2d_support.multi_char_scene import (
    SceneTiming, arrange_cast, background_root, background_path, cast_run_end, resolve_scene, transition_from_total,
)
from ui_small_theater.multi_char_insert_dialog import CastButtons, MultiCharInsertDialog, role_button
from ui_small_theater.multi_char_icons import (
    DELETE_COLOR, delete_message_icon, multi_char_icon, reference_audio_settings_icon,
    set_button_icon, speaker_switch_icon,
)
from ui_small_theater.multi_char_message_edit import (
    confirm_message_deletion, delete_saved_message, insert_saved_message, save_cast, save_emotion, save_speaker,
)
from ui_small_theater.multi_char_menu import fit_menu_to_contents
from ui_small_theater.multi_char_mode_bar import SceneModeBar
from ui_small_theater.multi_char_style import editor_font, editor_stylesheet
from ui_small_theater.multi_char_timeline import MultiCharTimeline, MIME, _character_pill_color
from ui_small_theater.multi_char_windows import MultiCharWindowLayout
from ui_qt.components.custom_bgm_dialog import BGM_DIRECTORY, CustomBGMDialog
from ui_constants import char_info_json


def character_badge_color(name):
    """让说话人标签与出场胶囊、轨道头使用同一角色配置原色。"""
    return _character_pill_color(name)


def character_badge_text_color(background):
    """根据背景亮度选择深色或白色文字，不为适配文字改动主题色。"""
    brightness = background.red() * 0.299 + background.green() * 0.587 + background.blue() * 0.114
    return "#334155" if brightness >= 145 else "#FFFFFF"


class SpeakerPill(QToolButton):
    """用一个按钮绘制角色名与替换区，左右两部分共用同一点击行为。"""

    def replacement_width(self):
        return self.fontMetrics().height() + 9

    def sizeHint(self):
        metrics = self.fontMetrics()
        right = self.replacement_width()
        left = max(3 * right, metrics.horizontalAdvance(self.text()) + 20)
        return QSize(left + right, metrics.height() + 8)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        """复用按钮的背景和状态，仅重绘两区内容与分隔线。"""
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QIcon()
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ToolButton, option)

        right = self.replacement_width()
        divider_x = self.width() - right
        height = self.height()
        painter.setPen(option.palette.color(QPalette.ButtonText))
        painter.drawText(QRect(0, 0, divider_x, height), Qt.AlignCenter, self.text())
        line_height = height
        painter.fillRect(QRect(divider_x, (height - line_height) // 2, 1, line_height), Qt.white)
        icon_size = round(self.fontMetrics().height() * 0.78)
        icon_rect = QRect(divider_x + (right - icon_size) // 2, (height - icon_size) // 2, icon_size, icon_size)
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        self.icon().paint(painter, icon_rect, Qt.AlignCenter, mode)


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
        font = editor_font(session.owner)
        self.setFont(font)
        # 使用逻辑像素，由 Qt 负责 DPI 缩放，避免继承全局大号点字体。
        style = editor_stylesheet(font.pixelSize(), session.names)
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

    def __init__(self, owner, chat, message, slots, facing="screen", *, roster=None):
        """建立共享会话并持久初始化首次时间轴，失败不进入编辑。"""
        super().__init__(owner)
        self.owner = owner
        self.chat = chat
        self.message = message
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self.roster = roster
        self.cast_message = None
        self.saved_cast = None
        self.swap_pending = False
        self.preview_opening_slots = None
        if roster is not None:
            payload = roster.payload(self.current_cast())
            slots, facing = payload["slots"], payload["motion_facing_mode"]
        self.names = [slot["character_name"] for slot in slots if slot["character_name"]]
        self.slots = slots
        self.resources = self.slot_resources(slots, facing)
        self.palette = [item["theme_color"] for item in char_info_json.values() if item.get("theme_color")]
        self.span = audio_span(message.audio_path, message.text)
        self.initialize_message(message)
        self.baseline = self.timeline_view(message)
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
        self.update_timeline_preferred_height()
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
        QApplication.instance().installEventFilter(self)

    def current_cast(self):
        return self.roster.casts()[self.message_index()]

    def slot_resources(self, slots, facing):
        """按当前演员及左右方向复用编排资源，换位时刷新方向索引。"""
        result = {}
        for index, slot in enumerate(slots):
            name = slot["character_name"]
            if not name:
                continue
            path = slot.get("model_json_path")
            direction = ("L" if index == 0 else "R") if facing == "face_to_face" else "C"
            old = getattr(self, "resources", {}).get(name)
            if old is not None and str(old.path or "") == str(path or "") and old.direction == direction:
                result[name] = old
            else:
                result[name] = TheaterResources(path, direction)
        return result

    def timeline_view(self, message):
        if self.roster is not None:
            return self.roster.timeline(message.performance_timeline)
        return deepcopy(message.performance_timeline)

    def refresh_cast_resources(self):
        """显示当前阵容的轨道和素材，已退场角色的草稿继续保留。"""
        if self.roster is None:
            return
        payload = self.roster.payload(self.current_cast())
        current = self.characters.tabText(self.characters.currentIndex())
        self.slots = payload["slots"]
        self.resources = self.slot_resources(self.slots, payload["motion_facing_mode"])
        self.names = list(self.resources)
        self.characters.blockSignals(True)
        while self.characters.count():
            self.characters.removeTab(0)
        for name in self.names:
            self.characters.addTab(name)
            self.draft.setdefault("characters", {}).setdefault(name, {"motions": [], "expressions": []})
            self.baseline.setdefault("characters", {}).setdefault(name, {"motions": [], "expressions": []})
        if current in self.names:
            self.characters.setCurrentIndex(self.names.index(current))
        self.characters.blockSignals(False)
        self.characters.setVisible(bool(self.names))
        self.select_event(None)
        self.view.selection = None
        self.view.drag = None
        self.view.press = None
        self.view.ghost = None
        self.view.snap_x = None
        self.view.rows = [(name, kind) for name in self.names for kind in ("motions", "expressions")]
        self.refresh_resources()
        self.update_timeline_preferred_height()

    def resolve_cast_draft(self):
        """切句或关闭之前处理尚未提交的出场选择。"""
        if self.roster is None or not self.cast_save.isEnabled():
            return True
        choice = QMessageBox.question(self.resource_window, "出场角色尚未保存", "是否保存本次出场选择？",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if choice == QMessageBox.Save:
            return self.commit_cast()
        if choice == QMessageBox.Discard:
            self.cast_buttons.set_selection(self.current_cast())
            self.update_cast_controls()
            return True
        return False

    def eventFilter(self, watched, event):
        """编排窗口共用空格播放，输入文字或操作模态弹窗时保留原行为。"""
        if (event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space
                and event.modifiers() == Qt.NoModifier
                and QApplication.activeWindow() in (self.resource_window, self.timeline_window)
                and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
                and not isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit))):
            if not event.isAutoRepeat():
                self.toggle_play()
            return True
        return super().eventFilter(watched, event)

    def initialize_message(self, message):
        """校验目标消息并持久初始化首次编排，失败时保留原消息。"""
        timeline = self.prepare_message_timeline(message)
        if message.performance_timeline is None:
            message.performance_timeline = timeline
            try:
                if not self.owner._save_chat():
                    raise RuntimeError("时间轴初始化未能保存")
            except Exception:
                message.performance_timeline = None
                raise

    def prepare_message_timeline(self, message):
        """只生成或校验目标时间轴，不修改消息和存档，供删除事务预先检查。"""
        timeline = message.performance_timeline
        if timeline is None:
            timeline = self.initial_timeline(message)
        if not isinstance(timeline, dict):
            raise ValueError("时间轴格式损坏；已保留原数据，请先修复存档。")
        groups = timeline.get("characters", {})
        if not isinstance(groups, dict):
            raise ValueError("角色轨道格式损坏；已保留原数据。")
        for name in self.names:
            tracks = groups.get(name, {})
            if not isinstance(tracks, dict) or any(
                not isinstance(tracks.get(kind, []), list) for kind in ("motions", "expressions")
            ):
                raise ValueError(f"{name} 的轨道格式损坏；已保留原数据。")
        return timeline

    def initial_timeline(self, message, cast=None):
        """沿用原自动演出生成首次双人轨道，无角色消息从空轨道开始。"""
        if self.roster is not None:
            if cast is None:
                index = next(index for index, item in enumerate(self.chat.message_list) if item is message)
                cast = self.roster.casts()[index]
            payload = self.roster.payload(cast)
            resources = self.slot_resources(payload["slots"], payload["motion_facing_mode"])
            if message.speaker_id in self.roster.characters:
                message = Message.from_dict(dict(message.as_dict(),
                                                 character_name=self.roster.label(message.speaker_id)))
            timeline = initial_message_timeline(message, resources, resources, self.palette)
            return self.roster.timeline(timeline, for_storage=True)
        return initial_message_timeline(message, self.names, self.resources, self.palette)

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
        self.delete_message_button.setEnabled(not self.audio_busy and index >= 0)
        self.message_count.setText(f"{index + 1 if index >= 0 else 0}/{len(self.chat.message_list)}")

    def resolve_message_drafts(self):
        """切句或插句前处理文本与时间轴草稿，取消时留在原句。"""
        if self.audio_busy or not self.resolve_text_draft() or not self.resolve_cast_draft():
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

    def show_message(self, target, *, stop_preview=True):
        """在现有两个窗口中切换已确认的消息，保持同一舞台编辑会话。"""
        try:
            self.initialize_message(target)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self.resource_window, "无法切换台词", str(error))
            return
        if stop_preview:
            self.send("stop")
        self.message = target
        self.span = audio_span(target.audio_path, target.text)
        self.baseline = self.timeline_view(target)
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
        self.refresh_cast_resources()
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
        above = menu.addAction(multi_char_icon("previous"), "本句之前")
        below = menu.addAction(multi_char_icon("next"), "本句之后")
        fit_menu_to_contents(menu)
        anchor = self.add_message_button.mapToGlobal(QPoint(0, self.add_message_button.height()))
        chosen = menu.exec_(anchor)
        if chosen is above or chosen is below:
            self.insert_message(after=chosen is below)

    def insert_message(self, after):
        """确认草稿和表单后插入普通消息，保存成功才切到新台词。"""
        if self.audio_busy or self.closed or self.message_index() < 0:
            return
        anchor = self.message
        if not self.resolve_message_drafts():
            return
        if self.audio_busy or self.closed or self.message is not anchor or self.message_index() < 0:
            return
        if self.roster is not None:
            index = self.message_index() + int(after)
            previous = self.roster.casts()[index - 1] if index else self.roster.opening
            dialog = MultiCharInsertDialog(self.names, anchor.speaker_id or anchor.character_name,
                                           self.resource_window, roster=self.roster, previous=previous,
                                           save=self.owner._save_chat)
        else:
            dialog = MultiCharInsertDialog(self.names, anchor.character_name, self.resource_window)
        try:
            if dialog.exec_() != QDialog.Accepted:
                return
            data = dialog.message_data()
            cast = dialog.selected_cast() if self.roster is not None else None
        finally:
            dialog.deleteLater()
        if (self.audio_busy or self.closed or self.message is not anchor or self.message_index() < 0
                or not data["text"].strip()):
            return
        try:
            target = Message.from_dict(data)
            # 新句沿用首次编排的初始化规则，场景保持缺省，从前句自然继承。
            target.performance_timeline = self.initial_timeline(target, cast)
            inserted = insert_saved_message(self.chat, anchor, target, after, self.owner._save_chat,
                                            roster=self.roster, cast=cast)
        except Exception as error:
            QMessageBox.warning(self.resource_window, "无法添加台词", str(error))
            return
        if not inserted:
            return
        self.show_message(target)
        # 主界面刷新可能处理 Qt 事件；先切到已经保存的新句，避免重入旧句草稿。
        self.owner.refresh_chat_display()
        self.owner.refresh_chat_list()

    def delete_message(self):
        """删除当前句及其草稿；保存成功后切到邻句，空剧本则结束编排。"""
        index = self.message_index()
        if self.audio_busy or self.closed or index < 0:
            return
        message = self.message
        if not confirm_message_deletion(self.resource_window, message, discard_drafts=True):
            return
        if self.audio_busy or self.closed or self.message is not message:
            return
        index = self.message_index()
        if index < 0:
            return
        messages = self.chat.message_list
        target = messages[index + 1] if index + 1 < len(messages) else (messages[index - 1] if index else None)
        old_timeline = target.performance_timeline if target is not None else None
        try:
            timeline = self.prepare_message_timeline(target) if target is not None else None
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self.resource_window, "无法删除本句", f"相邻台词无法打开：{error}")
            return
        # 在消息还在列表时停止旧预览，避免发送失效的句序号。
        self.stop()
        deleted = False
        failure = None
        try:
            if target is not None:
                target.performance_timeline = timeline
            deleted = delete_saved_message(self.chat, message, self.owner._save_chat, roster=self.roster)
        except Exception as error:
            failure = error
        finally:
            if not deleted and target is not None:
                target.performance_timeline = old_timeline
        if failure is not None:
            QMessageBox.warning(self.resource_window, "无法删除本句", str(failure))
        if not deleted:
            return
        owner = self.owner
        if target is None:
            self.finish()
        else:
            self.show_message(target, stop_preview=False)
        # 刷新主界面可能处理 Qt 事件，必须先脱离已删除对象再允许重入。
        owner.refresh_chat_display()
        owner.refresh_chat_list()

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
        if self.roster is not None:
            title = SpeakerPill()
            title.setText(self.message.character_name)
            title.setToolTip("点击切换本句说话人")
            title.setCursor(Qt.PointingHandCursor)
            title.clicked.connect(self.choose_speaker)
        else:
            title = QLabel(self.message.character_name)
        self.speaker_label = title
        title.setObjectName("multiCharSpeaker")
        title.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
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
        self.delete_message_button = QToolButton()
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
        self.delete_message_button.setObjectName("multiCharNavigation")
        self.delete_message_button.setText("删除本句")
        self.delete_message_button.setToolTip("删除本句及其编排和句前场景变化")
        self.delete_message_button.setAccessibleName("删除本句")
        self.delete_message_button.setCursor(Qt.PointingHandCursor)
        self.delete_message_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.delete_message_button.setIcon(delete_message_icon())
        self.delete_message_button.setIconSize(self.add_message_button.iconSize())
        self.delete_message_button.setStyleSheet(f"QToolButton:enabled {{ color: {DELETE_COLOR}; }}")
        self.delete_message_button.clicked.connect(self.delete_message)
        self.message_count = QLabel()
        self.message_count.setObjectName("multiCharMessageCount")
        self.message_count.setAlignment(Qt.AlignCenter)
        self.message_count.setAccessibleName("当前台词位置")
        header.addWidget(self.previous)
        header.addWidget(self.message_count)
        header.addWidget(self.next)
        header.addWidget(self.add_message_button)
        header.addWidget(self.delete_message_button)
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
        self.regenerate_pill = QFrame()
        self.regenerate_pill.setObjectName("multiCharRegeneratePill")
        self.regenerate_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        regenerate_layout = QHBoxLayout(self.regenerate_pill)
        regenerate_layout.setContentsMargins(1, 1, 1, 1)
        regenerate_layout.setSpacing(0)
        self.regenerate_button = QPushButton("重新生成音频")
        self.regenerate_button.setObjectName("multiCharRegenerateText")
        self.regenerate_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.regenerate_button.setCursor(Qt.PointingHandCursor)
        self.regenerate_button.setToolTip("重新生成本句音频")
        set_button_icon(self.regenerate_button, "audio", self.resource_window)
        self.regenerate_button.clicked.connect(self.regenerate_audio)
        regenerate_layout.addWidget(self.regenerate_button)
        divider = QFrame()
        divider.setObjectName("multiCharRegenerateDivider")
        divider.setFixedSize(1, round(self.resource_window.fontMetrics().height() * 0.68))
        regenerate_layout.addWidget(divider, 0, Qt.AlignVCenter)
        self.emotion_button = QPushButton()
        self.emotion_button.setObjectName("multiCharRegenerateEmotion")
        self.emotion_button.setCursor(Qt.PointingHandCursor)
        self.emotion_button.setLayoutDirection(Qt.RightToLeft)
        self.emotion_button.setIcon(multi_char_icon("chevron_down"))
        arrow_size = round(self.resource_window.fontMetrics().height() * 0.55)
        self.emotion_button.setIconSize(QSize(arrow_size, arrow_size))
        self.emotion_button.setToolTip("选择本句情绪，点击重新生成音频后更新语音")
        self.emotion_button.clicked.connect(self.choose_emotion)
        regenerate_layout.addWidget(self.emotion_button)
        self.emotion_divider = QFrame()
        self.emotion_divider.setObjectName("multiCharRegenerateDivider")
        self.emotion_divider.setFixedSize(divider.size())
        regenerate_layout.addWidget(self.emotion_divider, 0, Qt.AlignVCenter)
        self.reference_settings_button = QToolButton()
        self.reference_settings_button.setObjectName("multiCharRegenerateSettings")
        self.reference_settings_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.reference_settings_button.setCursor(Qt.PointingHandCursor)
        self.reference_settings_button.setAccessibleName("配置参考音频")
        self.reference_settings_button.setToolTip("配置本句说话人的参考音频")
        self.reference_settings_button.setIcon(reference_audio_settings_icon())
        icon_size = round(self.resource_window.fontMetrics().height() * 0.78)
        self.reference_settings_button.setIconSize(QSize(icon_size, icon_size))
        self.reference_settings_button.clicked.connect(self.configure_reference_audio)
        regenerate_layout.addWidget(self.reference_settings_button)
        self.update_audio_actions()
        self.text_actions.addWidget(self.regenerate_pill)
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
        self.settings_button = QPushButton("更多全局设置")
        self.settings_button.setObjectName("multiCharTextAction")
        self.settings_button.setToolTip("打开小剧场设置")
        self.settings_button.clicked.connect(self.open_settings)
        self.text_actions.addWidget(self.settings_button)
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
        self.characters.setVisible(bool(self.names))
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
        icon_path = Path(__file__).parents[1] / "icons" / "refresh.svg"
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
        """建立默认折叠的场景卡，共用标题栏的组合预览入口。"""
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
        self.transition_preview = QToolButton()
        self.transition_preview.setObjectName("multiCharTransitionPreview")
        self.transition_preview.setText("预览")
        self.transition_preview.setToolTip("预览整体场景效果")
        set_button_icon(self.transition_preview, "play", self.resource_window)
        self.transition_preview.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.transition_preview.clicked.connect(self.preview_transition)
        header.addWidget(self.transition_preview)
        root.addLayout(header)
        self.scene_body = QWidget()
        self.scene_body.setObjectName("multiCharSceneBody")
        grid = QGridLayout(self.scene_body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(pad * 2)
        # 分组之间的行距大于组内行距，让背景 / 音乐 / 转场三组边界清晰
        grid.setVerticalSpacing(round(pad * 1.7))
        grid.setColumnStretch(1, 1)
        metrics = self.resource_window.fontMetrics()
        font_px = self.resource_window.font().pixelSize()
        icon_size = round(metrics.height() * 0.85)
        # 标题顶部留白 = 分段条的内边距，使标题与右侧首行控件的中线对齐
        label_top = round(font_px * 0.25) + 4
        # 所有子面板共用同一组列宽，保证“标签 | 滑条 | 数值”上下严格对齐
        tag_width = metrics.horizontalAdvance("转场文本") + 6
        value_width = metrics.horizontalAdvance("00.0 秒") + 4

        def transparent_row():
            """普通容器会继承主程序全局 QWidget 底色，统一用透明对象名消除灰色底块。"""
            widget = QWidget()
            widget.setObjectName("multiCharSceneRow")
            return widget

        def slider_row(tag_text, slider, value_label):
            """统一的“标签 | 滑条 | 数值”三列行，滑条始终铺满中间列。"""
            row_widget = transparent_row()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(pad)
            tag = QLabel(tag_text)
            tag.setObjectName("multiCharSubpanelLabel")
            tag.setMinimumWidth(tag_width)
            row_layout.addWidget(tag)
            row_layout.addWidget(slider, 1)
            value_label.setMinimumWidth(value_width)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_layout.addWidget(value_label)
            return row_widget

        sub_pad = round(pad * 0.7)

        def make_subpanel():
            """二级参数子面板：浅底色 + 左侧导引线，勾选对应一级开关后才展开。"""
            frame = QFrame()
            frame.setObjectName("multiCharSceneSubpanel")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(sub_pad + 4, sub_pad, sub_pad, sub_pad)
            layout.setSpacing(round(pad * 0.7))
            return frame, layout

        row_offset = 1 if self.roster is not None else 0
        # 多角色模式将「出场角色」并入同一网格第 0 行，保证四行分类标签与右侧首列控件严格对齐
        categories = ((("出场角色", "actors"),) if self.roster is not None else ()) + (
            ("切换背景", "background"), ("切换BGM", "music"), ("句前转场", "transition"),
        )
        for row, (text, icon) in enumerate(categories):
            # 左侧分类标签顶对齐，右侧展开子面板或角色换行时标题仍锚定在首行控件旁
            label_widget = transparent_row()
            label_row = QHBoxLayout(label_widget)
            # 角色行与分段条行首行控件较高，转场行首行是复选框较矮，顶部留白分别取值
            is_transition_row = row == len(categories) - 1
            top_margin = round(font_px * 0.1) if is_transition_row else (
                round(font_px * 0.25) + 1 if self.roster is not None and row == 0 else label_top
            )
            label_row.setContentsMargins(0, top_margin, 0, 0)
            label_row.setSpacing(round(pad * 0.7))
            image = QLabel()
            image.setObjectName("multiCharSceneIcon")
            scene_icon = QIcon(str(Path(__file__).parents[1] / "icons" / "music.png")) if icon == "music" else multi_char_icon(icon)
            image.setPixmap(scene_icon.pixmap(icon_size, icon_size))
            label = QLabel(text)
            label.setObjectName("multiCharSceneLabel")
            label_row.addWidget(image)
            label_row.addWidget(label)
            label_row.addStretch(1)
            grid.addWidget(label_widget, row, 0, Qt.AlignTop)

        self.visible_checks = {}
        self.visibility_help = QToolButton()
        self.visibility_help.setObjectName("multiCharVisibilityHelp")
        self.visibility_help.setAccessibleName("出场角色沿用规则")
        self.visibility_help.setAutoRaise(True)
        help_icon = Path(__file__).parents[1] / "icons" / "multi_char_help.svg"
        self.visibility_help.setIcon(QIcon(str(help_icon)))
        help_size = round(font_px * 1.15)
        self.visibility_help.setIconSize(QSize(help_size, help_size))
        self.visibility_help.clicked.connect(self.show_visibility_help)
        if self.roster is not None:
            # 多角色出场区：流式角色胶囊 + 右侧互换/说明按钮，下方按需展开待保存确认条
            cast_column = QVBoxLayout()
            cast_column.setSpacing(round(pad * 0.75))
            cast_top_row = QHBoxLayout()
            cast_top_row.setSpacing(pad)
            self.cast_buttons = CastButtons(self.roster, self.current_cast(), self.owner._save_chat, self.scene_body)
            self.cast_buttons.changed.connect(self.update_cast_controls)
            self.cast_buttons.roster_changed.connect(self.refresh_speaker_label)
            cast_top_row.addWidget(self.cast_buttons, 1)
            self.cast_swap = QPushButton("互换")
            self.cast_swap.setObjectName("multiCharCastSwap")
            self.cast_swap.setAccessibleName("左右互换")
            self.cast_swap.setCursor(Qt.PointingHandCursor)
            set_button_icon(self.cast_swap, "swap", self.resource_window)
            self.cast_swap.clicked.connect(self.swap_cast)
            cast_top_row.addWidget(self.cast_swap, 0, Qt.AlignTop)
            self.visibility_help.setToolTip("点亮/熄灭角色后需点击「保存出场」生效；保存出场或左右互换会更新后续连续相同的旧阵容。")
            cast_top_row.addWidget(self.visibility_help, 0, Qt.AlignTop)
            cast_column.addLayout(cast_top_row)

            # 待保存确认条：复用场景子面板视觉样式，平时隐藏，仅在存在未提交的换人草稿时展开
            # 上行完整展示新站位与影响句数，下行右对齐放操作按钮，避免窄窗口下摘要折行裁切
            self.cast_confirm_bar, cast_confirm_layout = make_subpanel()
            self.cast_draft_summary = QLabel()
            self.cast_draft_summary.setObjectName("multiCharSubpanelLabel")
            self.cast_draft_summary.setWordWrap(True)
            self.cast_draft_summary.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            cast_confirm_layout.addWidget(self.cast_draft_summary)
            confirm_actions = QHBoxLayout()
            confirm_actions.setContentsMargins(0, 0, 0, 0)
            confirm_actions.setSpacing(pad)
            confirm_actions.addStretch(1)
            self.cast_cancel = QPushButton("取消")
            self.cast_cancel.setObjectName("multiCharControl")
            self.cast_cancel.setProperty("discardAction", True)
            self.cast_cancel.setCursor(Qt.PointingHandCursor)
            self.cast_cancel.clicked.connect(self.cancel_cast_draft)
            confirm_actions.addWidget(self.cast_cancel)
            self.cast_save = QPushButton("保存出场")
            self.cast_save.setObjectName("multiCharSave")
            self.cast_save.setCursor(Qt.PointingHandCursor)
            set_button_icon(self.cast_save, "save_white", self.resource_window)
            self.cast_save.clicked.connect(self.commit_cast)
            confirm_actions.addWidget(self.cast_save)
            cast_confirm_layout.addLayout(confirm_actions)
            self.cast_confirm_bar.hide()
            cast_column.addWidget(self.cast_confirm_bar)

            grid.addLayout(cast_column, 0, 1)
            # 多角色模式下出场角色已内嵌在 scene_body 中，不再占用标题行
            self.visible_group = None
        else:
            # 旧双人兼容模式：出场角色并入标题行（仅展开时显示，折叠时由摘要承担），省出一整行高度
            self.visible_group = transparent_row()
            visible_row = QHBoxLayout(self.visible_group)
            visible_row.setContentsMargins(pad, 0, 0, 0)
            visible_row.setSpacing(pad)
            actors_icon = QLabel()
            actors_icon.setObjectName("multiCharSceneIcon")
            actors_icon.setPixmap(multi_char_icon("actors").pixmap(icon_size, icon_size))
            visible_row.addWidget(actors_icon)
            actors_label = QLabel("出场角色")
            actors_label.setObjectName("multiCharSceneLabel")
            visible_row.addWidget(actors_label)
            for index, name in enumerate(self.names):
                check = QPushButton(name)
                check.setObjectName("multiCharVisiblePill")
                check.setProperty("characterSlot", str(index))
                check.setCheckable(True)
                # 仅键盘 Tab 可聚焦：鼠标点击切换不再残留蓝色焦点描边，避免误认为选中态
                check.setFocusPolicy(Qt.TabFocus)
                check.setCursor(Qt.PointingHandCursor)
                check.toggled.connect(self.change_visible_characters)
                self.visible_checks[name] = check
                visible_row.addWidget(check)
            self.visibility_help.setToolTip(
                "本句的出场角色设置会延续到后面的所有台词，直到后面的某句再次设置。\n"
                "但本句之前的台词不会受影响。\n下面的“切换背景”、“切换BGM”遵循相同的设定逻辑。"
            )
            visible_row.addWidget(self.visibility_help)
            visible_row.addStretch(1)
            header.insertWidget(2, self.visible_group, 1)
            self.visible_group.hide()

        background_row = QHBoxLayout()
        background_row.setSpacing(pad)
        self.background_mode = QButtonGroup(self)
        self.background_modes = SceneModeBar(self.background_mode)
        self.background_modes.setObjectName("multiCharBackgroundModes")
        background_modes_layout = self.background_modes.layout()
        self.background_buttons = {}
        # 分段开关仅用纯文字，避免与左侧分类图标和右侧文件按钮图标重复堆叠
        for enabled, text, tooltip in (
                (False, "沿用", "沿用上一句背景"),
                (True, "切换", "切换本句及后续背景图片")):
            button = QToolButton()
            button.setObjectName("multiCharBackgroundMode")
            button.setText(text)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tooltip)
            self.background_mode.addButton(button, int(enabled))
            self.background_buttons[enabled] = button
            background_modes_layout.addWidget(button)
        self.background_mode.buttonToggled.connect(self.change_background_enabled)
        background_row.addWidget(self.background_modes, 0, Qt.AlignLeft)
        self.background_file = QToolButton()
        set_button_icon(self.background_file, "background", self.resource_window)
        self.background_file.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.background_file.setObjectName("multiCharBackgroundFile")
        self.background_file.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.background_file.setToolTip("重新选择背景图片")
        self.background_file.clicked.connect(self.choose_background)
        background_row.addWidget(self.background_file, 1)
        grid.addLayout(background_row, row_offset, 1)

        music_column = QVBoxLayout()
        music_column.setSpacing(round(pad * 0.75))
        music_row = QHBoxLayout()
        music_row.setSpacing(pad)
        self.bgm_mode = QButtonGroup(self)
        self.bgm_modes = SceneModeBar(self.bgm_mode)
        self.bgm_modes.setObjectName("multiCharBgmModes")
        modes_layout = self.bgm_modes.layout()
        self.bgm_buttons = {}
        for text, action, tooltip in (
                ("沿用", None, "沿用上一句音乐"),
                ("切换", "play", "切换背景音乐"),
                ("停止", "stop", "停止当前背景音乐")):
            button = QToolButton()
            button.setObjectName("multiCharBgmMode")
            button.setText(text)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tooltip)
            button.setProperty("bgmAction", action)
            self.bgm_mode.addButton(button)
            self.bgm_buttons[action] = button
            modes_layout.addWidget(button)
        self.bgm_mode.buttonClicked.connect(self.change_bgm_mode)
        music_row.addWidget(self.bgm_modes, 0, Qt.AlignLeft)
        self.bgm_file = QToolButton()
        self.bgm_file.setObjectName("multiCharBgmFile")
        # 补齐音乐文件图标并左对齐展示，与上方背景图片选择框保持视觉对称
        self.bgm_file.setIcon(QIcon(str(Path(__file__).parents[1] / "icons" / "music.png")))
        self.bgm_file.setIconSize(QSize(icon_size, icon_size))
        self.bgm_file.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.bgm_file.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.bgm_file.clicked.connect(self.choose_bgm)
        music_row.addWidget(self.bgm_file, 1)
        music_column.addLayout(music_row)

        # BGM 子面板：淡出 / 淡入上下两行，沿用模式下整块隐藏不占高度
        self.bgm_subpanel, bgm_sub_layout = make_subpanel()
        self.bgm_durations = {}
        for key, text in (("fade_out_ms", "淡出"), ("fade_in_ms", "淡入")):
            slider = QSlider(Qt.Horizontal)
            slider.setObjectName("multiCharBgmDuration")
            slider.setRange(0, 3000)
            slider.setSingleStep(100)
            slider.setPageStep(500)
            slider.setValue(500)
            label = QLabel("0.5 秒")
            label.setObjectName("multiCharBgmDurationLabel")
            row_widget = slider_row(text, slider, label)
            self.bgm_durations[key] = (row_widget, slider, label)
            slider.valueChanged.connect(lambda value, name=key: self.update_bgm_duration(name, value))
            slider.sliderReleased.connect(self.save_bgm)
            bgm_sub_layout.addWidget(row_widget)
        music_column.addWidget(self.bgm_subpanel)
        grid.addLayout(music_column, row_offset + 1, 1)

        # 句前转场分组：黑场与胶囊两项结构对称——复选框开关 + 勾选后展开子面板
        transition_column = QVBoxLayout()
        transition_column.setSpacing(round(pad * 0.75))
        self.black_check = QCheckBox("黑场过渡")
        self.black_check.setObjectName("multiCharBlack")
        self.black_check.toggled.connect(self.change_black_enabled)
        transition_column.addWidget(self.black_check)
        self.black_subpanel, black_sub_layout = make_subpanel()
        self.black_duration = QSlider(Qt.Horizontal)
        self.black_duration.setObjectName("multiCharBlackDuration")
        self.black_duration.setRange(500, 4000)
        self.black_duration.setSingleStep(100)
        self.black_duration.setPageStep(500)
        self.black_duration.setValue(2000)
        self.black_duration.setToolTip("黑场总时长，包含淡出、全黑和淡入")
        self.black_duration.valueChanged.connect(self.update_black_duration_label)
        self.black_duration.sliderReleased.connect(self.commit_black_duration)
        self.black_duration_label = QLabel("2.0 秒")
        self.black_duration_label.setObjectName("multiCharBlackDurationLabel")
        black_sub_layout.addWidget(slider_row("总时长", self.black_duration, self.black_duration_label))
        transition_column.addWidget(self.black_subpanel)

        self.capsule_check = QCheckBox("胶囊转场")
        self.capsule_check.setObjectName("multiCharCapsule")
        self.capsule_check.toggled.connect(self.save_capsule)
        transition_column.addWidget(self.capsule_check)
        self.capsule_subpanel, capsule_sub_layout = make_subpanel()

        capsule_text_line = QHBoxLayout()
        capsule_text_line.setSpacing(pad)
        capsule_text_line.setContentsMargins(0, 0, 0, 0)
        self.capsule_text_info = QLabel("转场文本")
        self.capsule_text_info.setObjectName("multiCharSubpanelLabel")
        self.capsule_text_info.setMinimumWidth(tag_width)
        capsule_text_line.addWidget(self.capsule_text_info)
        self.capsule_text = QLineEdit()
        self.capsule_text.setObjectName("multiCharCapsuleText")
        self.capsule_text.setPlaceholderText("输入过场提示文字（回车或失焦自动保存）")
        self.capsule_text.editingFinished.connect(self.save_capsule)
        capsule_text_line.addWidget(self.capsule_text, 1)
        # 输入框已回车/失焦自动保存，这里仅保留弱化的“✓”图标按钮作为显式确认入口
        self.capsule_save = QToolButton()
        self.capsule_save.setObjectName("multiCharCapsuleApply")
        self.capsule_save.setText("✓")
        self.capsule_save.setToolTip("保存转场文本")
        self.capsule_save.setAccessibleName("保存转场文本")
        self.capsule_save.clicked.connect(self.save_capsule)
        capsule_text_line.addWidget(self.capsule_save)
        capsule_sub_layout.addLayout(capsule_text_line)

        self.capsule_duration = QSlider(Qt.Horizontal)
        self.capsule_duration.setObjectName("multiCharCapsuleDuration")
        self.capsule_duration.setRange(500, 5000)
        self.capsule_duration.setSingleStep(100)
        self.capsule_duration.setPageStep(500)
        self.capsule_duration.setValue(2500)
        self.capsule_duration.setToolTip("滑入和滑出固定 0.4 秒，你可以调整居中停留的时间")
        self.capsule_duration.valueChanged.connect(self.update_capsule_duration)
        self.capsule_duration.sliderReleased.connect(self.save_capsule)
        self.capsule_duration_label = QLabel("2.5 秒")
        self.capsule_duration_label.setObjectName("multiCharCapsuleDurationLabel")
        self.capsule_duration_row = slider_row("居中停留", self.capsule_duration, self.capsule_duration_label)
        capsule_sub_layout.addWidget(self.capsule_duration_row)
        transition_column.addWidget(self.capsule_subpanel)
        grid.addLayout(transition_column, row_offset + 2, 1)
        root.addWidget(self.scene_body)
        self.scene_body.hide()
        self.sync_scene_controls()
        return card

    def toggle_scene_details(self, expanded):
        """切换场景卡详细控件与摘要，不修改数据或打断正在进行的预览。"""
        self.scene_body.setVisible(expanded)
        if self.visible_group is not None:
            self.visible_group.setVisible(expanded)
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
        if self.roster is not None:
            cast = self.current_cast()
            if self.cast_message is not self.message or self.saved_cast != cast:
                self.cast_buttons.set_selection(cast)
                self.cast_message = self.message
                self.saved_cast = cast
            effective["visible_characters"] = [self.roster.label(role) for role in cast if role]
            self.update_cast_controls()
            self.refresh_speaker_label()
        dialog = getattr(self.owner, "settings_dialog", None)
        if getattr(dialog, "editor", None) is self:
            dialog.refresh_roles()
        for name, check in self.visible_checks.items():
            check.blockSignals(True)
            check.setChecked(name in effective["visible_characters"])
            check.blockSignals(False)
        self.muted_check.blockSignals(True)
        self.muted_check.setChecked(self.message.muted)
        self.muted_check.blockSignals(False)
        filename = scene.get("background", "")
        self.background_mode.blockSignals(True)
        self.background_buttons[bool(filename)].setChecked(True)
        self.background_mode.blockSignals(False)
        self.background_modes.update_selection()
        self.background_file.setText(filename)
        self.background_file.setToolTip(f"重新选择背景图片\n{filename}" if filename else "重新选择背景图片")
        self.background_file.setVisible(bool(filename))
        bgm = scene.get("bgm") or {}
        action = bgm.get("action")
        self.bgm_buttons[action].setChecked(True)
        self.bgm_modes.update_selection()
        music_file = bgm.get("file", "")
        self.bgm_file.setText(music_file or "选择音乐…")
        self.bgm_file.setToolTip(music_file or "选择背景音乐")
        self.bgm_file.setVisible(action == "play")
        for key, (row, slider, label) in self.bgm_durations.items():
            slider.blockSignals(True)
            slider.setValue(bgm.get(key, 500))
            slider.blockSignals(False)
            label.setText(f"{slider.value() / 1000:.1f} 秒")
            row.setVisible(action == "play" or (action == "stop" and key == "fade_out_ms"))
        self.bgm_subpanel.setVisible(action in ("play", "stop"))
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
        duration = SceneTiming.from_scene(scene).black_duration_ms
        self.black_duration.setValue(duration if enabled else 2000)
        self.black_duration.setVisible(enabled)
        self.black_duration.setEnabled(enabled and not first)
        self.black_duration.blockSignals(False)
        self.black_duration_label.setText(f"{self.black_duration.value() / 1000:.1f} 秒")
        self.black_duration_label.setVisible(enabled)
        self.black_duration_label.setEnabled(enabled and not first)
        self.black_subpanel.setVisible(enabled)
        capsule = scene.get("capsule") or {}
        capsule_enabled = bool(capsule.get("enabled"))
        self.capsule_check.blockSignals(True)
        self.capsule_check.setChecked(capsule_enabled)
        self.capsule_check.blockSignals(False)
        self.capsule_text.setText(str(capsule.get("text", "")))
        self.capsule_text.setVisible(capsule_enabled)
        self.capsule_save.setVisible(capsule_enabled)
        self.capsule_text_info.setVisible(capsule_enabled)
        self.capsule_duration.blockSignals(True)
        self.capsule_duration.setValue(capsule.get("hold_ms", 2500))
        self.capsule_duration.blockSignals(False)
        self.capsule_duration_label.setText(f"{self.capsule_duration.value() / 1000:.1f} 秒")
        self.capsule_duration_row.setVisible(capsule_enabled)
        self.capsule_subpanel.setVisible(capsule_enabled)
        self.transition_preview.setEnabled(self.stage_available and not self.audio_busy)
        self.update_audio_actions()
        visible_names = "、".join(effective["visible_characters"]) or "无"
        background = filename or "沿用"
        black = f"黑场 {duration / 1000:.1f}s" if enabled and not first else "无"
        if first and enabled:
            black = "首句不可用"
        if capsule_enabled:
            caption = self.capsule_text.text()
            black = (black + " → " if enabled and not first else "") + f"胶囊「{caption}」停留 {self.capsule_duration.value() / 1000:.1f}s"
        music = music_file if action == "play" else "停止" if action == "stop" else "沿用"
        self.scene_summary.set_summary(f"出场：{visible_names} ｜ 背景图片：{background} ｜ 音乐：{music} ｜ 转场：{black}")

    def update_cast_controls(self):
        """草稿换人与已保存阵容的即时互换使用独立提交入口；有草稿变更时展开确认条。"""
        index = self.message_index()
        if index < 0:
            self.cast_save.setEnabled(False)
            self.cast_cancel.setEnabled(False)
            self.cast_swap.setEnabled(False)
            self.cast_confirm_bar.hide()
            return
        casts = self.roster.casts()
        current = casts[index]
        selected = self.cast_buttons.selected()
        target = arrange_cast(current, selected)
        pending = target != current
        self.cast_save.setEnabled(not self.audio_busy and pending)
        self.cast_cancel.setEnabled(not self.audio_busy and pending)
        self.cast_swap.setEnabled(not self.audio_busy and not pending and all(current) and not self.swap_pending)
        end = cast_run_end(casts, index, current)
        affected = f"影响第 {index + 1}～{end} 句" if end > index + 1 else "影响本句"
        if pending:
            left_role, right_role = target
            if left_role and right_role:
                preview = f"左 {self.roster.label(left_role)} · 右 {self.roster.label(right_role)}"
            elif left_role or right_role:
                preview = f"单人 {self.roster.label(left_role or right_role)}"
            else:
                preview = "无角色出场"
            self.cast_draft_summary.setText(f"待应用：{preview}（{affected}）")
        self.cast_confirm_bar.setVisible(pending)
        self.cast_save.setToolTip(affected)
        self.cast_swap.setToolTip("请先保存或取消当前出场修改" if pending else f"互换左右站位（{affected}）")

    def cancel_cast_draft(self):
        """一键丢弃未提交的出场角色草稿，还原为本句已保存阵容。"""
        if self.roster is None or self.audio_busy:
            return
        self.cast_buttons.set_selection(self.current_cast())
        self.update_cast_controls()

    def commit_cast(self):
        """将出场草稿一次保存到同阵容段落，刷新当前舞台。"""
        previous = self.current_cast()
        return self.persist_cast(arrange_cast(previous, self.cast_buttons.selected()))

    def swap_cast(self):
        """交换当前两位角色并立即保存，成功后播放编排换位预览。"""
        if not self.cast_swap.isEnabled():
            return
        previous = self.current_cast()
        self.persist_cast(previous[::-1], previous_slots=self.roster.payload(previous))

    def persist_cast(self, cast, previous_slots=None):
        index = self.message_index()
        if self.audio_busy or index < 0:
            return False
        try:
            if not save_cast(self.roster, index, cast, self.owner._save_chat):
                return False
        except (RuntimeError, ValueError, OSError) as error:
            self.status.setText(str(error))
            return False
        self.sync_scene_controls()
        if previous_slots is None or not self.stage_available:
            self.refresh_cast_resources()
            self.apply_scene_preview()
        else:
            self.playing = self.can_resume = False
            self.transition_playing = self.swap_pending = True
            self.update_cast_controls()
            self.send("swap", position=self.position, previous_slots=previous_slots)
            self.update_clock()
        self.owner.refresh_chat_display()
        return True

    def finish_swap(self):
        if self.swap_pending:
            self.swap_pending = False
            self.refresh_cast_resources()
            self.update_cast_controls()

    def change_bgm_mode(self, *_):
        action = self.bgm_mode.checkedButton().property("bgmAction")
        if action == "play" and not ((self.message.scene_before or {}).get("bgm") or {}).get("file"):
            self.choose_bgm()
        else:
            self.save_bgm()

    def choose_bgm(self):
        """复用音乐管理与试听窗口，只把选择结果保存到本句。"""
        bgm = (self.message.scene_before or {}).get("bgm") or {}
        current = str(BGM_DIRECTORY / bgm["file"]) if bgm.get("file") else self.owner._current_bgm_path
        dialog = CustomBGMDialog(self.resource_window, current_path=current, selection_only=True)
        muted = self.owner.bgm_player.isMuted()
        self.owner.bgm_player.setMuted(True)
        result = dialog.exec_()
        self.owner.bgm_player.setMuted(muted)
        if result != QDialog.Accepted:
            self.sync_scene_controls()
            return
        value = {"action": "play", "file": Path(dialog.selected_path).name}
        value.update({key: round(slider.value() / 100) * 100
                      for key, (_, slider, _) in self.bgm_durations.items()})
        self.save_scene_value("bgm", value)

    def save_bgm(self):
        action = self.bgm_mode.checkedButton().property("bgmAction")
        value = None
        if action:
            value = {"action": action}
            if action == "play":
                value["file"] = (self.message.scene_before or {})["bgm"]["file"]
            value.update({key: round(slider.value() / 100) * 100
                          for key, (_, slider, _) in self.bgm_durations.items()
                          if key == "fade_out_ms" or action == "play"})
        if (self.message.scene_before or {}).get("bgm") != value:
            self.save_scene_value("bgm", value)

    def update_bgm_duration(self, key, milliseconds):
        _, slider, label = self.bgm_durations[key]
        label.setText(f"{milliseconds / 1000:.1f} 秒")
        if not slider.isSliderDown():
            self.save_bgm()

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

    def refresh_model_preview(self, opening_slots):
        """同步新模型到当前会话，保留已保存及草稿轨道。"""
        self.preview_opening_slots = opening_slots
        self.refresh_cast_resources()
        self.apply_scene_preview()
        self.show_validation()

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

    def change_background_enabled(self, button, checked):
        """选中切换时打开系统选择器，选中沿用时恢复继承背景。"""
        if not checked:
            return
        if self.background_mode.id(button):
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

    def save_capsule(self, *_args):
        """沿用句前配置保存，停用时保留标题与停留时间。"""
        value = {"enabled": self.capsule_check.isChecked(), "text": self.capsule_text.text(),
                 "hold_ms": round(self.capsule_duration.value() / 100) * 100}
        if (self.message.scene_before or {}).get("capsule") != value:
            self.save_scene_value("capsule", value)

    def update_capsule_duration(self, milliseconds):
        self.capsule_duration_label.setText(f"{milliseconds / 1000:.1f} 秒")
        if not self.capsule_duration.isSliderDown():
            self.save_capsule()

    def preview_transition(self):
        """只演示上一句场景到本句的句前阶段，结束后不启动本句语音。"""
        if self.audio_busy or not self.stage_available:
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
        self.refresh_speaker_label()
        text = f"<div style='color:#243046;'>{html.escape(self.message.text or '')}</div>"
        if self.message.translation:
            text += f"<div style='color:#6B7C96;'>{html.escape(self.message.translation)}</div>"
        self.dialogue_label.setText(text)

    def refresh_speaker_label(self):
        """按角色主题色更新说话人胶囊和替换图标。"""
        name = self.message.character_name
        can_choose = False
        if self.roster is not None:
            if self.message.speaker_id in self.roster.characters:
                name = self.roster.label(self.message.speaker_id)
            can_choose = any(self.current_cast()) or bool(self.message.speaker_id or self.message.character_name)
            self.speaker_label.setEnabled(can_choose and not self.audio_busy)
        display_text = name or ("选择说话人" if can_choose else "")
        self.speaker_label.setText(display_text)
        self.speaker_label.setVisible(bool(display_text))
        badge = character_badge_color(name)
        fg_hex = character_badge_text_color(badge)
        if self.roster is not None:
            background = badge.name()
            border = "1px solid transparent"
            hover = "border-color: rgba(36, 48, 70, 0.35);"
            if not name:
                background, fg_hex = "#F2F6FC", "#365C97"
                border = "1px dashed #B8CBE8"
                hover = "background-color: #E7F0FD; border-color: #7295F2;"
            self.speaker_label.setIcon(speaker_switch_icon(fg_hex))
            radius = self.speaker_label.sizeHint().height() // 4
            self.speaker_label.setStyleSheet(
                f"QToolButton#multiCharSpeaker {{background-color: {background}; color: {fg_hex};"
                f" border: {border}; border-radius: {radius}px; padding: 0; font-weight: bold;}}"
                f"QToolButton#multiCharSpeaker:hover:enabled {{{hover}}}"
            )
        else:
            self.speaker_label.setStyleSheet(
                f"background-color: {badge.name()}; color: {fg_hex};"
                "border: none; border-radius: 8px; padding: 2px 12px; font-weight: bold;"
            )
        self.speaker_label.adjustSize()
        self.speaker_label.updateGeometry()

    def choose_speaker(self):
        """弹出当前出场角色和无说话人选项。"""
        menu = QMenu(self.resource_window)
        menu.setObjectName("multiCharSpeakerMenu")
        message = self.message
        on_stage = [role for role in self.current_cast() if role]
        current_speaker = message.speaker_id or ""
        if on_stage:
            choices = QWidget(menu)
            choices.setObjectName("multiCharSceneRow")
            row = QHBoxLayout(choices)
            row.setContentsMargins(8, 8, 8, 6)
            row.setSpacing(6)
            for role in on_stage:
                # 顶部仅放置当前在场角色按钮并保持纯角色名，当前说话人以实心主题色高亮
                button = role_button(self.roster, role, choices, active=(role == current_speaker))
                button.clicked.connect(lambda checked, selected=role: self.change_speaker(menu, message, selected))
                row.addWidget(button)
            action = QWidgetAction(menu)
            action.setDefaultWidget(choices)
            menu.addAction(action)
            menu.addSeparator()
        clear_item = menu.addAction("无说话人")
        clear_item.setEnabled(bool(current_speaker or message.character_name))
        clear_item.triggered.connect(lambda checked=False: self.change_speaker(menu, message, ""))
        fit_menu_to_contents(menu)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.speaker_label.mapToGlobal(QPoint(0, self.speaker_label.height() + 2)))

    def change_speaker(self, menu, message, role):
        """切换说话人并立即保存，保留原音频引用与静音状态。"""
        if self.audio_busy or self.message is not message:
            return
        if role != "" and (not role or role not in self.current_cast()):
            return
        name = self.roster.label(role) if role else ""
        changed = message.speaker_id != role or message.character_name != name
        try:
            if changed and not save_speaker(self.chat, message, role, name, self.owner._save_chat):
                return
        except (RuntimeError, ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        menu.close()
        self.sync_scene_controls()
        self.owner.refresh_chat_display()
        self.apply_scene_preview()
        if changed and message.audio_path and name:
            # 切换说话人不自动重合成，给用户明确提示以便按需重新生成声线
            self.status.setText(f"已将说话人改为「{name}」（保留原音频，如需换声线请点击「重新生成音频」）。")

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
        try:
            self.chat._timeline.check_editable()
        except RuntimeError as error:
            self.status.setText(str(error))
            return False
        snapshot = self.message.as_dict()
        old_text = self.message.text
        old_timeline = self.message.performance_timeline
        new_text = self.original_input.toPlainText()
        self.message.text = new_text
        self.message.translation = self.translation_input.toPlainText()
        span = audio_span(self.message.audio_path, new_text)
        if not self.message.audio_path:
            self.message.performance_timeline, _ = trim_timeline(old_timeline, span.duration_ms)
        if not self.owner._save_chat():
            self.message.restore_failed_edit(snapshot)
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
        self.baseline = self.timeline_view(self.message)
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
        self.update_audio_actions()
        self.original_input.setEnabled(not busy)
        self.translation_input.setEnabled(not busy)
        self.timeline_window.setEnabled(not busy)
        self.resource_window.setEnabled(not busy)

    def update_audio_actions(self):
        """按当前说话人切换情绪区，合成期间统一锁定操作。"""
        has_speaker = bool(self.message.character_name)
        voice = self.voice_character()
        show_emotion = voice is None or not voice.is_sakiko
        self.regenerate_pill.setEnabled(has_speaker and not self.audio_busy)
        self.regenerate_pill.setToolTip("" if has_speaker else "本句是无角色消息，未指定语音角色")
        self.emotion_button.setVisible(show_emotion)
        self.emotion_divider.setVisible(show_emotion)
        self.emotion_button.setEnabled(voice is not None)
        label = EMOTION_LABELS_ZH[self.message.emotion.as_string()]
        self.emotion_button.setText(label)
        self.emotion_button.setAccessibleName(f"本句情绪：{label}")

    def voice_character(self):
        resolve = getattr(self.owner, "_voice_character", None)
        return resolve(self.message) if resolve is not None and self.message.character_name else None

    def choose_emotion(self):
        """用合成器的素材查找规则标明专用参考素材和回退选项。"""
        voice = self.voice_character()
        if self.audio_busy or voice is None or voice.is_sakiko:
            return
        menu = QMenu(self.resource_window)
        menu.setObjectName("multiCharEmotionMenu")
        message = self.message
        default_audio, _, _ = voice.get_reference_materials_for_emotion(EmotionEnum.HAPPINESS)
        for emotion in EmotionEnum:
            label = EMOTION_LABELS_ZH[emotion.as_string()]
            exact_audio, _, _ = voice.get_reference_materials_for_emotion(emotion, fallback=False)
            if not exact_audio:
                label += "（沿用默认）" if default_audio else "（未配置）"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(emotion == message.emotion)
            action.triggered.connect(lambda checked, selected=emotion: self.change_emotion(message, voice, selected))
        fit_menu_to_contents(menu)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.emotion_button.mapToGlobal(QPoint(0, self.emotion_button.height() + 2)))

    def change_emotion(self, message, voice, emotion):
        if (self.audio_busy or self.message is not message or message.emotion == emotion
                or voice is not self.voice_character() or voice.is_sakiko):
            return
        try:
            if not save_emotion(self.chat, message, emotion, self.owner._save_chat):
                return
        except (RuntimeError, ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.update_audio_actions()
        self.owner.refresh_chat_display()
        self.status.setText("情绪已保存，现有音频待重新生成。" if message.audio_path else "情绪已保存。")

    def open_settings(self):
        if not self.audio_busy:
            self.prepare_edit()
            self.owner.open_settings_dialog(editor=self)

    def configure_reference_audio(self):
        """暂停编排预览，在当前编排窗口中打开说话人的参考音频配置。"""
        if not self.audio_busy and self.message.character_name:
            self.prepare_edit()
            self.owner.configure_sentence_reference_audio(self.message, self.resource_window)

    def regenerate_audio(self):
        """保存或放弃文本草稿后，复用主控制台合成并立即持久更新音频。"""
        if self.audio_busy or not self.message.character_name or not self.resolve_text_draft(ask_audio=False):
            return
        self.stop()
        self.status.setText("正在生成音频…")
        started = self.owner.regenerate_audio(self.message_index(), on_complete=self.audio_regenerated)
        if started:
            self.set_audio_busy(True)
        else:
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
        self.timeline_toolbar = toolbar
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

    def update_timeline_preferred_height(self):
        """在按钮样式确定后计算自然高度，不从默认视口或窗口当前尺寸反推。"""
        self.timeline_toolbar.ensurePolished()
        root = self.timeline_window.layout()
        margins = root.contentsMargins()
        controls_height = max(self.timeline_toolbar.sizeHint().height(), self.timeline_toolbar.minimumSizeHint().height())
        preferred = self.view.intrinsic_height() + controls_height + margins.top() + margins.bottom() + root.spacing()
        self.timeline_window.setProperty("preferredHeight", preferred)

    def send(self, action, **payload):
        """发送同一编辑会话的命令，避免旧窗口消息干扰新会话。"""
        if action in ("enter", "play", "seek", "stop", "scene", "transition", "resource", "swap"):
            if action != "swap":
                self.finish_swap()
            payload["scene_context"] = {
                "messages": (self.roster.messages() if self.roster is not None else
                             [message.as_dict() for message in self.chat.message_list]),
                "index": self.message_index(),
            }
            if self.preview_opening_slots is not None:
                payload["scene_context"]["opening_slots"] = self.preview_opening_slots
        if action in ("enter", "scene", "transition", "swap"):
            payload["turn"] = self.turn()
        self.sequence += 1
        self.owner.to_live2d_change_character_queue.put(
            dict(payload, type="multi_char_preview", action=action, session=self.session_id, sequence=self.sequence,
                 script_session=getattr(self.owner, "_playback_session", None),
                 script_revision=getattr(self.owner, "_playback_revision", None))
        )

    def turn(self):
        """建立预览传输副本，不修改消息也不加入正式播放列表。"""
        turn = self.message.as_dict()
        if self.roster is not None and self.message.speaker_id in self.roster.characters:
            turn["character_name"] = self.roster.label(self.message.speaker_id)
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
        if result.get("action") == "toggle_play":
            self.toggle_play()
            return
        if result.get("notice"):
            self.status.setText(result["notice"])
            return
        if result.get("error"):
            self.finish_swap()
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
                if not self.transition_playing:
                    self.finish_swap()
        elif self.seek_resume is None and result.get("sequence", self.sequence) == self.sequence:
            self.position = min(self.span.duration_ms, max(0, int(result.get("position", 0))))
            self.playing = bool(result.get("playing"))
            if self.position >= self.span.duration_ms:
                self.can_resume = False
            self.update_clock()

    def refresh_resources(self, *_):
        """根据目标角色和标签显示资源，不禁用其他角色已有轨道。"""
        if not self.names:
            self.list.clear()
            self.types.setTabEnabled(1, False)
            self.custom.hide()
            self.refresh_button.setEnabled(False)
            if hasattr(self, "view"):
                self.view.rebuild()
            return
        self.refresh_button.setEnabled(True)
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
        if not self.names:
            return
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
            if self.roster is not None and name not in self.resources:
                continue
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
        from ui_motion_editor.live2d_expression_editor import CustomExpressionDialog
        from ui_motion_editor.live2d_performance_editor import confirm_description_changes
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
        self.message.performance_timeline = (self.roster.timeline(candidate, for_storage=True)
                                             if self.roster is not None else candidate)
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
        if self.audio_busy or not self.resolve_text_draft() or not self.resolve_cast_draft():
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
        if self.closing or self.audio_busy or not self.resolve_text_draft() or not self.resolve_cast_draft():
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
        QApplication.instance().removeEventFilter(self)
        dialog = getattr(self.owner, "settings_dialog", None)
        if getattr(dialog, "editor", None) is self:
            dialog.close()
        self.send("exit")
        self.layout.restore()
        self.resource_window.close()
        self.timeline_window.close()
        self.owner.setEnabled(True)
        self.owner.raise_()
        self.owner.performance_editor = None
        self.owner.performance_editor_finished(self.chat)
        self.resource_window.deleteLater()
        self.timeline_window.deleteLater()
        self.deleteLater()
