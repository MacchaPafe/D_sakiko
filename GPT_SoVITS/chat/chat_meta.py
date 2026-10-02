# 此文件存储了对话级别配置（meta）相关的定义类。
# 这些定义类最终都在 chat.py 中被 Chat 类使用。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .tool_options import TOOL_NAMES, change_tool_selection, normalize_enabled_tools


VALID_REASONING_ENABLED = {"auto", "on", "off"}
VALID_REASONING_EFFORT = {"default", "minimal", "low", "medium", "high", "xhigh"}


def _as_mapping(data: object) -> Mapping[str, object]:
    """将外部输入安全转换为只读映射视图。"""
    if isinstance(data, Mapping):
        return data
    return {}


def _as_str(value: object, default: str = "") -> str:
    """将外部输入安全转换为字符串。"""
    if isinstance(value, str):
        return value
    return default


def _as_int(value: object, default: int = 0) -> int:
    """将外部输入安全转换为整数，避免 bool 被当作整数。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _as_optional_float(value: object) -> float | None:
    """将外部输入安全转换为可选浮点数。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_optional_bool(value: object) -> bool | None:
    """将外部输入安全转换为可选布尔值。"""
    if isinstance(value, bool):
        return value
    return None


def _as_bool(value: object, default: bool = False) -> bool:
    """将外部输入安全转换为布尔值。"""
    if isinstance(value, bool):
        return value
    return default


@dataclass
class TheaterCharacterMeta:
    """小剧场中单个角色的补充设定。"""
    # 角色的说话风格
    talk_style: str = ""
    # 角色之间的互动细节，例如：“爱音称呼素世为 そよりん”
    interaction_details: str = ""

    @classmethod
    def from_dict(cls, data: object) -> "TheaterCharacterMeta":
        """从字典数据创建单个小剧场角色设定。"""
        mapping = _as_mapping(data)
        return cls(
            talk_style=_as_str(mapping.get("talk_style")),
            interaction_details=_as_str(mapping.get("interaction_details")),
        )

    def to_dict(self) -> dict[str, object]:
        """将单个小剧场角色设定转换为可序列化字典。"""
        return {
            "talk_style": self.talk_style,
            "interaction_details": self.interaction_details,
        }


@dataclass
class TheaterMeta:
    """小剧场模式下的对话级配置。"""

    # 小剧场第一个角色的信息。第一个角色是指渲染时站在左边的角色
    character_0: TheaterCharacterMeta = field(default_factory=TheaterCharacterMeta)
    # 小剧场第二个角色的信息。第二个角色是指渲染时站在右边的角色
    character_1: TheaterCharacterMeta = field(default_factory=TheaterCharacterMeta)
    # 当前小剧场的情景，即背景信息，比如：“放学后，祥子遇到了堵在羽丘门口的素世”
    situation: str = ""

    @classmethod
    def from_dict(cls, data: object) -> "TheaterMeta":
        """从字典数据创建小剧场配置。"""
        mapping = _as_mapping(data)
        return cls(
            character_0=TheaterCharacterMeta.from_dict(mapping.get("character_0")),
            character_1=TheaterCharacterMeta.from_dict(mapping.get("character_1")),
            situation=_as_str(mapping.get("situation")),
        )

    def to_dict(self) -> dict[str, object]:
        """将小剧场配置转换为可序列化字典。"""
        return {
            "character_0": self.character_0.to_dict(),
            "character_1": self.character_1.to_dict(),
            "situation": self.situation,
        }


@dataclass
class ToolCallRecordMeta:
    """单条工具调用 UI 展示记录。"""

    tool_call_id: str = ""
    tool_name: str = "unknown"
    arguments: dict[str, object] = field(default_factory=dict)
    message_index: int = -1
    status: str = "running"
    result_content: str = ""
    raw_result_content: str = ""
    duration_sec: float | None = None
    started_at: int = 0
    updated_at: int = 0
    ok: bool | None = None

    @classmethod
    def from_dict(cls, data: object) -> "ToolCallRecordMeta":
        """从字典数据创建单条工具调用展示记录。"""
        mapping = _as_mapping(data)
        return cls(
            tool_call_id=_as_str(mapping.get("tool_call_id")),
            tool_name=_as_str(mapping.get("tool_name"), "unknown"),
            arguments=dict(_as_mapping(mapping.get("arguments"))),
            message_index=_as_int(mapping.get("message_index"), -1),
            status=_as_str(mapping.get("status"), "running"),
            result_content=_as_str(mapping.get("result_content")),
            raw_result_content=_as_str(mapping.get("raw_result_content")),
            duration_sec=_as_optional_float(mapping.get("duration_sec")),
            started_at=_as_int(mapping.get("started_at")),
            updated_at=_as_int(mapping.get("updated_at")),
            ok=_as_optional_bool(mapping.get("ok")),
        )

    def to_dict(self) -> dict[str, object]:
        """将工具调用展示记录转换为可序列化字典。"""
        data: dict[str, object] = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "message_index": self.message_index,
            "status": self.status,
            "result_content": self.result_content,
            "raw_result_content": self.raw_result_content,
            "duration_sec": self.duration_sec,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
        if self.ok is not None:
            data["ok"] = self.ok
        return data


