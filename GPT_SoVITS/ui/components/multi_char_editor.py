"""小剧场独占编排会话：两个非模态窗口共用草稿和现有 Live2D 前台。"""
from __future__ import annotations

import html
import json
import uuid
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, QMimeData, QPointF, QRectF, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QDrag, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QTabBar,
                             QLineEdit, QListWidget, QListWidgetItem, QListView, QPushButton,
                             QMessageBox, QDoubleSpinBox, QSlider, QApplication, QStyle, QSizePolicy)

from performance_types import trim_timeline, sort_timeline_events, timeline_tracks, valid_timeline_events
from live2d_support.multi_char_resources import TheaterResources, audio_span, assign_colors
from ui.components.multi_char_timeline import MultiCharTimeline, MIME
from ui.components.multi_char_windows import MultiCharWindowLayout
from ui_constants import char_info_json


def character_badge_color(name):
    """将角色代表色调整为适合搭配白字的柔彩胶囊底色，呼应舞台对话框铭牌。"""
    raw = QColor(char_info_json.get(name, {}).get("theme_color", "#F06292"))
    if not raw.isValid():
        raw = QColor("#F06292")
    h, s, l, _ = raw.getHsl()
    if h < 0:
        return QColor.fromHsl(-1, 0, 115)
    return QColor.fromHsl(h, min(200, max(135, s)), min(142, max(108, l)))


