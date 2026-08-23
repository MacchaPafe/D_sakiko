from __future__ import annotations

import tempfile
import threading
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_IMAGE_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 4


@dataclass(frozen=True)
class PendingImageUpload:
    upload_id: str
    path: Path
    original_name: str
    mime_type: str
    size: int


class PendingImageStore:
    """保存浏览器草稿图片，直到消息发送时导入正式聊天附件。"""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="dsakiko-webui-images-")
        self._items: dict[str, PendingImageUpload] = {}
        self._lock = threading.Lock()

    def add(self, data: bytes, original_name: str) -> PendingImageUpload:
        suffix = Path(original_name).suffix.lower()[:10]
        upload_id = f"upload_{uuid.uuid4().hex}"
        path = Path(self._directory.name) / f"{upload_id}{suffix}"
        path.write_bytes(data)

        try:
            gpt_root = Path(__file__).resolve().parents[2] / "GPT_SoVITS"
            if str(gpt_root) not in sys.path:
                sys.path.insert(0, str(gpt_root))
            from chat.attachments import detect_image_mime_type

            mime_type = detect_image_mime_type(path)
            if mime_type is None:
                raise ValueError("文件不是支持的可读图片。")
        except Exception:
            path.unlink(missing_ok=True)
            raise

        item = PendingImageUpload(
            upload_id=upload_id,
            path=path,
            original_name=Path(original_name).name or "image",
            mime_type=mime_type,
            size=len(data),
        )
        with self._lock:
            self._items[upload_id] = item
        return item

    def resolve(self, upload_ids: list[str]) -> list[PendingImageUpload]:
        if len(upload_ids) > MAX_IMAGES_PER_MESSAGE:
            raise ValueError(f"每条消息最多添加 {MAX_IMAGES_PER_MESSAGE} 张图片。")
        if len(set(upload_ids)) != len(upload_ids):
            raise ValueError("图片编号重复。")
        with self._lock:
            items = [self._items.get(upload_id) for upload_id in upload_ids]
        if any(item is None for item in items):
            raise ValueError("部分图片已经失效，请重新选择。")
        return [item for item in items if item is not None]

    def discard(self, upload_ids: list[str]) -> None:
        with self._lock:
            items = [self._items.pop(upload_id, None) for upload_id in upload_ids]
        for item in items:
            if item is not None:
                item.path.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            self._items.clear()
        self._directory.cleanup()
