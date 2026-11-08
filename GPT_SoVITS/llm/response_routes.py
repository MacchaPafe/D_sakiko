"""集中描述 Responses 路由差异，不把订阅限制传播给业务调用方。"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .errors import LLMError


@dataclass(frozen=True)
class ResponseRoute:
    """同一协议的路由契约；不支持的偏好与功能要求分别处理。"""

    name: str
    omitted_preferences: frozenset[str] = frozenset()
    forbidden_options: frozenset[str] = frozenset()
    namespace: str | None = None

    def prepare(self, body: dict[str, object]) -> tuple[dict[str, object], list[str]]:
        """返回独立请求副本及诊断，不改写调用方配置或隐藏功能降级。"""
        prepared = copy.deepcopy(body)
        diagnostics: list[str] = []
        for key in sorted(self.forbidden_options):
            if key in prepared:
                raise LLMError("unsupported_parameter", f"{self.name} 不支持请求要求 {key}。")
        for key in sorted(self.omitted_preferences):
            if key in prepared:
                prepared.pop(key)
                diagnostics.append(f"{self.name} 不支持 {key}，仅在本次请求中省略；原配置保留。")
        if self.namespace is not None:
            tools = prepared.get("tools", [])
            if not isinstance(tools, list):
                raise LLMError("unsupported_input", "工具声明必须是列表。")
            if any(not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools):
                raise LLMError("unsupported_tool", "ChatGPT 订阅当前仅适配本地执行的函数工具。")
            if tools:
                prepared["tools"] = [{"type": "namespace", "name": self.namespace,
                                      "description": "数字小祥在本机执行的工具。", "tools": tools}]
                choice = prepared.get("tool_choice")
                if isinstance(choice, dict) and choice.get("type") == "function":
                    function = choice.get("function", choice)
                    if isinstance(function, dict):
                        prepared["tool_choice"] = {"type": "function", "name": function.get("name"),
                                                   "namespace": self.namespace}
        prepared.update(store=False, stream=True)
        return prepared, diagnostics


STANDARD_RESPONSES = ResponseRoute("Responses API")
CHATGPT_SUBSCRIPTION = ResponseRoute(
    "ChatGPT 订阅",
    omitted_preferences=frozenset({"temperature", "top_p", "max_output_tokens"}),
    forbidden_options=frozenset({"background", "conversation", "previous_response_id", "max_tool_calls",
                                 "metadata", "moderation", "multi_agent", "prompt", "prompt_cache_retention",
                                 "safety_identifier", "top_logprobs", "truncation", "user"}),
    namespace="dsakiko",
)


def response_route(name: str) -> ResponseRoute:
    """依据明确路由身份选择契约，不从域名或令牌外形猜测计费路径。"""
    if name == "standard":
        return STANDARD_RESPONSES
    if name == "chatgpt-subscription":
        return CHATGPT_SUBSCRIPTION
    raise LLMError("unsupported_route", "未知 Responses 路由：" + name)


def validate_subscription_model(body: dict[str, object], model: dict[str, object]) -> None:
    """将路由要求与账户目录能力求交，未知能力不猜测为不支持。"""
    reasoning = body.get("reasoning")
    levels = model.get("supported_reasoning_levels")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None and isinstance(levels, list):
        supported = [item.get("effort") for item in levels if isinstance(item, dict)]
        if reasoning["effort"] not in supported:
            raise LLMError("unsupported_parameter", "当前账户的所选模型不支持该推理强度，请手动调整。")
    modalities = model.get("input_modalities")
    if isinstance(modalities, list) and "image" not in modalities:
        for item in body.get("input", []):
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") == "input_image" for part in content):
                raise LLMError("unsupported_input", "当前账户的所选模型不支持图片，请手动切换模型。")
