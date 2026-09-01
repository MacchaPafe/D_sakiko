from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chat.rolling_summary import load_rolling_summary_prompt, save_rolling_summary_prompt
from ui_main.theme import ThemePalette, build_dialog_theme_stylesheet


MIN_SUMMARY_THRESHOLD_PERCENT = 70
MAX_SUMMARY_THRESHOLD_PERCENT = 90
SUMMARY_THRESHOLD_STEP_PERCENT = 5
DEFAULT_SUMMARY_THRESHOLD_PERCENT = 80


class RollingSummaryPromptDialog(QDialog):
    """编辑滚动摘要的用户自定义提示词。"""

    def __init__(self, palette: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑压缩提示词")
        self.setMinimumSize(560, 440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        editor_title = QLabel("个性化压缩要求", self)
        title_font = editor_title.font()
        title_font.setBold(True)
        editor_title.setFont(title_font)
        layout.addWidget(editor_title)

        intro = QLabel(
            "触发上下文压缩时，以下提示词将发送给执行压缩任务的大模型。"
            "一些硬性约束已写好，你只需加入你的个性化需求即可",
            self,
        )
        intro.setWordWrap(True)
        intro.setProperty("dialogRole", "secondary")
        layout.addWidget(intro)

        self.prompt_edit = QPlainTextEdit(self)
        self.prompt_edit.setObjectName("summaryPromptEditor")
        self.prompt_edit.setPlainText(load_rolling_summary_prompt())
        self.prompt_edit.setPlaceholderText("例如：重点保留人物之间的情绪变化和重要约定……")
        layout.addWidget(self.prompt_edit, 1)

        divider = QFrame(self)
        divider.setObjectName("summaryPromptDivider")
        divider.setFrameShape(QFrame.HLine)
        layout.addWidget(divider)

        info_title = QLabel("什么是上下文压缩", self)
        info_font = info_title.font()
        info_font.setItalic(True)
        info_font.setBold(True)
        info_title.setFont(info_font)
        layout.addWidget(info_title)
        self.expand_button = QToolButton(self)
        self.expand_button.setObjectName("summaryInfoExpandButton")
        self.expand_button.setText("点击展开")
        self.expand_button.setArrowType(Qt.RightArrow)
        self.expand_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.expand_button.setAutoRaise(True)
        self.expand_button.setCheckable(True)
        small_font = self.expand_button.font()
        if small_font.pointSizeF() > 0:
            small_font.setPointSizeF(small_font.pointSizeF() * 0.88)
        self.expand_button.setFont(small_font)
        layout.addWidget(self.expand_button)
        self.detail_label = QLabel(
            "随着对话变长，完整历史会占用越来越多 token，也可能分散大模型对当前内容的注意力。"
            "当上下文用量达到设定阈值时，程序会调用当前大模型，把较早的对话整理成累计摘要，"
            "同时保留最近 10 轮原文。之后的请求会携带摘要和最近消息，原始聊天记录不会被删除。",
            self,
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setProperty("dialogRole", "secondary")
        self.detail_label.setContentsMargins(18, 0, 0, 4)
        self.detail_label.setVisible(False)
        layout.addWidget(self.detail_label)
        self.expand_button.toggled.connect(self._toggle_details)  # noqa

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("取消", self)
        save_button = QPushButton("保存", self)
        save_button.setObjectName("summaryPromptSaveButton")
        save_button.setDefault(True)
        cancel_button.clicked.connect(self.reject)  # noqa
        save_button.clicked.connect(self.accept)  # noqa
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

        self.setStyleSheet(
            build_dialog_theme_stylesheet(palette)
            + f"""
            QPlainTextEdit#summaryPromptEditor {{
                background-color: {palette.surface};
                color: {palette.text_primary};
                border: 1px solid {palette.border_subtle};
                border-radius: 7px;
                padding: 10px;
                selection-background-color: {palette.surface_selected};
            }}
            QPlainTextEdit#summaryPromptEditor:focus {{
                border: 2px solid {palette.focus_ring};
            }}
            QFrame#summaryPromptDivider {{
                color: {palette.border_subtle};
                background-color: {palette.border_subtle};
                max-height: 1px;
                border: none;
            }}
            QToolButton#summaryInfoExpandButton {{
                background-color: transparent;
                color: {palette.text_secondary};
                border: none;
                padding: 2px 0;
            }}
            QToolButton#summaryInfoExpandButton:hover {{
                color: {palette.text_accent};
                background-color: transparent;
                border: none;
            }}
            QPushButton#summaryPromptSaveButton {{
                background-color: {palette.accent};
                color: {palette.on_accent};
                border-color: {palette.accent};
            }}
            QPushButton#summaryPromptSaveButton:hover {{
                background-color: {palette.accent_hover};
                color: {palette.on_accent};
                border-color: {palette.accent_hover};
            }}
            """
        )

    def _toggle_details(self, expanded: bool) -> None:
        self.expand_button.setText("收起" if expanded else "点击展开")
        self.expand_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.detail_label.setVisible(expanded)

    def prompt_text(self) -> str:
        return self.prompt_edit.toPlainText()


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


@dataclass(frozen=True)
class ContextUsageSizing:
    """记录上下文用量组件使用的尺寸参数。"""

    indicator_size: int = 26
    popup_width: int = 188
    popup_font_size: int = 12


def resolve_context_usage_sizing(screen_height: int, platform_name: str) -> ContextUsageSizing:
    """根据运行平台返回上下文用量组件的尺寸参数。"""
    is_macos = platform_name == "darwin"
    return ContextUsageSizing(
        indicator_size=max(20, int(screen_height * 0.015)),
        popup_width=188 if is_macos else max(150, int(screen_height * 0.23)),
        popup_font_size=12 if is_macos else max(10, int(screen_height * 0.017)),
    )


class ContextUsagePopup(QFrame):
    """显示上下文 token 详情的轻量悬浮窗口。"""

    summaryThresholdChanged = pyqtSignal(float)
    summaryEnabledChanged = pyqtSignal(bool)

    def __init__(
        self,
        palette: ThemePalette,
        parent: QWidget | None = None,
        width: int = 188,
        font_size: int = 12,
    ) -> None:
        """初始化浮窗结构和样式。"""
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("contextUsagePopup")
        width = max(150, width)
        font_size = max(10, font_size)
        self.setFixedWidth(width)
        line_height = max(font_size + 2, int(font_size * 1.5))
        self._font_size = font_size
        self._line_height = line_height

        self.used_label = QLabel(self)
        self.limit_label = QLabel(self)
        self.percent_label = QLabel(self)
        self.summary_enabled_checkbox = QCheckBox("启用上下文压缩", self)
        self.summary_threshold_label = QLabel(self)
        self.summary_threshold_slider = QSlider(Qt.Horizontal, self)
        self.edit_summary_prompt_button = QPushButton("修改压缩提示词", self)
        self.summary_threshold_slider.setObjectName("summaryCompressionThresholdSlider")
        self.summary_threshold_slider.setRange(
            MIN_SUMMARY_THRESHOLD_PERCENT // SUMMARY_THRESHOLD_STEP_PERCENT,
            MAX_SUMMARY_THRESHOLD_PERCENT // SUMMARY_THRESHOLD_STEP_PERCENT,
        )
        self.summary_threshold_slider.setSingleStep(1)
        self.summary_threshold_slider.setPageStep(1)
        self.summary_threshold_slider.valueChanged.connect(self._on_summary_threshold_changed)  # noqa
        self.summary_enabled_checkbox.toggled.connect(self._on_summary_enabled_changed)  # noqa
        self.edit_summary_prompt_button.clicked.connect(self._edit_summary_prompt)  # noqa

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)
        layout.addWidget(self.used_label)
        layout.addWidget(self.limit_label)
        layout.addWidget(self.percent_label)
        layout.addSpacing(4)
        layout.addWidget(self.summary_enabled_checkbox)
        layout.addWidget(self.summary_threshold_label)
        layout.addWidget(self.summary_threshold_slider)
        layout.addWidget(self.edit_summary_prompt_button)

        self._theme_palette = palette
        self._apply_style(font_size, line_height)
        self.set_summary_threshold_ratio(DEFAULT_SUMMARY_THRESHOLD_PERCENT / 100)
        self.set_summary_enabled(False)

    def _apply_style(self, font_size: int, line_height: int) -> None:
        """应用浮窗与阈值滑块样式。"""
        palette = self._theme_palette
        self.setStyleSheet(
            f"""
            QFrame#contextUsagePopup {{
                background-color: {palette.surface};
                border: 1px solid {palette.border_subtle};
                border-radius: 8px;
            }}
            QFrame#contextUsagePopup QLabel,
            QFrame#contextUsagePopup QCheckBox {{
                background-color: transparent;
                color: {palette.text_primary};
                font-size: {font_size}px;
                line-height: {line_height}px;
            }}
            QFrame#contextUsagePopup QPushButton {{
                background-color: transparent;
                color: {palette.text_accent};
                border: 1px solid {palette.border_subtle};
                border-radius: 5px;
                padding: 5px;
                font-size: {font_size}px;
            }}
            QFrame#contextUsagePopup QPushButton:hover {{
                background-color: {palette.surface_selected};
            }}
            QFrame#contextUsagePopup QPushButton:disabled {{
                background-color: transparent;
                color: {palette.text_secondary};
                border-color: {palette.border_subtle};
            }}
            QSlider#summaryCompressionThresholdSlider::groove:horizontal {{
                height: 4px;
                background: {palette.border_subtle};
                border-radius: 2px;
            }}
            QSlider#summaryCompressionThresholdSlider::sub-page:horizontal {{
                background: {palette.accent};
                border-radius: 2px;
            }}
            QSlider#summaryCompressionThresholdSlider::handle:horizontal {{
                width: 12px;
                margin: -4px 0;
                background: {palette.accent};
                border: 1px solid {palette.accent};
                border-radius: 6px;
            }}
            """
        )

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """同步角色语义色板到阈值滑块和浮窗文字。"""
        if not isinstance(palette, ThemePalette):
            raise TypeError("palette 必须是 ThemePalette")
        self._theme_palette = palette
        self._apply_style(self._font_size, self._line_height)

    def set_summary_threshold_ratio(self, ratio: float) -> None:
        """设置上下文压缩阈值，不触发用户修改信号。"""
        percent = max(
            MIN_SUMMARY_THRESHOLD_PERCENT,
            min(MAX_SUMMARY_THRESHOLD_PERCENT, int(round(float(ratio) * 100))),
        )
        slider_value = int(round(percent / SUMMARY_THRESHOLD_STEP_PERCENT))
        self.summary_threshold_slider.blockSignals(True)
        self.summary_threshold_slider.setValue(slider_value)
        self.summary_threshold_slider.blockSignals(False)
        self._update_summary_threshold_text(slider_value * SUMMARY_THRESHOLD_STEP_PERCENT)

    def set_summary_enabled(self, enabled: bool) -> None:
        """同步上下文压缩开关，不触发用户修改信号。"""
        self.summary_enabled_checkbox.blockSignals(True)
        self.summary_enabled_checkbox.setChecked(bool(enabled))
        self.summary_enabled_checkbox.blockSignals(False)
        self.summary_threshold_label.setEnabled(bool(enabled))
        self.summary_threshold_slider.setEnabled(bool(enabled))
        self.edit_summary_prompt_button.setEnabled(bool(enabled))

    def _on_summary_enabled_changed(self, enabled: bool) -> None:
        self.summary_threshold_label.setEnabled(enabled)
        self.summary_threshold_slider.setEnabled(enabled)
        self.edit_summary_prompt_button.setEnabled(enabled)
        self.summaryEnabledChanged.emit(enabled)

    def _edit_summary_prompt(self) -> None:
        self.hide()
        parent = self.parentWidget().window() if self.parentWidget() is not None else None
        dialog = RollingSummaryPromptDialog(self._theme_palette, parent)
        if dialog.exec_() == QDialog.Accepted:
            save_rolling_summary_prompt(dialog.prompt_text())

    def _on_summary_threshold_changed(self, slider_value: int) -> None:
        percent = slider_value * SUMMARY_THRESHOLD_STEP_PERCENT
        self._update_summary_threshold_text(percent)
        self.summaryThresholdChanged.emit(percent / 100)

    def _update_summary_threshold_text(self, percent: int) -> None:
        self.summary_threshold_label.setText(f"上下文压缩阈值：{percent}%")
        tooltip = (
            f"当累计 token 数达到上下文上限的 {percent}% 时，自动进行历史记录压缩，"
            "以达到节省费用、减少 LLM 注意力涣散的目的。"
        )
        self.summary_threshold_label.setToolTip(tooltip)
        self.summary_threshold_slider.setToolTip(tooltip)

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
        self.limit_label.setText(f"token 上限：{limit_text}")
        self.percent_label.setText(f"当前占比：{percent_text}")

    @staticmethod
    def _format_tokens(value: int) -> str:
        """将 token 数格式化为便于阅读的整数文本。"""
        return f"{max(0, value):,}"


class ContextUsageIndicator(QWidget):
    """绘制上下文 token 用量圆环，并在点击时展示详情浮窗。"""

    summaryThresholdChanged = pyqtSignal(float)
    summaryEnabledChanged = pyqtSignal(bool)

    def __init__(
        self,
        palette: ThemePalette,
        parent: QWidget | None = None,
        size: int = 26,
        popup_width: int = 188,
        popup_font_size: int = 12,
    ) -> None:
        """初始化圆环组件的默认状态。"""
        super().__init__(parent)
        self._snapshot = ContextUsageSnapshot(used_tokens=None, token_limit=None)
        self._theme_palette = palette
        self._size = max(20, size)
        self._popup = ContextUsagePopup(palette, self, popup_width, popup_font_size)
        self._popup.summaryThresholdChanged.connect(self.summaryThresholdChanged.emit)  # noqa
        self._popup.summaryEnabledChanged.connect(self.summaryEnabledChanged.emit)  # noqa

        self.setObjectName("contextUsageIndicator")
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("上下文用量：未知")

    def set_theme_palette(self, palette: ThemePalette) -> None:
        """设置圆环和详情浮窗使用的角色语义色板。"""
        if not isinstance(palette, ThemePalette):
            raise TypeError("palette 必须是 ThemePalette")
        self._theme_palette = palette
        self._popup.set_theme_palette(palette)
        self.update()

    def set_summary_threshold_ratio(self, ratio: float) -> None:
        """同步上下文压缩阈值到详情浮窗。"""
        self._popup.set_summary_threshold_ratio(ratio)

    def set_summary_enabled(self, enabled: bool) -> None:
        """同步上下文压缩开关到详情浮窗。"""
        self._popup.set_summary_enabled(enabled)

    def set_snapshot(self, snapshot: ContextUsageSnapshot) -> None:
        """设置新的上下文 token 用量快照并刷新展示。"""
        self._snapshot = snapshot
        self._popup.set_snapshot(snapshot)
        self._update_tooltip()
        self.update()

    def sizeHint(self) -> QSize:
        """返回圆环组件的推荐尺寸。"""
        return QSize(self._size, self._size)

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

        base_pen = QPen(QColor(self._theme_palette.border_subtle))
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
        return QColor(self._theme_palette.accent)
