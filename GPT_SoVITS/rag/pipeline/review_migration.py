"""提供 Stage 3 审核摘要、迁移诊断和安全文件替换。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel

from .review_models import ReviewMigrationReport, SourceFingerprint


def canonical_json_sha256(value: object) -> str:
    """计算不受对象键顺序和 JSON 排版影响的 SHA-256。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    """流式计算文件 SHA-256。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_fingerprint(
    role: str,
    path: str | Path,
    episode: int | None = None,
) -> SourceFingerprint:
    """为一份直接来源建立不依赖文件位置的逻辑摘要。"""

    return SourceFingerprint(role=role, episode=episode, sha256=file_sha256(path))


def migration_report_path(output_path: str | Path) -> Path:
    """返回审核产物并排的固定迁移报告路径。"""

    return Path(f"{Path(output_path)}.migration-report.json")


def safely_write_json_model(model: BaseModel, output_path: str | Path) -> None:
    """校验后使用同目录临时文件和原子替换保存模型。"""

    target = Path(output_path)
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


def write_migration_report(report: ReviewMigrationReport, output_path: str | Path) -> Path:
    """覆盖写入本次审核迁移报告并返回实际路径。"""

    report_path = migration_report_path(output_path)
    safely_write_json_model(report, report_path)
    return report_path


def blocked_removed_ids(
    completed_publish_ids: set[str],
    disappeared_ids: set[str],
    allowed_ids: set[str],
    allow_all_removed: bool,
) -> list[str]:
    """找出本次重生成仍未获得一次性删除许可的已发布身份。"""

    protected = completed_publish_ids & disappeared_ids
    if allow_all_removed:
        return []
    return sorted(protected - allowed_ids)
