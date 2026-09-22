"""提供 OCR 中间产物的加载、校验和原子写入。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import TypeVar

from pydantic import BaseModel

from .models import OCRObservationsArtifact, OCRReviewArtifact


ModelT = TypeVar("ModelT", bound=BaseModel)


def json_file_sha256(path: str | Path) -> str:
    """计算 JSON 文件的字节级 SHA-256，用于外部修改保护。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_model(model: BaseModel, path: str | Path) -> Path:
    """把 Pydantic 模型安全写入同目录临时文件后原子替换。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target


def load_observations(path: str | Path) -> OCRObservationsArtifact:
    """加载并验证 observations JSON。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return OCRObservationsArtifact.model_validate(payload)


def load_review(path: str | Path) -> OCRReviewArtifact:
    """加载并验证 review JSON。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return OCRReviewArtifact.model_validate(payload)
