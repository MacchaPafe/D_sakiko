from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from PyQt5.QtCore import QEvent, QObject, QPoint, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeyEvent
from PyQt5.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget


# 命令是否可见于命令栏
CommandVisibility = Literal["public", "hidden"]
# 命令的执行方法
# immediate：选择该命令后立刻执行
# confirm：选择该命令后弹出确认框，确认后执行
# send：选择该命令后，需要按下回车键发送消息才能执行
CommandExecutionPolicy = Literal["immediate", "confirm", "send"]
# 命令的 payload 类型
# static：命令没有参数，payload 就是命令本身（比如 /v 的 payload 就是 "v"）
# parameterized：命令包含参数，payload 是用户输入的完整文本（比如 /change_l2d_model#model_json 的 payload 就是 "change_l2d_model#model_json"）
# frontend_only：命令包含参数，但参数只在前端使用，发送到后端的 payload 是命令本身，不包含参数
CommandPayloadKind = Literal["static", "parameterized", "frontend_only"]


@dataclass(frozen=True)
class CommandSpec:
    """描述一个输入框命令的展示信息、匹配字段和执行策略。"""

    # 命令的内部名称（比如 v, s, conv）
    command: str
    # 命令的展示名称，该名称会被用于命令栏的匹配和展示，通常以斜杠+内部名称组成（比如 /v, /s, /conv）
    display_command: str
    # 命令的中文名称
    title: str
    # 命令的简介信息（中文）
    description: str
    # 命令的别名（通常都是英文的），会被用于命令栏的匹配
    aliases: tuple[str, ...]
    # 该命令是否可见
    visibility: CommandVisibility
    # 回车键选择该命令后，命令的执行方式
    execution_policy: CommandExecutionPolicy
    # 命令是否包含参数。例如，/change_l2d_model#model_json 就是一个带参数的命令，其 payload_kind 就是 "parameterized"，payload_prefix 就是 "change_l2d_model#"
    payload_kind: CommandPayloadKind = "static"
    # 如果 execution_policy 是 "confirm"，则 danger_text 会用于提示用户确认的输入框中
    danger_text: str = ""
    # 如果 payload_kind 是 "parameterized"，则 payload_prefix 会用于命令匹配时的前缀判断
    payload_prefix: str = ""


@dataclass(frozen=True)
class CommandMatch:
    """保存一次命令匹配的结果。"""
    # 匹配到的命令
    spec: CommandSpec
    # 命令的参数（若该命令接受参数）
    payload: str
    # 该命令是 / 开头的 slash 命令，还是裸命令
    is_slash_command: bool


def build_default_input_command_specs() -> tuple[CommandSpec, ...]:
    """创建默认的输入框命令。"""
    return (
        CommandSpec(
            command="s",
            display_command="/s",
            title="切换角色",
            aliases=("switch",),
            description="切换到下一个角色",
            visibility="public",
            execution_policy="send",
        ),
        CommandSpec(
            command="l",
            display_command="/l",
            title="切换语言",
            aliases=("language", "lang"),
            description="在中文输出与日文输出之间切换",
            visibility="public",
            execution_policy="immediate",
        ),
        CommandSpec(
            command="v",
            display_command="/v",
            title="语音合成",
            aliases=("voice",),
            description="开启或关闭 GPT-SoVITS 语音合成",
            visibility="public",
            execution_policy="immediate",
        ),
        CommandSpec(
            command="clr",
            display_command="/clr",
            title="清空对话",
            aliases=("clear",),
            description="清空当前角色聊天记录",
            visibility="public",
            execution_policy="confirm",
            danger_text="确定要清空当前角色的聊天记录吗？角色记忆也将同步被删除",
        ),
        CommandSpec(
            command="conv",
            display_command="/conv",
            title="祥子状态",
            aliases=("convert", "sakiko"),
            description="在黑祥与白祥状态之间切换",
            visibility="public",
            execution_policy="immediate",
        ),
        CommandSpec(
            command="mask",
            display_command="/mask",
            title="面具",
            aliases=("face",),
            description="切换祥子的面具状态",
            visibility="public",
            execution_policy="immediate",
        ),
        CommandSpec(
            command="save",
            display_command="/save",
            title="保存聊天记录",
            aliases=("write",),
            description="保存当前聊天记录",
            visibility="public",
            execution_policy="immediate",
            payload_kind="frontend_only",
        ),
        CommandSpec(
            command="bye",
            display_command="/bye",
            title="退出程序",
            aliases=("exit", "quit"),
            description="保存聊天记录并退出程序",
            visibility="public",
            execution_policy="confirm",
            danger_text="确定要保存聊天记录并退出程序吗？",
        ),
        CommandSpec(
            command="change_l2d_background",
            display_command="/change_l2d_background",
            title="切换 Live2D 背景",
            aliases=("bg", "background"),
            description="切换 Live2D 场景的背景图片",
            visibility="public",
            execution_policy="immediate",
        ),
        CommandSpec(
            command="change_l2d_model",
            display_command="/change_l2d_model",
            title="更改 Live2D 模型",
            aliases=("model", "l2d"),
            description="选择并切换到角色的某个 Live2D 模型",
            visibility="public",
            execution_policy="immediate",
            payload_kind="frontend_only",
        ),
        CommandSpec(
            command="start_talking",
            display_command="start_talking",
            title="开始录音动作",
            aliases=(),
            description="内部录音开始事件",
            visibility="hidden",
            execution_policy="send",
        ),
        CommandSpec(
            command="stop_talking",
            display_command="stop_talking",
            title="结束录音动作",
            aliases=(),
            description="内部录音结束事件",
            visibility="hidden",
            execution_policy="send",
        ),
        CommandSpec(
            command="change_l2d_model_payload",
            display_command="change_l2d_model#<model_json>",
            title="应用 Live2D 模型",
            aliases=(),
            description="内部 Live2D 模型切换事件",
            visibility="hidden",
            execution_policy="send",
            payload_kind="parameterized",
            payload_prefix="change_l2d_model#",
        ),
    )


