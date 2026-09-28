"""定义普通工具的用户选项和依赖关系，不包含世界书工具。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolOption:
    """保存工具的稳定名称、菜单标签与必须同时开启的前置工具。"""

    name: str
    label: str
    requires: tuple[str, ...] = ()


TOOL_OPTIONS = (
    ToolOption("get_current_datetime", "获取时间"),
    ToolOption("set_reminder", "设置提醒", ("get_current_datetime",)),
    ToolOption("get_system_hardware_status", "硬件状态"),
    ToolOption("web_search", "网络搜索"),
    ToolOption("get_weather", "获取天气"),
    ToolOption("fetch_all_live2d_models", "获取服装列表"),
    ToolOption("change_character_live2d", "切换服装", ("fetch_all_live2d_models",)),
    ToolOption("start_lottery", "随机抽签"),
    ToolOption("list_directory", "浏览目录"),
    ToolOption("read_file_content", "读取文件"),
    ToolOption("grep_search_in_file", "搜索文件内容"),
    ToolOption("export_document", "导出文档"),
)
TOOL_NAMES = frozenset(option.name for option in TOOL_OPTIONS)


def normalize_enabled_tools(names: frozenset[str]) -> frozenset[str]:
    """移除未知工具和缺少前置工具的选项，直至所有依赖均满足。"""

    enabled = names & TOOL_NAMES
    while True:
        normalized = frozenset(
            option.name
            for option in TOOL_OPTIONS
            if option.name in enabled and all(name in enabled for name in option.requires)
        )
        if normalized == enabled:
            return normalized
        enabled = normalized


def change_tool_selection(
    current: frozenset[str], name: str, enabled: bool,
) -> frozenset[str]:
    """开启时补齐前置工具，关闭时递归关闭依赖它的工具。"""

    if name not in TOOL_NAMES:
        raise ValueError(f"未知的普通工具：{name}")
    selected = set(current)
    if not enabled:
        selected.discard(name)
        return normalize_enabled_tools(frozenset(selected))

    pending = [name]
    options = {option.name: option for option in TOOL_OPTIONS}
    visited: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency in visited:
            continue
        visited.add(dependency)
        selected.add(dependency)
        pending.extend(options[dependency].requires)
    return normalize_enabled_tools(frozenset(selected))
