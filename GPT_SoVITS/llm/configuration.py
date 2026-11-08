"""把显式设置快照转换成连接，不读取全局 Qt 配置。"""
from __future__ import annotations

from .types import LLMConfig


def setting(settings: object, name: str, default: object = None) -> object:
    """读取调用方提供的设置值，兼容旧快照缺省字段。"""
    return getattr(getattr(settings, name, None), "value", default)


def resolve_config(settings: object, default_key: str = "", *, theater: bool = False) -> LLMConfig:
    """统一解析主对话和小剧场的模型连接，保持现有提供商优先级。"""
    from llm_model_utils import ensure_openai_compatible_model
    timeout = float(setting(settings, "theater_request_timeout_seconds" if theater
                            else "llm_request_timeout_seconds", 100 if theater else 30))
    if setting(settings, "use_default_deepseek_api", True):
        return LLMConfig("deepseek_up", "deepseek/deepseek-flash", default_key, timeout=timeout)
    if setting(settings, "enable_custom_llm_api_provider", False):
        protocol = str(setting(settings, "custom_llm_api_protocol", "chat-completions"))
        prefix = "custom_responses" if protocol == "responses" else "custom_llm_api"
        model = str(setting(settings, prefix + "_model", ""))
        if protocol != "responses":
            model = ensure_openai_compatible_model(model)
        return LLMConfig("custom", model, str(setting(settings, prefix + "_key", "")),
                         str(setting(settings, prefix + "_url", "")), protocol, timeout)
    provider = str(setting(settings, "llm_api_provider", "deepseek"))
    if provider == "chatgpt":
        return LLMConfig("chatgpt", str(setting(settings, "chatgpt_model", "")),
                         base_url="https://api.openai.com/v1", protocol="responses", timeout=timeout,
                         route="chatgpt-subscription", account_id=str(setting(settings, "chatgpt_account_id", "")))
    models = setting(settings, "llm_api_model", {})
    keys = setting(settings, "llm_api_key", {})
    bases = setting(settings, "llm_api_base_url", {})
    model = str(models.get(provider, "")) if isinstance(models, dict) else ""
    base = str(bases.get(provider, "") or "") if isinstance(bases, dict) else ""
    key = str(keys.get(provider, "")) if isinstance(keys, dict) else ""
    # 显式自定义地址由 OpenAI 兼容路由承接；内置供应商保留其原生 LiteLLM 路由。
    if base:
        model = ensure_openai_compatible_model(model)
    elif "/" not in model:
        model = provider + "/" + model
    return LLMConfig(provider, model, key, base, timeout=timeout)