@dataclass
class ToolCallHistoryRecordMeta:
    """单条工具调用摘要轨迹。"""

    time: int = 0
    role: str = ""
    tool_rounds: int = 0
    tool_errors: int = 0

    @classmethod
    def from_dict(cls, data: object) -> "ToolCallHistoryRecordMeta":
        """从字典数据创建单条工具调用摘要轨迹。"""
        mapping = _as_mapping(data)
        return cls(
            time=_as_int(mapping.get("time")),
            role=_as_str(mapping.get("role")),
            tool_rounds=_as_int(mapping.get("tool_rounds")),
            tool_errors=_as_int(mapping.get("tool_errors")),
        )

    def to_dict(self) -> dict[str, object]:
        """将工具调用摘要轨迹转换为可序列化字典。"""
        return {
            "time": self.time,
            "role": self.role,
            "tool_rounds": self.tool_rounds,
            "tool_errors": self.tool_errors,
        }


@dataclass
class ReasoningMeta:
    """当前对话的大模型推理模式配置。"""

    # 是否启用推理功能；auto 会不传入任何数据，让模型自己决定是否启用；on 会强制启用；off 会强制关闭
    enabled: str = "auto"
    # 推理模式的努力程度，默认 default；其他可选值包括 minimal、low、medium、high、xhigh，努力程度越高，模型越倾向于进行复杂的推理
    effort: str = "default"

    @classmethod
    def from_dict(cls, data: object) -> "ReasoningMeta":
        """从字典数据创建推理模式配置，并归一化非法值。"""
        mapping = _as_mapping(data)
        enabled = _as_str(mapping.get("enabled"), "auto")
        effort = _as_str(mapping.get("effort"), "default")
        if enabled not in VALID_REASONING_ENABLED:
            enabled = "auto"
        if effort not in VALID_REASONING_EFFORT:
            effort = "default"
        return cls(enabled=enabled, effort=effort)

    def to_dict(self) -> dict[str, object]:
        """将推理模式配置转换为可序列化字典。"""
        return {
            "enabled": self.enabled,
            "effort": self.effort,
        }


@dataclass
class WorldbookChatSettings:
    """保存单个对话选择的世界书开关、根包和剧情集数。"""

    enabled: bool = False
    root_package_id: str = ""
    episode: int | None = None

    @classmethod
    def from_dict(cls, data: object) -> "WorldbookChatSettings":
        """从旧存档兼容地读取世界书对话设置。"""

        mapping = _as_mapping(data)
        raw_episode = mapping.get("episode")
        episode = _as_int(raw_episode) if raw_episode is not None else None
        if episode is not None and not 1 <= episode <= 13:
            episode = None
        return cls(
            enabled=_as_bool(mapping.get("enabled"), False),
            root_package_id=_as_str(mapping.get("root_package_id")),
            episode=episode,
        )

    def to_dict(self) -> dict[str, object]:
        """把世界书对话设置转换成稳定 JSON 字典。"""

        data: dict[str, object] = {"enabled": self.enabled}
        if self.root_package_id:
            data["root_package_id"] = self.root_package_id
        if self.episode is not None:
            data["episode"] = self.episode
        return data


