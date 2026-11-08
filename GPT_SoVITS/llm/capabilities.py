"""保守处理本地已知的参数能力，第三方模型别名保持未知。"""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit
from .types import LLMConfig, LLMRequest


def prepare_request(request: LLMRequest, config: LLMConfig) -> tuple[LLMRequest, list[str]]:
    """只省略可信能力表明确排除的采样偏好，不删除功能要求。"""
    options = dict(request.options)
    diagnostics: list[str] = []
    if config.route == "chatgpt-subscription":
        # 订阅路由有独立契约，不能用普通 API 的模型表推断其可用参数。
        return replace(request, options=options), diagnostics
    hostname = urlsplit(config.base_url).hostname if config.base_url else None
    if config.base_url and hostname not in {"api.openai.com", "api.deepseek.com"}:
        return replace(request, options=options), diagnostics
    try:
        import litellm
        model = config.model
        if config.protocol == "responses" and "/" not in model:
            model = "openai/" + model
        # 协议默认参数表不能证明一个未知模型的能力；先确认模型已被本地目录识别。
        litellm.get_model_info(model=model)
        supported = litellm.get_supported_openai_params(model=model)
    except Exception:
        supported = None
    if isinstance(supported, list):
        for key in ("temperature", "top_p"):
            if key in options and key not in supported:
                options.pop(key)
                diagnostics.append(f"本地已知模型能力不支持采样参数 {key}，本次省略。")
    return replace(request, options=options), diagnostics
