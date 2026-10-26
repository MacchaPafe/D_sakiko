"""双人四轨时间轴：绘制、跨窗口拖入、吸附及拖入垃圾桶删除。"""
from __future__ import annotations

import json
from copy import deepcopy

from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import (QColor, QPen, QBrush, QLinearGradient, QPainter,
                         QPainterPath, QPalette, QPolygonF, QFontMetrics)
from PyQt5.QtWidgets import QGraphicsObject, QGraphicsScene, QGraphicsView, QApplication, QStyle

from performance_types import valid_timeline_events, sort_timeline_events
from ui_constants import char_info_json

MIME = "application/x-dsakiko-multi-char-resource"

_MOTION_HUES = (216, 188, 252, 162, 230, 198)
_EXPRESSION_HUES = (340, 18, 322, 352, 28, 330)


def _stable_hue_choice(text, choices):
    """根据条目名称稳定选取色相，保证同一动作或表情在重绘时色调一致。"""
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(str(text or "")))
    return choices[seed % len(choices)]


def _pastel_palette(color_str, row=0, name=""):
    """将事件颜色转化为分轨冷暖区分的高级瓷感渐变、柔光阴影、圆点徽标与深色文字。"""
    is_expression = (row % 2 == 1)
    fallback = "#F06292" if is_expression else "#5A7FE7"
    raw = QColor(color_str) if color_str else QColor(fallback)
    if not raw.isValid():
        raw = QColor(fallback)
    h, s, _, _ = raw.getHsl()
    if h < 0:
        return {
            "top": QColor("#F6F8FC"),
            "bottom": QColor("#E9EEF6"),
            "border": QColor("#BCC8DC"),
            "selected": QColor("#4A6FD8"),
            "dot": QColor("#7B8DA8"),
            "text": QColor("#334155"),
        }
    # 动作轨倾向清透冷色/宝石色系，表情轨倾向柔美暖粉/珊瑚色系，避免全屏同色粉块
    if not is_expression and (h < 32 or h > 320):
        h = _stable_hue_choice(name, _MOTION_HUES)
    elif is_expression and 75 <= h <= 270:
        h = _stable_hue_choice(name, _EXPRESSION_HUES)

    sat = min(200, max(145, s))
    return {
        "top": QColor.fromHsl(h, min(155, sat), 245),
        "bottom": QColor.fromHsl(h, min(178, sat), 229),
        "border": QColor.fromHsl(h, min(172, sat), 176),
        "selected": QColor.fromHsl(h, min(220, sat + 25), 112),
        "dot": QColor.fromHsl(h, min(215, sat + 20), 132),
        "text": QColor.fromHsl(h, min(165, sat), 48),
    }


def _character_pill_color(name):
    """提取角色代表色并柔化为清爽的粉彩糖果色竖条。"""
    raw = QColor(char_info_json.get(name, {}).get("theme_color", "#5A7FE7"))
    if not raw.isValid():
        raw = QColor("#5A7FE7")
    h, s, l, _ = raw.getHsl()
    if h < 0:
        return QColor("#7E92B2")
    return QColor.fromHsl(h, min(200, max(140, s)), min(152, max(112, l)))


