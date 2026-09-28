"""验证工具选择的持久化、依赖联动、界面交互和执行边界。"""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from chat.chat_meta import ChatMeta
from chat.tool_calling import (
    ToolCallingAgentRuntime,
    ToolRegistry,
    build_default_tool_registry,
    register_live2d_tools,
    register_lottery_tool,
    register_reminder_tool,
)
from chat.tool_options import TOOL_NAMES
from ui_main.components.tool_selection_chip import ToolSelectionChip
from ui_main.theme import derive_theme_palette


@pytest.mark.parametrize("name,dependency", [
    ("set_reminder", "get_current_datetime"),
    ("change_character_live2d", "fetch_all_live2d_models"),
])
def test_dependency_changes_and_persistence(name: str, dependency: str) -> None:
    """开启补齐前置、关闭前置联动关闭，关闭依赖工具则保留前置。"""

    meta = ChatMeta.from_dict({"tool_calling_enabled": False})
    assert meta.set_tool_enabled(name, True) == {name, dependency}
    assert meta.enabled_tool_names() == {name, dependency}
    meta = ChatMeta.from_dict(meta.to_dict())
    assert meta.enabled_tool_names() == {name, dependency}
    meta.set_tool_enabled(name, False)
    assert meta.enabled_tool_names() == {dependency}
    meta.set_tool_enabled(name, True)
    assert meta.set_tool_enabled(dependency, False) == {name, dependency}
    assert not ChatMeta.from_dict(meta.to_dict()).enabled_tool_names()


def test_legacy_bulk_and_invalid_dependency_config() -> None:
    """旧开关兼容，异常依赖安全归一化，全关后全开不恢复部分选择。"""

    assert ChatMeta.from_dict({}).enabled_tool_names() == TOOL_NAMES
    assert "tool_calling_enabled" not in ChatMeta().to_dict()
    assert not ChatMeta.from_dict({"tool_calling_enabled": False}).enabled_tool_names()
    meta = ChatMeta.from_dict({
        "disabled_tools": ["get_current_datetime", "unknown", None],
        "worldbook": {"enabled": True},
    })
    assert "set_reminder" not in meta.enabled_tool_names()
    assert "set_reminder" in meta.disabled_tools
    assert "disabled_tools" not in meta.extra
    assert meta.worldbook.enabled
    meta.set_all_tools_enabled(False)
    assert meta.worldbook.enabled
    meta.set_all_tools_enabled(True)
    assert meta.enabled_tool_names() == TOOL_NAMES
    assert "disabled_tools" not in meta.to_dict()


def test_menu_catalog_covers_all_registered_general_tools() -> None:
    """每个实际注册的普通工具都必须有独立菜单项，避免新增工具漏配。"""

    registry = build_default_tool_registry()
    register_live2d_tools(registry, lambda: "anon", lambda path: None)
    register_reminder_tool(registry, lambda content, timestamp: True)
    register_lottery_tool(registry, lambda title, options: True)
    assert {schema["function"]["name"] for schema in registry.build_tools_schema()} == TOOL_NAMES


@pytest.fixture
def chip() -> Iterator[ToolSelectionChip]:
    """提供不启动主程序或调用模型的真实 Qt 工具控件。"""

    application = QApplication.instance() or QApplication([])
    widget = ToolSelectionChip(height=28)
    widget.set_theme_palette(derive_theme_palette("#7799CC"))
    widget.bind(ChatMeta())
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()
    application.processEvents()


def test_menu_dependencies_bulk_keyboard_and_chat_switch(chip: ToolSelectionChip) -> None:
    """菜单勾选触发依赖提示，主区键盘操作批量切换，换聊天恢复独立设置。"""

    first = ChatMeta()
    chip.bind(first)
    notifications: list[str] = []
    chip.settingsChanged.connect(notifications.append)
    chip.actions_by_name["get_current_datetime"].trigger()
    assert chip.text() == "工具 · 部分"
    assert not chip.actions_by_name["set_reminder"].isChecked()
    assert "联动关闭：设置提醒" in notifications[-1]
    chip.actions_by_name["set_reminder"].trigger()
    assert chip.actions_by_name["get_current_datetime"].isChecked()
    assert "联动开启：获取时间" in notifications[-1]
    chip.actions_by_name["web_search"].trigger()
    QTest.keyClick(chip, Qt.Key_Space)
    assert first.enabled_tool_names() == TOOL_NAMES
    QTest.keyClick(chip, Qt.Key_Space)
    assert not first.enabled_tool_names()
    assert chip.text() == "工具 · 关闭"
    chip.actions_by_name["set_reminder"].trigger()
    second = ChatMeta()
    chip.bind(second)
    assert chip.text() == "工具"
    chip.bind(first)
    assert first.enabled_tool_names() == {"set_reminder", "get_current_datetime"}
    assert chip.text() == "工具 · 部分"
    assert not chip.actions_by_name["web_search"].isChecked()