class InputCommandMatcher:
    """负责输入框命令的匹配、过滤和排序。"""

    def __init__(self, specs: Sequence[CommandSpec]):
        """保存命令注册表并建立内部索引。"""
        self._specs = tuple(specs)
        self._public_specs = tuple(spec for spec in self._specs if spec.visibility == "public")
        self._exact_specs = {spec.command: spec for spec in self._specs if spec.payload_kind != "parameterized"}
        self._slash_specs: dict[str, CommandSpec] = {}
        for spec in self._public_specs:
            self._slash_specs[spec.command.lower()] = spec
            self._slash_specs[spec.display_command.lstrip("/").lower()] = spec
            self._slash_specs[spec.title.lower()] = spec
            for alias in spec.aliases:
                self._slash_specs[alias.lower()] = spec

    def public_specs(self) -> tuple[CommandSpec, ...]:
        """返回可显示在命令栏中的公开命令。"""
        return self._public_specs

    def find_by_text(self, text: str) -> CommandMatch | None:
        """根据输入文本查找命令，支持 slash 命令、裸命令和参数化内部 payload。"""
        stripped_text = text.strip()
        if not stripped_text:
            return None

        if stripped_text.startswith("/"):
            query = stripped_text[1:].strip().lower()
            spec = self._slash_specs.get(query)
            if spec is None:
                return None
            return CommandMatch(spec=spec, payload=spec.command, is_slash_command=True)

        for spec in self._specs:
            if spec.payload_kind == "parameterized" and spec.payload_prefix:
                if stripped_text.startswith(spec.payload_prefix):
                    return CommandMatch(spec=spec, payload=stripped_text, is_slash_command=False)

        spec = self._exact_specs.get(stripped_text)
        if spec is None:
            return None
        return CommandMatch(spec=spec, payload=spec.command, is_slash_command=False)

    def filter(self, query: str) -> tuple[CommandSpec, ...]:
        """按 query 过滤公开命令并按匹配优先级排序。"""
        normalized_query = query.strip().lstrip("/").lower()
        if not normalized_query:
            return self._public_specs

        ranked_specs: list[tuple[tuple[int, int, str], CommandSpec]] = []
        for spec in self._public_specs:
            rank = self.rank(spec, normalized_query)
            if rank is not None:
                ranked_specs.append((rank, spec))
        ranked_specs.sort(key=lambda one: one[0])
        return tuple(spec for _, spec in ranked_specs)

    def rank(self, spec: CommandSpec, query: str) -> tuple[int, int, str] | None:
        """
        计算命令和 query 的匹配优先级，数值越小越靠前。
        排序的优先级计算方式是匹配前缀，各个字段的优先级如下
        1. 命令本身（比如 "v"）
        2. 展示命令（比如 "/v"）
        3. 别名（比如 "voice"）
        4. 标题（比如 "语音合成"）
        5. 描述信息（比如 "开启或关闭 GPT-SoVITS 语音合成"）
        """
        prefix_fields = (
            (0, spec.command.lower()),
            (0, spec.display_command.lstrip("/").lower()),
            *[(1, alias.lower()) for alias in spec.aliases],
            (2, spec.title.lower()),
            (3, spec.description.lower()),
        )
        for priority, value in prefix_fields:
            if value.startswith(query):
                return (priority, len(value), spec.command)

        if query.isascii():
            return None

        contains_fields = (
            (12, spec.title.lower()),
            (13, spec.description.lower()),
        )
        for priority, value in contains_fields:
            if query in value:
                return (priority, len(value), spec.command)
        return None

    def is_hidden_payload(self, text: str) -> bool:
        """判断一段文本是否是需要隐藏显示的内部命令。"""
        match = self.find_by_text(text)
        return match is not None and match.spec.visibility == "hidden"