class ResourceList(QListWidget):
    """资源单击试播，超过拖动阈值后只传递资源引用。"""

    def __init__(self, session):
        """复用 Qt 原生单击与拖拽判定，避免按下时抢先试播。"""
        super().__init__()
        self.session = session
        self.column_timer = QTimer(self)
        self.column_timer.setSingleShot(True)
        self.column_timer.timeout.connect(self.update_columns)
        self.setDragEnabled(True)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSpacing(0)
        self.itemClicked.connect(self.preview)

    def resizeEvent(self, event):
        """合并视口尺寸变化，在本轮 Qt 布局结束后更新两列。"""
        super().resizeEvent(event)
        self.column_timer.start(0)

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
        self.can_resume = False
        self.position = 0
        self.seek_resume = None
        self.selection = None
        self.stage_available = True
        self.expression_dialog = None
        self.resource_window = EditorWindow(self)
        self.timeline_window = EditorWindow(self, True)
        self.build_resources()
        self.build_timeline()
        for window in (self.resource_window, self.timeline_window):
            for button in window.findChildren(QPushButton):
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
            timeline = {"characters": {name: {"motions": [], "expressions": []} for name in self.names}}
            if message.character_name in self.resources:
                performance = message.performance.as_dict() if message.performance else None
                timeline["characters"][message.character_name] = self.resources[message.character_name].defaults(
                    performance, message.emotion.as_string(),
                )
            assign_colors(timeline, self.palette)
            message.performance_timeline = timeline
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

    def _transport_icon(self, kind):
        """绘制圆润抗锯齿矢量播放与停止图标，契合浅色卡片风格。"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#5A7FE7" if kind == "pause" else "#415882"))
        if kind == "play":
            path = QPainterPath()
            path.moveTo(QPointF(9, 6))
            path.lineTo(QPointF(25, 16))
            path.lineTo(QPointF(9, 26))
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "pause":
            painter.drawRoundedRect(QRectF(8, 6, 5.5, 20), 2.5, 2.5)
            painter.drawRoundedRect(QRectF(18.5, 6, 5.5, 20), 2.5, 2.5)
        else:
            painter.drawRoundedRect(QRectF(8, 8, 16, 16), 3.5, 3.5)
        painter.end()
        return QIcon(pixmap)

    def message_index(self):
        """按对象身份定位当前句，避免内容相同的消息被误认为同一句。"""
        return next((index for index, message in enumerate(self.chat.message_list)
                     if message is self.message), -1)

    def update_navigation(self):
        """首尾禁用对应切句按钮，消息被删除时禁用两端。"""
        index = self.message_index()
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(0 <= index < len(self.chat.message_list) - 1)

    def switch_message(self, step):
        """处理当前草稿后原位切句，更新音频与四轨并隔离旧预览回执。"""
        index = self.message_index()
        target_index = index + step
        if index < 0 or not 0 <= target_index < len(self.chat.message_list):
            self.update_navigation()
            return
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
                    return
            elif chosen is not discard:
                return
        target = self.chat.message_list[target_index]
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
        self.speaker_label.setText(target.character_name)
        badge = character_badge_color(target.character_name)
        self.speaker_label.setStyleSheet(
            f"background-color: {badge.name()}; color: #FFFFFF;"
            "border-radius: 8px; padding: 2px 12px; font-weight: bold;"
        )
        text = f"<div style='color:#243046;'>{html.escape(target.text or '')}</div>"
        if target.translation:
            text += f"<div style='color:#6B7C96;'>{html.escape(target.translation)}</div>"
        self.dialogue_label.setText(text)
        self.update_navigation()
        self.read_waveform()
        self.view.rebuild()
        self.view.horizontalScrollBar().setValue(0)
        self.update_clock()
        self.show_validation()
        if self.span.missing:
            self.status.setText("音频缺失/损坏：临时无声预览，保留所有原编排。")
        self.send("enter")

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
        badge_bg = character_badge_color(self.message.character_name)
        title.setStyleSheet(
            f"background-color: {badge_bg.name()}; color: #FFFFFF;"
            "border-radius: 8px; padding: 2px 12px; font-weight: bold;"
        )
        card_layout.addWidget(title)
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
        root.addWidget(card)
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
        bar.addWidget(self.search, 1)
        root.addLayout(bar)
        self.list = ResourceList(self)
        self.list.setObjectName("multiCharResourceList")
        root.addWidget(self.list, 1)
        footer = QHBoxLayout()
        self.previous = QPushButton("上一句")
        self.previous.clicked.connect(lambda: self.switch_message(-1))
        footer.addWidget(self.previous)
        self.next = QPushButton("下一句")
        self.next.clicked.connect(lambda: self.switch_message(1))
        footer.addWidget(self.next)
        footer.addStretch(1)
        self.custom = QPushButton("自定义表情…")
        self.custom.clicked.connect(self.custom_expression)
        footer.addWidget(self.custom)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload_resources)
        footer.addWidget(refresh)
        root.addLayout(footer)
        self.drag_hint = QLabel("可拖入下方轨道编排演出")
        self.drag_hint.setObjectName("multiCharHint")
        self.drag_hint.setAlignment(Qt.AlignCenter)
        policy = self.drag_hint.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.drag_hint.setSizePolicy(policy)
        root.addWidget(self.drag_hint)
        self.drag_hint.hide()
        self.update_navigation()
        self.characters.currentChanged.connect(self.refresh_resources)
        self.types.currentChanged.connect(self.refresh_resources)
        self.search.textChanged.connect(self.refresh_resources)

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

        bar.addWidget(self.status, 1)
        discard = QPushButton("放弃修改")
        discard.clicked.connect(self.discard)
        bar.addWidget(discard)
        save = QPushButton("保存")
        save.clicked.connect(self.save)
        bar.addWidget(save)
        root.addWidget(toolbar)
        self.update_clock()
        desired_view_height = self.view.top + len(self.view.rows) * self.view.row_height + self.view.group_gap + 16
        controls_height = root.minimumSize().height() - self.view.minimumHeight()
        self.timeline_window.setProperty("preferredHeight", desired_view_height + controls_height)

    def send(self, action, **payload):
        """发送同一编辑会话的命令，避免旧窗口消息干扰新会话。"""
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
        if result.get("error"):
            self.status.setText(result["error"])
            self.playing = False
            self.can_resume = False
            self.update_clock()
            return
        if result.get("type") == "multi_char_window":
            self.layout.bind(result.get("handle"), result.get("geometry"))
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
        """保存表情或手动刷新后重新解析资源及失效标志。"""
        self.stop()
        for name, old in list(self.resources.items()):
            self.resources[name] = TheaterResources(str(old.path) if old.path else "", old.direction)
        self.send("refresh")
        self.refresh_resources()
        self.view.rebuild()

    def preview_resource(self, payload):
        """清理整句预览，独占试播当前资源并保留游标。"""
        self.playing = False
        self.can_resume = False
        self.send("resource", position=self.position, **payload)
        self.update_clock()

    def prepare_edit(self):
        """修改前暂停并保留游标，修改后使用定位规则重新开始。"""
        if self.playing:
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
        self.status.setText("Live2D 前台已关闭；编排仍可保存或放弃。")
        self.update_clock()

    def save_current(self):
        """按实际时长校验后保存原消息，写盘失败回滚消息但保留草稿。"""
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
        if self.save_current():
            self.finish()

    def discard(self):
        """放弃草稿，保留进入时已持久保存的时间轴。"""
        self.finish()

    def request_close(self):
        """两个窗口共用一次未保存确认，取消时保留完整编辑会话。"""
        if self.closing:
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
        if self.closed:
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