def test_arrow_opens_menu_without_changing_selection(chip: ToolSelectionChip) -> None:
    """点击右侧箭头只展开菜单，不误触主区批量开关或保存信号。"""

    meta = ChatMeta()
    chip.bind(meta)
    opened: list[bool] = []
    changed: list[str] = []
    chip.menu().aboutToShow.connect(lambda: opened.append(True))
    chip.settingsChanged.connect(changed.append)
    QTimer.singleShot(0, chip.menu().close)
    QTest.mouseClick(chip, Qt.LeftButton, pos=QPoint(chip.width() - 10, chip.height() // 2))
    assert opened == [True]
    assert not changed
    assert meta.enabled_tool_names() == TOOL_NAMES


class TurnCompletion:
    """记录每次模型可见工具，并在首个请求中模拟用户修改设置。"""

    def __init__(self, meta: ChatMeta, requested: str) -> None:
        """绑定当前对话及模型将尝试调用的工具名称。"""

        self.meta = meta
        self.requested = requested
        self.schemas: list[object] = []
        self.histories: list[object] = []

    def __call__(self, **kwargs: object) -> dict[str, object]:
        """先输出工具调用，后输出最终回复，同时保存调用时的配置。"""

        self.schemas.append(kwargs.get("tools"))
        self.histories.append(kwargs.get("messages"))
        if len(self.schemas) == 1:
            self.meta.set_all_tools_enabled(False)
            message: dict[str, object] = {
                "content": "",
                "tool_calls": [{
                    "id": "call_test", "type": "function",
                    "function": {"name": self.requested, "arguments": "{}"},
                }],
            }
        else:
            message = {"content": "最终回答"}
        return {"choices": [{"message": message}]}


@pytest.mark.parametrize("requested", ["web_search", "get_weather", "hidden_worldbook"])
def test_runtime_snapshot_blocked_calls_and_worldbook(requested: str) -> None:
    """本轮选择不会中途改变，禁用工具无法执行，隐藏工具在全关后仍可使用。"""

    meta = ChatMeta(tool_calling_enabled=False)
    meta.set_tool_enabled("web_search", True)
    completion = TurnCompletion(meta, requested)
    executed: list[str] = []

    def web_handler(arguments: dict[str, object]) -> str:
        """记录已开启工具的实际执行。"""

        executed.append("web_search")
        return "搜索完成"

    def weather_handler(arguments: dict[str, object]) -> str:
        """记录不应发生的禁用工具执行。"""

        executed.append("get_weather")
        return "天气结果"

    def hidden_handler(arguments: dict[str, object]) -> str:
        """记录独立于普通开关的隐藏工具执行。"""

        executed.append("hidden_worldbook")
        return "世界书结果"

    registry = ToolRegistry()
    registry.register_tool("web_search", "搜索", {}, web_handler)
    registry.register_tool("get_weather", "天气", {}, weather_handler)
    overlay = ToolRegistry()
    overlay.register_tool("hidden_worldbook", "世界书", {}, hidden_handler, channel="worldbook-hidden")
    runtime = ToolCallingAgentRuntime(llm_completion=completion, tool_registry=registry)
    try:
        snapshot = meta.enabled_tool_names()
        runtime.run(model="test", messages=[], tool_overlay=overlay, enabled_general_tools=snapshot)
        assert executed == ([] if requested == "get_weather" else [requested])
        expected_schema = registry.with_enabled_general_tools(snapshot).combined(overlay).build_tools_schema()
        assert completion.schemas == [expected_schema, expected_schema]
        if requested == "get_weather":
            assert "tool_not_found" in str(completion.histories[-1])
        assert registry.has_tool("get_weather")
        assert not meta.enabled_tool_names()
        runtime.run(
            model="test", messages=[], tool_overlay=overlay,
            include_base_tools=False, enabled_general_tools=meta.enabled_tool_names(),
        )
        assert completion.schemas[-1] == overlay.build_tools_schema()
        runtime.run(model="test", messages=[], enabled_general_tools=frozenset())
        assert completion.schemas[-1] is None
    finally:
        runtime._executor.shutdown(wait=True)