class MotionBlock(QGraphicsObject):
    """一个具名事件条块；显示长度与原始动作长度分离。"""

    def __init__(self, row, event, width, name, color, available, height):
        """保存原事件引用，选择及排序不依赖数组下标。"""
        super().__init__()
        self.row = row
        self.event = event
        self.width = max(8, width)
        self.height = height
        self.solid_width = self.width
        self.name = name
        self.color = QColor(color)
        self.available = available
        self.selected = False
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self):
        """返回包含边框与底部柔光阴影的条块绘制区域。"""
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter, option, widget=None):
        """以带柔光微阴影的大圆角瓷感胶囊与左端宝石圆点徽标展示事件。"""
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(self.scene().font())
        tones = _pastel_palette(self.color.name(), self.row, self.name)
        solid = QRectF(0, 0, self.solid_width, self.height)
        box = solid.adjusted(1.5, 1.0, -1.5, -3.0)
        radius = min(box.height() * 0.44, max(5.0, box.width() * 0.44))

        # 多层柔光环境微阴影，消除原生绘图扁平剪纸感
        if box.width() > 10 and box.height() > 10:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(28, 48, 88, 12))
            painter.drawRoundedRect(box.adjusted(0.5, 2.2, -0.5, 2.2), radius, radius)
            painter.setBrush(QColor(28, 48, 88, 18))
            painter.drawRoundedRect(box.adjusted(0.2, 1.1, -0.2, 1.1), radius, radius)

        grad = QLinearGradient(0, box.top(), 0, box.bottom())
        if self.available:
            grad.setColorAt(0.0, tones["top"])
            grad.setColorAt(1.0, tones["bottom"])
        else:
            grad.setColorAt(0.0, QColor("#FFF1F2"))
            grad.setColorAt(1.0, QColor("#FFE4E6"))

        painter.setBrush(QBrush(grad))
        if self.selected:
            painter.setPen(QPen(tones["selected"] if self.available else QColor("#E11D48"), 2.4))
        else:
            painter.setPen(
                QPen(
                    tones["border"] if self.available else QColor("#FDA4AF"),
                    1.35,
                    Qt.SolidLine if self.available else Qt.DashLine,
                )
            )
        painter.drawRoundedRect(box, radius, radius)

        # 内部顶边瓷感高光环
        if box.width() > 16 and box.height() > 14:
            inner = box.adjusted(1.3, 1.3, -1.3, -1.3)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 210), 1.15))
            painter.drawRoundedRect(inner, max(2.0, radius - 1.2), max(2.0, radius - 1.2))

        # 与半圆左端同心呼应的圆润宝石圆点徽标（取代原本与圆弧冲突的直竖线）
        dot_d = min(11.0, max(6.5, round(box.height() * 0.24)))
        dot_inset = max(7.5, round(radius * 0.52))
        if self.solid_width >= dot_inset + dot_d + 18:
            dot_y = box.top() + (box.height() - dot_d) * 0.5
            dot_rect = QRectF(box.left() + dot_inset, dot_y, dot_d, dot_d)
            painter.setPen(QPen(QColor(255, 255, 255, 215), 1.2))
            painter.setBrush(tones["dot"] if self.available else QColor("#F43F5E"))
            painter.drawEllipse(dot_rect)
            left_pad = round(dot_inset + dot_d + max(6.0, box.height() * 0.14))
        else:
            left_pad = 8

        if self.solid_width < self.width:
            tail_box = QRectF(self.solid_width, box.top(), self.width - self.solid_width, box.height())
            tail_fill = QColor(tones["bottom"])
            tail_fill.setAlpha(110)
            painter.setBrush(tail_fill)
            painter.setPen(QPen(tones["border"], 1.2, Qt.DashLine))
            painter.drawRoundedRect(tail_box, radius, radius)

        painter.setPen(tones["text"] if self.available else QColor("#BE123C"))
        text = painter.fontMetrics().elidedText(
            self.name, Qt.ElideRight, max(1, int(self.solid_width - left_pad - 8))
        )
        painter.drawText(
            QRectF(box.left() + left_pad, box.top(), max(1.0, box.width() - left_pad - 6.0), box.height()),
            Qt.AlignVCenter,
            text,
        )


