from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QPoint, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


@dataclass(frozen=True)
class ContextUsageSnapshot:
    """记录上下文 token 用量组件展示所需的纯数据。"""

    used_tokens: int | None
    token_limit: int | None
    error_message: str = ""

    @property
    def ratio(self) -> float | None:
        """返回已用 token 占上限的比例，上限不可用时返回 None。"""
        if self.used_tokens is None or self.token_limit is None or self.token_limit <= 0:
            return None
        return max(0.0, self.used_tokens / self.token_limit)

    @property
    def has_error(self) -> bool:
        """返回当前快照是否表示统计失败。"""
        return bool(self.error_message)


class ContextUsagePopup(QFrame):
    """显示上下文 token 详情的轻量悬浮窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化浮窗结构和样式。"""
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("contextUsagePopup")
        self.setFixedWidth(188)

        self.used_label = QLabel(self)
        self.limit_label = QLabel(self)
        self.percent_label = QLabel(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)
        layout.addWidget(self.used_label)
        layout.addWidget(self.limit_label)
        layout.addWidget(self.percent_label)

        self.setStyleSheet(
            """
            QFrame#contextUsagePopup {
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.10);
                border-radius: 8px;
            }
            QFrame#contextUsagePopup QLabel {
                background-color: transparent;
                color: #4D5560;
                font-size: 12px;
                line-height: 18px;
            }
            """
        )

    def set_snapshot(self, snapshot: ContextUsageSnapshot) -> None:
        """根据最新 token 快照刷新浮窗文案。"""
        if snapshot.used_tokens is None:
            used_text = "计算失败" if snapshot.has_error else "未知"
        else:
            used_text = self._format_tokens(snapshot.used_tokens)

        limit_text = "未知" if snapshot.token_limit is None else self._format_tokens(snapshot.token_limit)
        ratio = snapshot.ratio
        percent_text = "未知" if ratio is None else f"{ratio * 100:.1f}%"

        self.used_label.setText(f"已用 token：{used_text}")
        self.limit_label.setText(f"总 token 上限：{limit_text}")
        self.percent_label.setText(f"当前占比：{percent_text}")

    @staticmethod
    def _format_tokens(value: int) -> str:
        """将 token 数格式化为便于阅读的整数文本。"""
        return f"{max(0, value):,}"


class ContextUsageIndicator(QWidget):
    """绘制上下文 token 用量圆环，并在点击时展示详情浮窗。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化圆环组件的默认状态。"""
        super().__init__(parent)
        self._snapshot = ContextUsageSnapshot(used_tokens=None, token_limit=None)
        self._theme_color = QColor("#7799CC")
        self._popup = ContextUsagePopup(self)

        self.setObjectName("contextUsageIndicator")
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("上下文用量：未知")

    def set_theme_color(self, color: str) -> None:
        """设置圆环已用部分的主题色。"""
        theme_color = QColor(color)
        self._theme_color = theme_color if theme_color.isValid() else QColor("#7799CC")
        self.update()

    def set_snapshot(self, snapshot: ContextUsageSnapshot) -> None:
        """设置新的上下文 token 用量快照并刷新展示。"""
        self._snapshot = snapshot
        self._popup.set_snapshot(snapshot)
        self._update_tooltip()
        self.update()

    def sizeHint(self) -> QSize:
        """返回圆环组件的推荐尺寸。"""
        return QSize(26, 26)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """响应点击事件，切换详情浮窗显示状态。"""
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if self._popup.isVisible():
            self._popup.hide()
        else:
            self._show_popup()
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制默认空环和按比例加粗的已用圆弧。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        ratio = self._snapshot.ratio
        base_width = 2.4
        progress_width = 2.8 if ratio is None else min(5.0, 2.8 + 2.2 * min(ratio, 1.0))
        max_width = max(base_width, progress_width)
        rect = QRectF(
            max_width / 2 + 1,
            max_width / 2 + 1,
            self.width() - max_width - 2,
            self.height() - max_width - 2,
        )

        base_pen = QPen(QColor("#DDE4EA"))
        if self._snapshot.has_error:
            base_pen.setColor(QColor("#B8C0CA"))
        base_pen.setWidthF(base_width)
        base_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(base_pen)
        painter.drawEllipse(rect)

        if ratio is None or self._snapshot.has_error:
            painter.end()
            return

        progress_pen = QPen(self._progress_color(ratio))
        progress_pen.setWidthF(progress_width)
        progress_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(progress_pen)
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * min(ratio, 1.0)))
        painter.end()

    def _show_popup(self) -> None:
        """在圆环上方显示详情浮窗。"""
        self._popup.set_snapshot(self._snapshot)
        self._popup.adjustSize()
        pos = self.mapToGlobal(QPoint((self.width() - self._popup.width()) // 2, -self._popup.height() - 8))
        self._popup.move(pos)
        self._popup.show()

    def _update_tooltip(self) -> None:
        """根据当前快照刷新悬停提示文本。"""
        if self._snapshot.has_error:
            detail = self._snapshot.error_message[:80]
            self.setToolTip(f"上下文用量：计算失败（{detail}）")
            return
        if self._snapshot.used_tokens is None:
            self.setToolTip("上下文用量：未知")
            return
        used_text = ContextUsagePopup._format_tokens(self._snapshot.used_tokens)
        if self._snapshot.token_limit is None:
            self.setToolTip(f"上下文用量：{used_text} / 未知 tokens")
            return
        limit_text = ContextUsagePopup._format_tokens(self._snapshot.token_limit)
        self.setToolTip(f"上下文用量：{used_text} / {limit_text} tokens")

    def _progress_color(self, ratio: float) -> QColor:
        """根据当前用量比例返回圆环已用部分颜色。"""
        if ratio >= 0.95:
            return QColor("#E35D5B")
        if ratio >= 0.80:
            return QColor("#D99A2B")
        return QColor(self._theme_color)