class InputCommandRow(QFrame):
    """命令栏中的单行命令控件。"""

    def __init__(self, spec: CommandSpec, parent: QWidget | None = None):
        """创建一行命令展示控件。"""
        super().__init__(parent)
        self.spec = spec
        self._selected = False
        self._theme_color = "#7799CC"
        self.setObjectName("inputCommandRow")
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout()
        layout.setContentsMargins(9, 3, 9, 3)
        layout.setSpacing(8)

        self.icon_label = QLabel("◇")
        self.icon_label.setFixedWidth(18)
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.command_label = QLabel(spec.display_command)
        self.command_label.setFixedWidth(185)

        self.title_label = QLabel(spec.title)
        self.title_label.setFixedWidth(108)

        self.description_label = QLabel(spec.description)
        self.description_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        layout.addWidget(self.icon_label, 0)
        layout.addWidget(self.command_label, 0)
        layout.addWidget(self.title_label, 0)
        layout.addWidget(self.description_label, 1)
        self.setLayout(layout)
        self.set_selected(False)

    def set_theme_color(self, color: str) -> None:
        """更新该行使用的主题色。"""
        self._theme_color = color
        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        """设置该行是否处于键盘选中状态。"""
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        """刷新该行的样式。"""
        selected_bg = "#F1F2F4"
        hover_bg = "#F6F7F8"
        background = selected_bg if self._selected else "transparent"
        self.setStyleSheet(f"""
            QFrame#inputCommandRow {{
                background-color: {background};
                border-radius: 9px;
            }}
            QFrame#inputCommandRow:hover {{
                background-color: {hover_bg};
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        self.icon_label.setStyleSheet("color: #5F6368; font-size: 14px;")
        self.command_label.setStyleSheet(
            f"color: {self._theme_color}; font-family: Menlo, Consolas, monospace; font-size: 13px;"
        )
        self.title_label.setStyleSheet("color: #202124; font-weight: 600; font-size: 13px;")
        self.description_label.setStyleSheet("color: #8B8F94; font-size: 13px;")


class InputCommandPalette(QFrame):
    """显示 slash 命令候选项的 Codex 风格浮层控件。"""

    commandSelected = pyqtSignal(object)

    def __init__(self, matcher: InputCommandMatcher, parent: QWidget | None = None):
        """创建命令栏控件并初始化内部状态。"""
        super().__init__(parent)
        self._matcher = matcher
        self._line_edit: QLineEdit | None = None
        self._anchor_widget: QWidget | None = None
        self._filtered_specs: tuple[CommandSpec, ...] = ()
        self._rows: list[InputCommandRow] = []
        self._selected_index = 0
        self._theme_color = "#7799CC"

        self.setObjectName("inputCommandPalette")
        self.setWindowFlags(Qt.Widget)
        self.hide()

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setObjectName("inputCommandPaletteScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(6, 6, 6, 6)
        self._content_layout.setSpacing(2)
        self._content_widget.setLayout(self._content_layout)
        self._scroll_area.setWidget(self._content_widget)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll_area)
        self.setLayout(layout)
        self.set_theme_color(self._theme_color)

    def attach_to_input(self, line_edit: QLineEdit, anchor_widget: QWidget) -> None:
        """绑定输入框和定位锚点，并安装事件监听。"""
        self._line_edit = line_edit
        self._anchor_widget = anchor_widget
        line_edit.installEventFilter(self)
        anchor_widget.installEventFilter(self)
        line_edit.textChanged.connect(self._on_input_text_changed)

    def set_theme_color(self, color: str) -> None:
        """刷新命令栏主题色。"""
        color_obj = QColor(color)
        if not color_obj.isValid():
            color = "#7799CC"
        self._theme_color = color
        self.setStyleSheet(f"""
            QFrame#inputCommandPalette {{
                background-color: #FFFFFF;
                border: 1px solid rgba(0, 0, 0, 0.10);
                border-radius: 13px;
            }}
            QScrollArea#inputCommandPaletteScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 6px 2px 6px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(0, 0, 0, 0.14);
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        for row in self._rows:
            row.set_theme_color(self._theme_color)

    def show_for_query(self, query: str) -> None:
        """根据 query 过滤命令并显示命令栏。"""
        self._filtered_specs = self._matcher.filter(query)
        self._selected_index = 0
        self._refresh_rows()
        if not self._filtered_specs:
            self.hide_palette()
            return
        self._show_palette()

    def hide_palette(self) -> None:
        """隐藏命令栏。"""
        self.hide()
        self._filtered_specs = ()
        self._selected_index = 0

    def handle_input_key_event(self, event: QEvent) -> bool:
        """处理输入框传来的按键事件，命中时返回 True。"""
        if not self.isVisible():
            return False
        if event.type() != QEvent.KeyPress:
            return False
        if not isinstance(event, QKeyEvent):
            return False
        key = event.key()
        if key == Qt.Key_Up:
            self._move_selection(-1)
            return True
        if key == Qt.Key_Down:
            self._move_selection(1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._confirm_selection()
            return True
        if key == Qt.Key_Escape:
            self.hide_palette()
            return True
        return False

    def eventFilter(self, a0: QObject, a1: QEvent) -> bool:
        """
        拦截输入框键盘、焦点和窗口尺寸事件。
        
        :param a0: watched，即输入框或锚点组件
        :param a1: event，即键盘事件、焦点事件或窗口尺寸事件
        """
        if a0 is self._line_edit:
            if self.handle_input_key_event(a1):
                return True
            if a1.type() == QEvent.FocusOut:
                QTimer.singleShot(0, self._hide_if_focus_outside)
        if self.isVisible() and a1.type() in (QEvent.Resize, QEvent.Move):
            self._position_near_anchor()
        return super().eventFilter(a0, a1)

    def _on_input_text_changed(self, text: str) -> None:
        """根据输入框内容显示、刷新或隐藏命令栏。"""
        if self._should_show_palette(text):
            self.show_for_query(text.strip()[1:])
        else:
            self.hide_palette()

    def _hide_if_focus_outside(self) -> None:
        """当焦点离开输入框和命令栏时隐藏命令栏。"""
        focus_widget = QApplication.focusWidget()
        if focus_widget is None:
            self.hide_palette()
            return
        if focus_widget is self._line_edit or self.isAncestorOf(focus_widget):
            return
        self.hide_palette()

    def _should_show_palette(self, text: str) -> bool:
        """判断当前输入是否应该触发命令栏。"""
        stripped_text = text.strip()
        return stripped_text.startswith("/")

    def _show_palette(self) -> None:
        """定位并显示命令栏。"""
        self._position_near_anchor()
        self.raise_()
        self.show()

    def _position_near_anchor(self) -> None:
        """将命令栏定位到输入面板上方。"""
        if self._anchor_widget is None or self.parentWidget() is None:
            return
        parent = self.parentWidget()
        anchor_top_left = self._anchor_widget.mapTo(parent, QPoint(0, 0))
        width = self._anchor_widget.width()
        row_count = min(max(len(self._filtered_specs), 1), 8)
        height = row_count * 40 + 14
        self.setFixedWidth(width)
        self.setFixedHeight(height)
        self.move(anchor_top_left.x(), max(0, anchor_top_left.y() - height - 8))

    def _refresh_rows(self) -> None:
        """根据当前过滤结果重建行控件。"""
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []

        for index, spec in enumerate(self._filtered_specs):
            row = InputCommandRow(spec, self._content_widget)
            row.set_theme_color(self._theme_color)
            row.set_selected(index == self._selected_index)
            row.installEventFilter(self)
            row.mousePressEvent = lambda event, row_index=index: self._handle_row_clicked(row_index)
            row.enterEvent = lambda event, row_index=index: self._handle_row_hovered(row_index)
            self._rows.append(row)
            self._content_layout.addWidget(row)
        self._content_layout.addStretch(1)

    def _move_selection(self, delta: int) -> None:
        """移动当前选中行。"""
        if not self._filtered_specs:
            return
        self._selected_index = (self._selected_index + delta) % len(self._filtered_specs)
        self._sync_row_selection()
        self._scroll_to_selected_row()

    def _sync_row_selection(self) -> None:
        """同步所有行的选中状态。"""
        for index, row in enumerate(self._rows):
            row.set_selected(index == self._selected_index)

    def _scroll_to_selected_row(self) -> None:
        """将当前选中行滚动到可见区域内。"""
        if not self._rows:
            return
        selected_row = self._rows[self._selected_index]
        self._scroll_area.ensureWidgetVisible(selected_row, 0, 6)

    def _confirm_selection(self) -> None:
        """确认当前选中命令并发出信号。"""
        if not self._filtered_specs:
            return
        spec = self._filtered_specs[self._selected_index]
        self.hide_palette()
        self.commandSelected.emit(spec)

    def _handle_row_clicked(self, row_index: int) -> None:
        """处理鼠标点击某行的动作。"""
        self._selected_index = row_index
        self._confirm_selection()

    def _handle_row_hovered(self, row_index: int) -> None:
        """处理鼠标悬停某行的动作。"""
        self._selected_index = row_index
        self._sync_row_selection()
