from __future__ import annotations

import mimetypes
import json
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtGui import QImageReader

if TYPE_CHECKING:
    from chat.chat import MessageAttachment


SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
}

_IMAGE_FORMAT_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}

_MIME_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

_MODEL_ATTACHMENT_CAPABILITIES_PATH = Path(__file__).with_name("model_attachment_capabilities.json")
_DEFAULT_IMAGE_UPLOAD_BLOCKLIST = [
    "deepseek-v4-flash",
    "deepseek/deepseek-v4-flash",
    "openai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-flash",
    "openai/deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/DeepSeek-V4-Flash",
    "openai/deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-v4-pro",
    "deepseek/deepseek-v4-pro",
    "openai/deepseek-v4-pro",
    "deepseek-ai/deepseek-v4-pro",
    "openai/deepseek-ai/deepseek-v4-pro",
    "deepseek-ai/DeepSeek-V4-Pro",
    "openai/deepseek-ai/DeepSeek-V4-Pro",
]


def reference_audio_dir() -> Path:
    """返回 reference_audio 目录的绝对路径。"""
    return Path(__file__).resolve().parents[2] / "reference_audio"


def chat_attachment_dir(chat_id: str) -> Path:
    """返回指定对话的附件目录绝对路径。"""
    return reference_audio_dir() / "chat_attachments" / chat_id


def resolve_attachment_path(path: str) -> Path:
    """将附件存储路径解析为绝对路径。"""
    raw_path = Path(path)
    if raw_path.is_absolute():
        return raw_path
    return reference_audio_dir() / raw_path


def detect_image_mime_type(path: str | Path) -> str | None:
    """检测本地文件是否为支持的图片，并返回 MIME 类型。"""
    image_path = Path(path)
    if not image_path.exists() or not image_path.is_file():
        return None

    guessed_mime_type, _encoding = mimetypes.guess_type(str(image_path))
    reader = QImageReader(str(image_path))
    if not reader.canRead():
        return None

    if guessed_mime_type in SUPPORTED_IMAGE_MIME_TYPES:
        return guessed_mime_type

    image_format = bytes(reader.format()).decode("ascii", errors="ignore").lower()
    return _IMAGE_FORMAT_MIME_TYPES.get(image_format)


def is_supported_image_file(path: str | Path) -> bool:
    """判断本地文件是否为支持的图片。"""
    return detect_image_mime_type(path) is not None


def model_supports_image_upload(model: str, *, use_default_deepseek_api: bool = False) -> bool:
    """判断当前模型是否允许上传图片附件。"""
    if use_default_deepseek_api:
        return False
    if model_image_upload_is_blocked(model):
        return False

    if _litellm_supports_vision(model):
        return True
    return model_image_upload_is_force_allowed(model)


def model_can_force_allow_image_upload(model: str, *, use_default_deepseek_api: bool = False) -> bool:
    """判断当前模型是否允许用户手动加入图片上传白名单。"""
    if use_default_deepseek_api:
        return False
    return bool(model.strip()) and not model_image_upload_is_blocked(model)


def model_image_upload_is_blocked(model: str) -> bool:
    """判断模型是否在图片上传黑名单中。"""
    return _model_matches(model, _DEFAULT_IMAGE_UPLOAD_BLOCKLIST)


def model_image_upload_is_force_allowed(model: str) -> bool:
    """判断模型是否在图片上传强制允许白名单中。"""
    image_upload = _load_image_upload_capabilities()
    return _model_matches(model, _read_string_list(image_upload.get("force_allowlist")))