class MultiCharTimeline(QGraphicsView):
    """按角色分组的四轨视图，与资源窗口共享编辑会话。"""

    selected = pyqtSignal(object)
    changed = pyqtSignal()
    seeking = pyqtSignal(int, bool)
    trackSelected = pyqtSignal(str, str)
    editing = pyqtSignal()
    dragging = pyqtSignal(bool)
    notice = pyqtSignal(str)

    def __init__(self, session, parent=None):
        """初始化坐标映射和拖动状态，所有时刻以整数毫秒保存。"""
        super().__init__(parent)
        self.session = session
        self.setScene(QGraphicsScene(self))
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.margin = 140
        self.top = 56
        self.row_height = 38
        self.group_gap = 12
        self.zoom = 1.0
        self.pixels = 0.1
        self.playhead = 0
        self.rows = [(name, kind) for name in session.names for kind in ("motions", "expressions")]
        self.blocks = []
        self.selection = None
        self.drag = None
        self.press = None
        self.ghost = None
        self.ruler_drag = False
        self.snap_x = None
        self.trash_hover = False
        self.waveform = []
        self.rebuild()

    def x_at(self, ms):
        """将毫秒转换到场景坐标。"""
        return self.margin + ms * self.pixels

    def time_at(self, x):
        """将场景横坐标转换为毫秒，允许校验发现负数。"""
        return round((x - self.margin) / self.pixels)

    def row_y(self, row):
        """返回第 row 条轨道的场景顶部纵坐标，两位角色分组之间留出明显间距。"""
        return self.top + row * self.row_height + (self.group_gap if row >= 2 else 0)

    def row_at(self, y):
        """查找鼠标对应轨道，双人分组间隙按中线归属上下轨道，超出四轨返回无目标。"""
        if y < self.top:
            return None
        total_h = len(self.rows) * self.row_height + self.group_gap
        if y >= self.top + total_h:
            return None
        mid_gap = self.top + 2 * self.row_height + self.group_gap * 0.5
        if y < mid_gap:
            return min(1, max(0, int((y - self.top) // self.row_height)))
        second_top = self.top + 2 * self.row_height + self.group_gap
        return min(len(self.rows) - 1, 2 + max(0, int((y - second_top) // self.row_height)))

    def events(self, row):
        """返回可解释事件，不修改损坏存档数据。"""
        name, kind = self.rows[row]
        tracks = self.session.draft.get("characters", {}).get(name, {})
        return valid_timeline_events(tracks.get(kind, []), kind)

    def enabled_row(self, row):
        """V2 表情轨只展示保留数据，不允许编辑。"""
        name, kind = self.rows[row]
        return kind == "motions" or self.session.resources[name].version == "v3"

    def rebuild(self):
        """根据草稿重绘分组圆角轨道槽、角色双轨名牌、波形和胶囊条块。"""
        self.scene().clear()
        self.scene().setFont(self.session.timeline_window.font())
        metrics = QFontMetrics(self.scene().font())
        self.top = max(52, round(metrics.height() * 2.35))
        self.group_gap = 12
        self.row_height = max(round(metrics.height() * 1.42),
                              (self.viewport().height() - self.top - self.group_gap - 4) // len(self.rows))
        self.row_padding = max(4, round(metrics.height() * 0.16))
        self.margin = max(145, max(metrics.horizontalAdvance(name + " · 表情")
                                   for name in self.session.names) + round(metrics.height() * 1.35))
        self.blocks = []
        duration = self.session.span.duration_ms
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff if self.zoom <= 1.001 else Qt.ScrollBarAsNeeded)
        self.pixels = max(0.005, (self.viewport().width() - self.margin - 28) / max(1, duration)) * self.zoom
        if self.zoom <= 1.001:
            width = max(200, self.viewport().width() - 2)
        else:
            width = max(self.viewport().width() - 2, self.x_at(duration) + 18)
            for row in range(len(self.rows)):
                for event in self.events(row):
                    width = max(width, self.x_at(event["at_ms"]) + 60)
        scene_height = self.top + len(self.rows) * self.row_height + self.group_gap
        self.scene().setSceneRect(0, 0, width, scene_height)

        ruler_h = round(metrics.height() * 1.2)
        no_pen = QPen(Qt.NoPen)
        card_border = QPen(QColor("#D5E0F0"), 1.1)
        grid_pen = QPen(QColor("#E4ECF7"), 1, Qt.DotLine)
        sub_tick_pen = QPen(QColor("#C8D5E8"), 1)
        major_tick_pen = QPen(QColor("#8FA4C4"), 1.25)

        # 全画布柔和云母雾蓝底色（卡片与轨道槽悬浮其上）
        self.scene().addRect(0, 0, width, scene_height, no_pen, QBrush(QColor("#EAF0F9")))

        # 左上角音频状态圆角卡片
        header_card_x = 4.0
        header_card_w = self.margin - 9.0
        audio_card = QPainterPath()
        audio_card.addRoundedRect(QRectF(header_card_x, 3.0, header_card_w, self.top - 7.0), 9.0, 9.0)
        self.scene().addPath(audio_card, card_border, QBrush(QColor("#F8FAFE")))

        # 右上角标尺 + 声波内嵌圆角槽
        well_x = self.margin - 1.0
        well_w = max(40.0, width - well_x - 5.0)
        ruler_well = QPainterPath()
        ruler_well.addRoundedRect(QRectF(well_x, 3.0, well_w, self.top - 7.0), 9.0, 9.0)
        self.scene().addPath(ruler_well, card_border, QBrush(QColor("#F9FBFF")))
        self.scene().addLine(well_x + 1, ruler_h, well_x + well_w - 1, ruler_h, QPen(QColor("#E1E9F5"), 1))

        # 细分短刻度与主时间刻度
        for sub in range(51):
            sub_x = self.x_at(duration * sub / 50)
            if sub % 5 != 0:
                self.scene().addLine(sub_x, ruler_h - 4, sub_x, ruler_h, sub_tick_pen)
        for tick in range(11):
            ms = duration * tick / 10
            x = self.x_at(ms)
            self.scene().addLine(x, ruler_h - 7, x, ruler_h, major_tick_pen)
            label = self.scene().addText(f"{ms / 1000:.1f}", self.scene().font())
            label.setDefaultTextColor(QColor("#526582"))
            label_w = label.boundingRect().width()
            if tick == 0:
                label_x = x + round(metrics.height() * 0.22)
            elif tick == 10:
                label_x = min(x - label_w * 0.5, well_x + well_w - label_w - 4)
            else:
                label_x = x - label_w / 2
            label.setPos(label_x, -1)

        # 波形绘制：晴空蓝对称柔光声波，无声时绘呼吸感微虚线
        middle = round(ruler_h + (self.top - 4 - ruler_h) * 0.5)
        center_pen = QPen(QColor("#CBD8EC"), 1.1, Qt.DashLine)
        self.scene().addLine(self.margin, middle, self.x_at(duration), middle, center_pen)
        if self.waveform:
            amp_scale = max(7.0, (self.top - ruler_h - 6) * 0.38)
            glow_path = QPainterPath()
            core_path = QPainterPath()
            span_w = max(1.0, self.x_at(duration) - self.margin)
            count = len(self.waveform)
            for index, amplitude in enumerate(self.waveform):
                x = self.margin + index * span_w / count
                h_px = max(1.0, min(1.0, float(amplitude)) * amp_scale)
                glow_path.moveTo(x, middle - h_px)
                glow_path.lineTo(x, middle + h_px)
                core_path.moveTo(x, middle - h_px * 0.72)
                core_path.lineTo(x, middle + h_px * 0.72)
            self.scene().addPath(glow_path, QPen(QColor(90, 127, 231, 65), 2.2))
            self.scene().addPath(core_path, QPen(QColor("#5A7FE7"), 1.3))

        # 左上角状态小胶囊标签（先绘胶囊底衬，再叠加文字）
        audio_text = "音频缺失" if self.session.span.missing else "音频波形" if self.session.span.path else "无声演出"
        badge_fill = (
            QColor("#FFF1F2") if self.session.span.missing
            else QColor("#EBF3FF") if self.session.span.path
            else QColor("#EEF3FA")
        )
        badge_stroke = (
            QColor("#FECDD3") if self.session.span.missing
            else QColor("#BFDBFE") if self.session.span.path
            else QColor("#D5E0F0")
        )
        audio_label = self.scene().addText(audio_text, self.scene().font())
        audio_label.setDefaultTextColor(
            QColor("#E11D48") if self.session.span.missing
            else QColor("#3B66D6") if self.session.span.path
            else QColor("#64748B")
        )
        al_rect = audio_label.boundingRect()
        al_x = header_card_x + (header_card_w - al_rect.width()) * 0.5
        al_y = (self.top - 4 - al_rect.height()) * 0.5
        badge_path = QPainterPath()
        badge_path.addRoundedRect(
            QRectF(al_x - 8, al_y + 2, al_rect.width() + 16, al_rect.height() - 4),
            (al_rect.height() - 4) * 0.5,
            (al_rect.height() - 4) * 0.5,
        )
        badge_item = self.scene().addPath(badge_path, QPen(badge_stroke, 1), QBrush(badge_fill))
        badge_item.setZValue(0)
        audio_label.setZValue(1)
        audio_label.setPos(al_x, al_y)

        # 当前右上资源面板选中的目标轨道
        active_pair = None
        if hasattr(self.session, "characters") and hasattr(self.session, "types"):
            char_idx = self.session.characters.currentIndex()
            if 0 <= char_idx < len(self.session.names):
                active_pair = (
                    self.session.names[char_idx],
                    "motions" if self.session.types.currentIndex() == 0 else "expressions",
                )

        # 先按角色绘制双层合并名牌卡片，强化两人之间的分组间隙感
        pill_w = max(5.0, round(metrics.height() * 0.18))
        pill_x = header_card_x + max(6.0, round(metrics.height() * 0.22))
        header_text_x = pill_x + pill_w + max(6.0, round(metrics.height() * 0.25))
        for group_idx, char_name in enumerate(self.session.names):
            first_row = group_idx * 2
            if first_row >= len(self.rows):
                break
            gy_top = self.row_y(first_row) + 1.5
            gy_bottom = self.row_y(min(len(self.rows) - 1, first_row + 1)) + self.row_height - 1.5
            group_h = gy_bottom - gy_top
            group_active = active_pair and active_pair[0] == char_name

            group_card = QPainterPath()
            group_card.addRoundedRect(QRectF(header_card_x, gy_top, header_card_w, group_h), 10.0, 10.0)
            self.scene().addPath(
                group_card,
                QPen(QColor("#ADC6F5" if group_active else "#D3DFEF"), 1.25 if group_active else 1.0),
                QBrush(QColor("#F9FBFF" if group_active else "#F5F8FD")),
            )

            # 角色贯穿双轨的专属应援色胶囊条
            pill_color = _character_pill_color(char_name)
            pill_pad = max(8.0, round(group_h * 0.1))
            char_pill = QPainterPath()
            char_pill.addRoundedRect(
                QRectF(pill_x, gy_top + pill_pad, pill_w, max(12.0, group_h - 2 * pill_pad)),
                pill_w * 0.5,
                pill_w * 0.5,
            )
            self.scene().addPath(char_pill, no_pen, QBrush(pill_color))

            # 同角色动作与表情行之间的柔和内部分隔线
            mid_y = self.row_y(first_row + 1)
            self.scene().addLine(
                header_text_x, mid_y, header_card_x + header_card_w - 8, mid_y, QPen(QColor("#E3EAF5"), 1)
            )

        # 绘制四条内嵌圆角轨道槽（Track Wells）与轨道头标签
        for row, (name, kind) in enumerate(self.rows):
            y = self.row_y(row)
            enabled = self.enabled_row(row)
            is_active = (name, kind) == active_pair

            # 左侧选中行柔光高亮衬底
            if is_active:
                active_head = QPainterPath()
                active_head.addRoundedRect(
                    QRectF(header_text_x - 3, y + 3.5, header_card_x + header_card_w - header_text_x - 2, self.row_height - 7.0),
                    7.0,
                    7.0,
                )
                self.scene().addPath(active_head, QPen(QColor("#C3D8FC"), 1), QBrush(QColor("#E8F1FF")))

            # 右侧内嵌圆角轨道槽（每条轨道上下留出呼吸感边距）
            well_rect = QRectF(well_x, y + 2.0, well_w, self.row_height - 4.0)
            well_radius = min(11.0, well_rect.height() * 0.28)
            if not enabled:
                lane_bg = QColor("#E4E9F2")
                lane_border = QColor("#CFD8E6")
            elif is_active:
                lane_bg = QColor("#FFFFFF")
                lane_border = QColor("#A9C5F8")
            elif kind == "motions":
                lane_bg = QColor("#FFFFFF")
                lane_border = QColor("#D5E0F0")
            else:
                lane_bg = QColor("#F8FAFE")
                lane_border = QColor("#DCE5F2")

            well_path = QPainterPath()
            well_path.addRoundedRect(well_rect, well_radius, well_radius)
            self.scene().addPath(well_path, QPen(lane_border, 1.2 if is_active else 1.0), QBrush(lane_bg))

            # 轨道槽内部刻度参考虚线
            for tick in range(1, 10):
                tx = self.x_at(duration * tick / 10)
                self.scene().addLine(tx, well_rect.top() + 2, tx, well_rect.bottom() - 2, grid_pen)

            label = self.scene().addText(
                name + " · " + ("动作" if kind == "motions" else "表情"),
                self.scene().font(),
            )
            label.setDefaultTextColor(
                QColor("#1E40AF") if is_active
                else QColor("#2D3B52") if enabled
                else QColor("#94A3B8")
            )
            label.setTextWidth(self.margin - header_text_x - 8)
            label.setPos(header_text_x, y + (self.row_height - label.boundingRect().height()) / 2)

            events = self.events(row)
            for index, event in enumerate(events):
                resource = event["resource"]
                item = self.session.resources[name].resolve(kind, resource)
                reset = kind == "expressions" and resource is None
                title = "恢复默认" if reset else item["name"] if item else "失效：" + str(resource.get("file_name", "未知"))
                start = event["at_ms"]
                end = events[index + 1]["at_ms"] if index + 1 < len(events) else duration
                length = self.session.resources[name].duration(resource) if kind == "motions" else None
                if kind == "motions":
                    end = min(end, start + length) if length else min(end, start + round(45 / self.pixels))
                end = min(end, duration)
                default_color = "#8E9CB4" if reset else "#F06292" if kind == "expressions" else "#5A7FE7"
                block = MotionBlock(
                    row, event, self.x_at(end) - self.x_at(start), title,
                    event.get("color", default_color), bool(item or reset),
                    self.row_height - 2 * self.row_padding,
                )
                block.setPos(self.x_at(start), y + self.row_padding)
                block.selected = self.selection is event
                tooltip = title
                if resource:
                    tooltip += "\n" + resource.get("file_name", "")
                if kind == "motions" and length is None:
                    tooltip += "\n时长未知"
                if start >= duration:
                    tooltip += "\n开始时间越界"
                block.setToolTip(tooltip)
                self.scene().addItem(block)
                self.blocks.append(block)

        self.viewport().update()

    def resizeEvent(self, event):
        """保持缩放倍数，窗口大小变化时重算时间与像素比例。"""
        super().resizeEvent(event)
        if hasattr(self, "rows"):
            self.rebuild()

    def wheelEvent(self, event):
        """Ctrl 滚轮缩放时间轴，普通滚轮沿用视图滚动。"""
        if event.modifiers() & Qt.ControlModifier:
            self.zoom = min(20, max(1, self.zoom * (1.2 if event.angleDelta().y() > 0 else 1 / 1.2)))
            self.rebuild()
            event.accept()
        else:
            super().wheelEvent(event)

    def candidate(self, row, event, at, modifiers):
        """按像素距离选最近合法吸附点，末尾始终用动作原始单轮长度。"""
        duration = self.session.span.duration_ms
        name, kind = self.rows[row]
        length = self.session.resources[name].duration(event["resource"]) if kind == "motions" else None
        targets = [(0, 0), (self.playhead, self.playhead)]
        if kind == "motions":
            if length:
                targets.append((duration - length, duration))
            for other in self.events(row):
                if other is event:
                    continue
                other_length = self.session.resources[name].duration(other["resource"])
                if length:
                    targets.append((other["at_ms"] - length, other["at_ms"]))
                if other_length:
                    end = other["at_ms"] + other_length
                    targets.append((end, end))
        self.snap_x = None
        occupied = {other["at_ms"] for other in self.events(row) if other is not event}
        legal = [(point, guide) for point, guide in targets if 0 <= point < duration and point not in occupied]
        if legal and not modifiers & Qt.AltModifier:
            point, guide = min(legal, key=lambda item: abs(item[0] - at))
            if abs(point - at) * self.pixels <= 9:
                self.snap_x = self.x_at(guide)
                at = point
        return at, 0 <= at < duration and at not in occupied

    def show_overlap(self):
        """拖动时展示原始尾段轮廓，草稿不裁剪；松手重建后只留实际段。"""
        if not self.ghost:
            return
        row, moving, at, valid = self.ghost
        name, kind = self.rows[row]
        if kind != "motions" or not valid:
            return
        starts = [event["at_ms"] for event in self.events(row) if event is not moving] + [at]
        for block in self.blocks:
            if block.row != row or block.event is moving:
                continue
            start = block.event["at_ms"]
            length = self.session.resources[name].duration(block.event["resource"])
            if length is None:
                continue
            end = min(start + length, self.session.span.duration_ms)
            next_start = min((point for point in starts if point > start), default=end)
            block.prepareGeometryChange()
            block.width = max(8, (end - start) * self.pixels)
            block.solid_width = max(8, (min(end, next_start) - start) * self.pixels)
            block.update()

    def trash_rect(self):
        """返回场景中的临时垃圾桶热区，覆盖在右下角而不挤动布局。"""
        corner = self.mapToScene(self.viewport().rect().bottomRight())
        unit = self.session.timeline_window.font().pixelSize()
        return QRectF(corner.x() - unit * 7, corner.y() - unit * 2.5, unit * 6.5, unit * 2)

    def drawForeground(self, painter, rect):
        """绘制樱桃粉顶帽游标、大圆角拖拽预览、吸附线及浮动垃圾桶。"""
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(self.scene().font())
        unit = max(14.0, float(self.scene().font().pixelSize()))
        cap_half_w = max(5.5, unit * 0.32)
        cap_shoulder_y = max(10.0, unit * 0.5)
        cap_tip_y = max(16.0, unit * 0.78)
        if self.snap_x is not None:
            painter.setPen(QPen(QColor("#F59E0B"), 1.6, Qt.DashLine))
            painter.drawLine(QPointF(self.snap_x, cap_tip_y), QPointF(self.snap_x, self.sceneRect().height()))

        # 播放游标（呼应左上角舞台铭牌的樱桃粉顶帽 + 垂直线，起点防越界）
        x = self.x_at(self.playhead)
        playhead_color = QColor("#F0547E")
        painter.setPen(QPen(playhead_color, 2.0))
        painter.drawLine(QPointF(x, cap_tip_y - 1.0), QPointF(x, self.sceneRect().height()))
        left_x = max(float(self.margin), x - cap_half_w)
        right_x = max(left_x + cap_half_w * 1.6, x + cap_half_w)
        cap = QPolygonF([
            QPointF(left_x, 2.5),
            QPointF(right_x, 2.5),
            QPointF(right_x, cap_shoulder_y),
            QPointF(x, cap_tip_y),
            QPointF(left_x, cap_shoulder_y),
        ])
        painter.setPen(QPen(QColor("#FFFFFF"), 1.4))
        painter.setBrush(playhead_color)
        painter.drawPolygon(cap)

        if self.ghost:
            row, event, at, valid = self.ghost
            name, kind = self.rows[row]
            length = self.session.resources[name].duration(event["resource"]) if kind == "motions" else None
            stroke = QColor("#10B981") if valid else QColor("#F43F5E")
            fill = QColor(16, 185, 129, 45) if valid else QColor(244, 63, 94, 45)
            painter.setPen(QPen(stroke, 1.8, Qt.DashLine))
            painter.setBrush(fill)
            full_length = length or 400
            later = [other["at_ms"] for other in self.events(row)
                     if other is not event and other["at_ms"] > at]
            visible = min(full_length, min(later, default=at + full_length) - at)
            y = self.row_y(row) + self.row_padding
            height = self.row_height - 2 * self.row_padding
            radius = min(height * 0.44, max(5.0, visible * self.pixels * 0.44))
            painter.drawRoundedRect(QRectF(self.x_at(at), y, max(12, visible * self.pixels), height), radius, radius)
            if visible < full_length:
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(
                    QRectF(self.x_at(at + visible), y, (full_length - visible) * self.pixels, height), radius, radius
                )
        if self.drag:
            area = self.trash_rect()
            painter.setPen(QPen(QColor("#F43F5E" if self.trash_hover else "#FDA4AF"), 1.8 if self.trash_hover else 1.2))
            painter.setBrush(QColor("#FFE4E6") if self.trash_hover else QColor(255, 241, 242, 240))
            painter.drawRoundedRect(area, 8, 8)
            icon = self.style().standardIcon(QStyle.SP_TrashIcon)
            icon.paint(painter, int(area.left() + 8), int(area.top() + (area.height() - 18) / 2), 18, 18)
            painter.setPen(QColor("#BE123C"))
            painter.drawText(
                area.adjusted(26, 0, -4, 0), Qt.AlignCenter,
                "松开删除" if self.trash_hover else "删除",
            )

    def payload(self, mime):
        """读取本应用的资源拖拽负载，不接收外部任意路径。"""
        if not mime.hasFormat(MIME):
            return None
        try:
            payload = json.loads(bytes(mime.data(MIME)).decode("utf-8"))
            if payload.get("session") == self.session.session_id:
                return payload
        except (ValueError, TypeError):
            pass
        return None

    def dragEnterEvent(self, event):
        """只接收当前编排会话发起的跨窗口拖入。"""
        if self.payload(event.mimeData()):
            self.editing.emit()
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        """校验角色/类型并更新吸附预览，不在移动过程中写草稿。"""
        payload = self.payload(event.mimeData())
        pos = self.mapToScene(event.pos())
        row = self.row_at(pos.y())
        self.ghost = None
        if payload and row is not None and self.enabled_row(row):
            if self.rows[row] == (payload["character"], payload["kind"]):
                draft = {"resource": payload["resource"]}
                at, valid = self.candidate(row, draft, self.time_at(pos.x()), event.keyboardModifiers())
                self.ghost = (row, draft, at, valid)
                self.show_overlap()
                event.acceptProposedAction()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        """离开时间轴只清理视觉预览，拖拽会话仍可跨窗口继续。"""
        self.ghost = None
        self.snap_x = None
        self.rebuild()

    def dropEvent(self, event):
        """落下时一次性创建合法事件，错误目标不覆盖已有编排。"""
        self.dragMoveEvent(event)
        if self.ghost and self.ghost[3]:
            row, draft, at, _ = self.ghost
            name, kind = self.rows[row]
            tracks = self.session.draft.setdefault("characters", {}).setdefault(name, {"motions": [], "expressions": []})
            new_event = {"at_ms": at, "resource": deepcopy(draft["resource"])}
            tracks.setdefault(kind, []).append(new_event)
            sort_timeline_events(tracks[kind])
            self.selection = new_event
            self.changed.emit()
            self.selected.emit((row, new_event))
            event.acceptProposedAction()
        else:
            self.notice.emit("请放入对应角色轨道，且开始时间不能越界或重复。")
        self.ghost = None
        self.snap_x = None
        self.rebuild()

    def mousePressEvent(self, event):
        """点击选中条块，时间尺点击开始定位，轨道标题切换资源。"""
        pos = self.mapToScene(event.pos())
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if pos.y() < self.top and pos.x() >= self.margin:
            self.ruler_drag = True
            self.seeking.emit(max(0, min(self.session.span.duration_ms, self.time_at(pos.x()))), False)
            return
        row = self.row_at(pos.y())
        if row is not None and pos.x() < self.margin:
            self.trackSelected.emit(*self.rows[row])
            return
        block = next((item for item in self.items(event.pos()) if isinstance(item, MotionBlock)), None)
        self.selection = block.event if block else None
        self.press = None
        if block and self.enabled_row(block.row):
            self.press = (event.pos(), block.row, block.event, pos.x() - block.pos().x())
            self.selected.emit((block.row, block.event))
        else:
            self.selected.emit(None)
        for item in self.blocks:
            item.selected = item.event is self.selection
            item.update()

    def mouseMoveEvent(self, event):
        """超过阈值后才移动旧事件，移动期间只绘制候选位置。"""
        pos = self.mapToScene(event.pos())
        if self.ruler_drag:
            self.seeking.emit(max(0, min(self.session.span.duration_ms, self.time_at(pos.x()))), False)
        elif self.press and event.buttons() & Qt.LeftButton:
            origin, row, item, offset = self.press
            if not self.drag and (event.pos() - origin).manhattanLength() >= QApplication.startDragDistance():
                self.editing.emit()
                self.drag = self.press
                self.dragging.emit(True)
            if self.drag:
                self.trash_hover = self.trash_rect().contains(pos)
                for block in self.blocks:
                    if block.event is item:
                        block.setOpacity(0.3)
                at, valid = self.candidate(row, item, self.time_at(pos.x() - offset), event.modifiers())
                valid = valid and self.row_at(pos.y()) == row
                self.ghost = (row, item, at, valid)
                self.show_overlap()
                self.viewport().update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """松开时提交移动或垃圾桶删除；其他位置恢复原事件。"""
        pos = self.mapToScene(event.pos())
        if self.ruler_drag:
            self.ruler_drag = False
            self.seeking.emit(max(0, min(self.session.span.duration_ms, self.time_at(pos.x()))), True)
        elif self.drag:
            _, row, item, _ = self.drag
            name, kind = self.rows[row]
            events = self.session.draft["characters"][name][kind]
            if self.trash_rect().contains(pos):
                events[:] = [other for other in events if other is not item]
                self.selection = None
                self.selected.emit(None)
                self.changed.emit()
            elif self.ghost and self.ghost[3]:
                item["at_ms"] = self.ghost[2]
                sort_timeline_events(events)
                self.changed.emit()
                self.selected.emit((row, item))
        self.press = self.drag = self.ghost = None
        self.dragging.emit(False)
        self.snap_x = None
        self.trash_hover = False
        self.rebuild()

    def keyPressEvent(self, event):
        """Esc 仅取消进行中的拖动，不误关闭两个编辑窗口。"""
        if event.key() == Qt.Key_Escape:
            self.dragging.emit(False)
            self.press = self.drag = self.ghost = None
            self.snap_x = None
            self.rebuild()
        else:
            super().keyPressEvent(event)
