"""提供带依赖联动和三种汇总状态的普通工具菜单。"""

from __future__ import annotations

from functools import partial

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QAction, QMenu, QWidget

from chat.chat_meta import ChatMeta
from chat.tool_options import TOOL_NAMES, TOOL_OPTIONS
from ui_main.components.input_option_chips import SplitToggleChip


class ToolSelectionChip(SplitToggleChip):
    """将对话工具设置绑定到批量开关和可逐项勾选的菜单。"""

    settingsChanged = pyqtSignal(str)

    def __init__(self, *, height: int, parent: QWidget | None = None) -> None:
        """建立普通工具菜单，并标明前置依赖。"""

        super().__init__("工具", accessible_name="工具调用", height=height, parent=parent)
        self._meta: ChatMeta | None = None
        self.actions_by_name: dict[str, QAction] = {}
        menu = QMenu(self)
        labels = {option.name: option.label for option in TOOL_OPTIONS}
        for option in TOOL_OPTIONS:
            label = option.label
            if option.requires:
                label += "（依赖：" + "、".join(labels[name] for name in option.requires) + "）"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(partial(self._set_tool, option.name))
            self.actions_by_name[option.name] = action
        self.actions_by_name["set_reminder"].setToolTip("关闭后已创建的提醒仍会触发")
        menu.setToolTipsVisible(True)
        self.setMenu(menu)
        self.clicked.connect(self._toggle_all)

    def bind(self, meta: ChatMeta) -> None:
        """切换到当前对话配置，并同步所有菜单项与汇总状态。"""

        self._meta = meta
        self._refresh()

    def _refresh(self) -> None:
        """显示全开、部分开启或全关，部分开启时主区点击补齐全开。"""

        if self._meta is None:
            return
        selected = self._meta.enabled_tool_names()
        all_enabled = selected == TOOL_NAMES
        state = "全部开启" if all_enabled else "部分开启" if selected else "全部关闭"
        self.setChecked(all_enabled)
        self.setText("工具" if all_enabled else "工具 · 部分" if selected else "工具 · 关闭")
        next_action = "全部关闭" if all_enabled else "全部开启"
        description = (
            f"普通工具{state}（{len(selected)}/{len(TOOL_NAMES)}），点击{next_action}；"
            "右侧菜单可单独设置。"
        )
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        for name, action in self.actions_by_name.items():
            action.setChecked(name in selected)

    def _toggle_all(self, checked: bool) -> None:
        """根据当前实际选择执行全开或全关，不恢复旧的部分选择。"""

        if self._meta is None:
            return
        enabled = self._meta.enabled_tool_names() != TOOL_NAMES
        self._meta.set_all_tools_enabled(enabled)
        self._refresh()
        self.settingsChanged.emit("已全部开启普通工具" if enabled else "已全部关闭普通工具")

    def _set_tool(self, name: str, enabled: bool) -> None:
        """修改工具并发出包含依赖联动结果的保存提示。"""

        if self._meta is None:
            return
        changed = self._meta.set_tool_enabled(name, enabled)
        self._refresh()
        linked_labels = [
            option.label for option in TOOL_OPTIONS
            if option.name in changed and option.name != name
        ]
        message = "已更新工具调用设置"
        if linked_labels:
            message += "，已联动" + ("开启" if enabled else "关闭") + "：" + "、".join(linked_labels)
        self.settingsChanged.emit(message)
