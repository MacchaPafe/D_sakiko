from __future__ import annotations

import mimetypes
import shutil
import uuid
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