@dataclass
class ChatMeta:
    """
    对话级别的元数据与配置。
    这些数据并不会在所有对话中存在，可能是空的。例如，在主程序对话中，theater 字段就是空的（初始内容）。
    """

    # 小剧场相关的存储数据。
    theater: TheaterMeta = field(default_factory=TheaterMeta)
    # Live2D 模型相关的存储数据，key: 角色名称 value: live2d 模型的 json 描述文件的相对路径
    live2d_models: dict[str, str] = field(default_factory=dict)
    character_forms: dict[str, str] = field(default_factory=dict)
    live2d_form_models: dict[str, dict[str, str]] = field(default_factory=dict)
    # 工具调用记录
    tool_call_records: list[ToolCallRecordMeta] = field(default_factory=list)
    # 工具调用历史
    tool_call_history: list[ToolCallHistoryRecordMeta] = field(default_factory=list)
    # 模型推理强度设置
    llm_reasoning: ReasoningMeta = field(default_factory=ReasoningMeta)
    # 当前对话是否允许模型调用工具。
    tool_calling_enabled: bool = True
    # 普通工具的关闭名单；旧存档缺失时由原开关决定全开或全关。
    disabled_tools: list[str] = field(default_factory=list)
    # 当前对话的世界书开关、根包与剧情进度。
    worldbook: WorldbookChatSettings = field(default_factory=WorldbookChatSettings)
    # 用来存放一些无法分类的字段，避免旧程序载入新版本程序存档时丢失数据。
    # （不过有一说一，真的会有人更新版本后又用旧版本程序打开新版本对话记录吗？有点诡异了）
    extra: dict[str, object] = field(default_factory=dict)

    def enabled_tool_names(self) -> frozenset[str]:
        """返回依赖有效的普通工具快照，供界面及本轮执行共同使用。"""

        if not self.tool_calling_enabled:
            return frozenset()
        return normalize_enabled_tools(TOOL_NAMES - frozenset(self.disabled_tools))

    def set_all_tools_enabled(self, enabled: bool) -> None:
        """批量全开或全关，清除之前的部分选择。"""

        self.disabled_tools = []
        self.tool_calling_enabled = enabled

    def set_tool_enabled(self, name: str, enabled: bool) -> frozenset[str]:
        """修改单项并同步依赖，返回所有发生变化的工具名称。"""

        previous = self.enabled_tool_names()
        selected = change_tool_selection(previous, name, enabled)
        self.disabled_tools = sorted(TOOL_NAMES - selected)
        self.tool_calling_enabled = bool(selected)
        return previous ^ selected

    @classmethod
    def from_dict(cls, data: object) -> "ChatMeta":
        """从字典数据创建对话元数据，并保留未知顶级字段。"""
        mapping = _as_mapping(data)
        live2d_models = {
            str(key): value
            for key, value in _as_mapping(mapping.get("live2d_models")).items()
            if isinstance(value, str)
        }
        character_forms = {
            str(key): value for key, value in _as_mapping(mapping.get("character_forms")).items()
            if value in ("black", "white")
        }
        live2d_form_models = {
            str(key): {form: path.strip() for form, path in _as_mapping(value).items()
                       if form in ("black", "white") and isinstance(path, str) and path.strip()}
            for key, value in _as_mapping(mapping.get("live2d_form_models")).items()
        }
        legacy_sakiko = live2d_models.pop("祥子", None)
        if legacy_sakiko and legacy_sakiko.strip():
            live2d_form_models.setdefault("sakiko", {}).setdefault("white", legacy_sakiko.strip())

        raw_tool_call_records = mapping.get("tool_call_records")
        tool_call_records = [
            ToolCallRecordMeta.from_dict(one)
            for one in (raw_tool_call_records if isinstance(raw_tool_call_records, list) else [])
        ]

        raw_tool_call_history = mapping.get("tool_call_history")
        tool_call_history = [
            ToolCallHistoryRecordMeta.from_dict(one)
            for one in (raw_tool_call_history if isinstance(raw_tool_call_history, list) else [])
        ]

        known_keys = {
            "theater",
            "live2d_models",
            "character_forms",
            "live2d_form_models",
            "tool_call_records",
            "tool_call_history",
            "llm_reasoning",
            "tool_calling_enabled",
            "disabled_tools",
            "worldbook",
        }
        # 保留所有未知的字段到 extra 中，确保数据不丢失
        extra = {str(key): value for key, value in mapping.items() if key not in known_keys}

        raw_disabled_tools = mapping.get("disabled_tools")
        result = cls(
            theater=TheaterMeta.from_dict(mapping.get("theater")),
            live2d_models=live2d_models,
            character_forms=character_forms,
            live2d_form_models=live2d_form_models,
            tool_call_records=tool_call_records,
            tool_call_history=tool_call_history,
            llm_reasoning=ReasoningMeta.from_dict(mapping.get("llm_reasoning")),
            tool_calling_enabled=_as_bool(mapping.get("tool_calling_enabled"), True),
            disabled_tools=[
                name for name in (raw_disabled_tools if isinstance(raw_disabled_tools, list) else [])
                if isinstance(name, str) and name in TOOL_NAMES
            ],
            worldbook=WorldbookChatSettings.from_dict(mapping.get("worldbook")),
            extra=extra,
        )
        selected = result.enabled_tool_names()
        result.tool_calling_enabled = bool(selected)
        result.disabled_tools = sorted(TOOL_NAMES - selected) if selected else []
        return result

    def to_dict(self) -> dict[str, object]:
        """将对话元数据转换为可序列化字典。"""
        data = dict(self.extra)
        if self.theater != TheaterMeta():
            data["theater"] = self.theater.to_dict()
        if self.live2d_models:
            data["live2d_models"] = dict(self.live2d_models)
        if self.character_forms:
            data["character_forms"] = dict(self.character_forms)
        if self.live2d_form_models:
            data["live2d_form_models"] = {key: dict(value) for key, value in self.live2d_form_models.items()}
        if self.tool_call_records:
            data["tool_call_records"] = [one.to_dict() for one in self.tool_call_records]
        if self.tool_call_history:
            data["tool_call_history"] = [one.to_dict() for one in self.tool_call_history]
        if self.llm_reasoning != ReasoningMeta():
            data["llm_reasoning"] = self.llm_reasoning.to_dict()
        selected = self.enabled_tool_names()
        if not selected:
            data["tool_calling_enabled"] = False
        elif selected != TOOL_NAMES:
            data["disabled_tools"] = sorted(TOOL_NAMES - selected)
        if self.worldbook != WorldbookChatSettings():
            data["worldbook"] = self.worldbook.to_dict()
        return data
