from __future__ import annotations


DEEPSEEK_FLASH_MODEL = "deepseek-flash"
DEEPSEEK_MODEL_PRESETS = (DEEPSEEK_FLASH_MODEL, "deepseek-v4-pro")
DEEPSEEK_FLASH_COMPATIBLE_NAMES = (
    DEEPSEEK_FLASH_MODEL,
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
)
DEEPSEEK_DEPRECATED_MODEL_ALIASES = {
    "deepseek-chat": DEEPSEEK_FLASH_MODEL,
    "deepseek-reasoner": DEEPSEEK_FLASH_MODEL,
    "deepseek-v4-flash": DEEPSEEK_FLASH_MODEL,
    "deepseek-v4-flash-vision-exp": DEEPSEEK_FLASH_MODEL,
}


def normalize_official_deepseek_model(model: str) -> str:
    """将内置官方 DeepSeek 配置中的旧短模型名迁移到当前名称。"""
    normalized = model.strip()
    return DEEPSEEK_DEPRECATED_MODEL_ALIASES.get(normalized, normalized)
