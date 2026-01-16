def ensure_openai_compatible_model(model: str) -> str:
    """Ensure the model string is routed through LiteLLM's OpenAI-compatible adapter.

    LiteLLM routes OpenAI-compatible endpoints via the "openai/" provider prefix.
    For OpenAI-compatible third-party endpoints (fixed base_url or custom base_url),
    we prepend "openai/" unless it's already present.

    Examples:
    - "deepseek-ai/DeepSeek-V3.2" -> "openai/deepseek-ai/DeepSeek-V3.2"
    - "openai/gpt-4o" -> "openai/gpt-4o"
    """
    model = (model or "").strip()
    if not model:
        return model
    return model if model.startswith("openai/") else f"openai/{model}"