def add_model_image_upload_force_allow(model: str) -> None:
    """将模型加入图片上传强制允许白名单。"""
    normalized_model = model.strip()
    if not normalized_model:
        raise ValueError("模型名称为空，无法加入图片上传白名单。")
    if model_image_upload_is_blocked(normalized_model):
        raise ValueError(f"模型在图片上传黑名单中，不能强制允许：{normalized_model}")

    data = _load_model_attachment_capabilities()
    image_upload = data.get("image_upload")
    if not isinstance(image_upload, dict):
        image_upload = {}
        data["image_upload"] = image_upload

    allowlist = _read_string_list(image_upload.get("force_allowlist"))
    if not _model_matches(normalized_model, allowlist):
        allowlist.append(normalized_model)
    image_upload["force_allowlist"] = allowlist
    image_upload.pop("blocklist", None)
    _save_model_attachment_capabilities(data)


def import_image_attachment(chat_id: str, source_path: str) -> "MessageAttachment":
    """复制图片到对话附件目录，并返回消息附件对象。"""
    if not chat_id:
        raise ValueError("导入图片附件失败：缺少 chat_id。")

    source = Path(source_path)
    mime_type = detect_image_mime_type(source)
    if mime_type is None:
        raise ValueError(f"导入图片附件失败：文件不是支持的可读图片：{source_path}")

    target_dir = chat_attachment_dir(chat_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    extension = source.suffix.lower()
    if not extension:
        extension = _MIME_TYPE_EXTENSIONS.get(mime_type, "")
    target_name = f"{uuid.uuid4().hex}{extension}"
    target_path = target_dir / target_name
    shutil.copy2(source, target_path)

    from chat.chat import MessageAttachment

    return MessageAttachment(
        type="image",
        path=f"chat_attachments/{chat_id}/{target_name}",
        mime_type=mime_type,
        original_name=source.name,
    )


def delete_chat_attachment_dir(chat_id: str) -> None:
    """删除指定对话的附件目录。"""
    if not chat_id:
        return
    target_dir = chat_attachment_dir(chat_id)
    if target_dir.exists():
        shutil.rmtree(target_dir)


def _litellm_supports_vision(model: str) -> bool:
    """使用 LiteLLM 查询模型视觉能力，查询失败时按不支持处理。"""
    try:
        import litellm

        return bool(litellm.supports_vision(model))
    except Exception:
        return False


def _load_image_upload_capabilities() -> dict[str, object]:
    """读取图片上传能力配置段。"""
    data = _load_model_attachment_capabilities()
    image_upload = data.get("image_upload")
    if isinstance(image_upload, dict):
        return image_upload
    return {}


def _load_model_attachment_capabilities() -> dict[str, object]:
    """读取模型附件能力配置文件。"""
    if not _MODEL_ATTACHMENT_CAPABILITIES_PATH.exists():
        return _default_model_attachment_capabilities()
    try:
        with _MODEL_ATTACHMENT_CAPABILITIES_PATH.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)
    except (OSError, json.JSONDecodeError):
        return _default_model_attachment_capabilities()
    if isinstance(data, dict):
        return data
    return _default_model_attachment_capabilities()


def _save_model_attachment_capabilities(data: dict[str, object]) -> None:
    """保存模型附件能力配置文件。"""
    _MODEL_ATTACHMENT_CAPABILITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _MODEL_ATTACHMENT_CAPABILITIES_PATH.open("w", encoding="utf-8") as config_file:
        json.dump(data, config_file, ensure_ascii=False, indent=2)
        config_file.write("\n")


def _read_string_list(value: object) -> list[str]:
    """从未知 JSON 值中读取字符串列表。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _model_matches(model: str, candidates: Sequence[str]) -> bool:
    """按大小写不敏感的精确模型名匹配候选列表。"""
    normalized_model = _normalize_model_name(model)
    return any(_normalize_model_name(candidate) == normalized_model for candidate in candidates)


def _normalize_model_name(model: str) -> str:
    """规范化模型名称以便进行本地配置匹配。"""
    return model.strip().lower()


def _default_model_attachment_capabilities() -> dict[str, object]:
    """返回内置模型附件能力配置。"""
    return {
        "version": 1,
        "image_upload": {
            "force_allowlist": [],
        },
    }
