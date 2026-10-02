"""对话级角色形态和模型覆盖；渲染器只消费已解析的模型目标。"""

from pathlib import Path
from typing import Optional

SAKIKO_FORMS = ("black", "white")
FORM_LABELS = {"black": "黑祥", "white": "白祥"}


def character_form(meta, character_folder: str) -> Optional[str]:
    if character_folder != "sakiko":
        return None
    value = getattr(meta, "character_forms", {}).get("sakiko", "black")
    return value if value in SAKIKO_FORMS else "black"


def explicit_model(meta, character_name: str, character_folder: str,
                   form: Optional[str] = None) -> Optional[str]:
    if character_folder == "sakiko":
        selected = form or character_form(meta, character_folder)
        value = getattr(meta, "live2d_form_models", {}).get("sakiko", {}).get(selected)
        # 兼容尚未经过 ChatMeta 反序列化的旧对象。
        if value is None and selected == "white":
            value = getattr(meta, "live2d_models", {}).get(character_name)
    else:
        value = getattr(meta, "live2d_models", {}).get(character_name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def set_model_override(meta, character_name: str, character_folder: str,
                       path: Optional[str], form: Optional[str] = None) -> None:
    if character_folder == "sakiko":
        selected = form or character_form(meta, character_folder)
        if selected not in SAKIKO_FORMS:
            raise ValueError("无效的祥子形态")
        models = meta.live2d_form_models.setdefault("sakiko", {})
        # 保存时迁移旧白祥选择，避免清除白祥覆盖后又读到旧项。
        legacy = meta.live2d_models.pop(character_name, None)
        if isinstance(legacy, str) and legacy.strip():
            models.setdefault("white", legacy.strip())
        if path:
            models[selected] = path.strip()
        else:
            models.pop(selected, None)
        if not models:
            meta.live2d_form_models.pop("sakiko", None)
    elif path:
        meta.live2d_models[character_name] = path.strip()
    else:
        meta.live2d_models.pop(character_name, None)


def default_form_model(form: str) -> Optional[str]:
    from .model_catalog import Live2DModelCatalog

    root = Path(__file__).resolve().parents[2]
    option = next((item for item in Live2DModelCatalog(root / "live2d_related", root)
                   .list_options("sakiko", form=form) if item.is_default), None)
    return str(option.model_json_path) if option else None
