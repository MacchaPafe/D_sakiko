from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


CHARACTER_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
CHARACTER_AVATAR_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp"})
CHARACTER_CREATED_AT_FILE = ".created_at_ns"
VOICE_MODEL_README = """此目录用于放置该角色的 GPT-SoVITS 语音模型。

只有同时配置有效的 .ckpt、.pth、参考音频、参考文本和语言后，语音合成才会启用。
目录为空不会影响纯文本角色对话。
"""


class CharacterCreationError(ValueError):
    """表示角色资源创建请求不合法或无法安全完成。"""


@dataclass(frozen=True)
class CharacterDiskRecord:
    """描述一个已经完整写入磁盘的角色。"""

    character_folder_name: str
    character_name: str
    character_description: str
    icon_path: str | None
    created_at_ns: int


def validate_character_id(character_folder_name: str) -> str:
    """校验并返回规范化后的角色 ID。"""
    normalized_id = character_folder_name.strip()
    if not CHARACTER_ID_PATTERN.fullmatch(normalized_id):
        raise CharacterCreationError("角色 ID 只能包含小写英文字母、数字、下划线和连字符。")
    return normalized_id


def safe_character_id_from_name(character_name: str) -> str:
    """仅在显示名称本身可安全作为角色 ID 时返回建议值。"""
    candidate = character_name.strip().lower().replace(" ", "_")
    return candidate if CHARACTER_ID_PATTERN.fullmatch(candidate) else ""


def create_character_resources(
        character_name: str,
        character_folder_name: str,
        character_description: str,
        avatar_source_path: str | None = None,
        live2d_related_dir: str = "../live2d_related",
        reference_audio_dir: str = "../reference_audio",
) -> CharacterDiskRecord:
    """以临时目录加原子改名的方式创建角色及空语音目录结构。"""
    normalized_name = character_name.strip()
    normalized_id = validate_character_id(character_folder_name)
    normalized_description = character_description.strip()
    if not normalized_name:
        raise CharacterCreationError("角色名称不能为空。")
    if not normalized_description:
        raise CharacterCreationError("角色描述不能为空，且必须由用户填写。")

    live2d_root = Path(live2d_related_dir).expanduser().resolve(strict=False)
    voice_root = Path(reference_audio_dir).expanduser().resolve(strict=False)
    live2d_root.mkdir(parents=True, exist_ok=True)
    voice_root.mkdir(parents=True, exist_ok=True)
    _ensure_character_identity_is_unique(live2d_root, normalized_id, normalized_name)

    target_character_dir = live2d_root / normalized_id
    target_voice_dir = voice_root / normalized_id
    if target_voice_dir.exists():
        raise CharacterCreationError(f"角色语音目录已存在：{normalized_id}")
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{normalized_id}.", dir=live2d_root))
    character_dir_committed = False
    voice_dir_created = False
    try:
        (temporary_dir / "name.txt").write_text(normalized_name, encoding="utf-8")
        (temporary_dir / "character_description.txt").write_text(
            normalized_description,
            encoding="utf-8",
        )
        created_at_ns = time.time_ns()
        (temporary_dir / CHARACTER_CREATED_AT_FILE).write_text(
            str(created_at_ns),
            encoding="utf-8",
        )
        icon_path = _copy_optional_avatar(avatar_source_path, temporary_dir)
        os.replace(temporary_dir, target_character_dir)
        character_dir_committed = True

        voice_model_dir = target_voice_dir / "GPT-SoVITS_models"
        voice_model_dir.mkdir(parents=True, exist_ok=False)
        voice_dir_created = True
        (voice_model_dir / "README.txt").write_text(VOICE_MODEL_README, encoding="utf-8")
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        if character_dir_committed:
            shutil.rmtree(target_character_dir, ignore_errors=True)
        if voice_dir_created:
            shutil.rmtree(target_voice_dir, ignore_errors=True)
        raise

    created_icon_path = (
        str(target_character_dir / Path(icon_path).name)
        if icon_path is not None
        else None
    )
    return CharacterDiskRecord(
        character_folder_name=normalized_id,
        character_name=normalized_name,
        character_description=normalized_description,
        icon_path=created_icon_path,
        created_at_ns=created_at_ns,
    )


def discover_complete_character_records(
        live2d_related_dir: str = "../live2d_related",
) -> list[CharacterDiskRecord]:
    """读取磁盘上具备非空名称和描述的全部角色记录。"""
    root = Path(live2d_related_dir).expanduser().resolve(strict=False)
    if not root.is_dir():
        return []

    records: list[CharacterDiskRecord] = []
    for character_dir in root.iterdir():
        if not character_dir.is_dir() or character_dir.name.startswith("."):
            continue
        record = _read_complete_character_record(character_dir)
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: (record.created_at_ns, record.character_folder_name))
    return records


def _ensure_character_identity_is_unique(
        live2d_root: Path,
        character_folder_name: str,
        character_name: str,
) -> None:
    """确认角色 ID 和显示名称在磁盘角色目录中都没有重复。"""
    if (live2d_root / character_folder_name).exists():
        raise CharacterCreationError(f"角色 ID 已存在：{character_folder_name}")

    for character_dir in live2d_root.iterdir():
        if not character_dir.is_dir():
            continue
        name_path = character_dir / "name.txt"
        try:
            existing_name = name_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if existing_name == character_name:
            raise CharacterCreationError(f"角色名称已存在：{character_name}")


def _copy_optional_avatar(avatar_source_path: str | None, target_dir: Path) -> Path | None:
    """把可选头像复制到临时角色目录并返回临时目标路径。"""
    if not avatar_source_path:
        return None
    source_path = Path(avatar_source_path).expanduser().resolve(strict=True)
    suffix = source_path.suffix.lower()
    if suffix not in CHARACTER_AVATAR_SUFFIXES:
        raise CharacterCreationError("头像必须是 png、jpg、jpeg 或 bmp 文件。")
    target_path = target_dir / f"avatar{suffix}"
    shutil.copy2(source_path, target_path)
    return target_path


def _read_complete_character_record(character_dir: Path) -> CharacterDiskRecord | None:
    """从一个角色目录读取完整角色记录，不完整时返回空。"""
    try:
        character_name = (character_dir / "name.txt").read_text(encoding="utf-8").strip()
        character_description = (
            character_dir / "character_description.txt"
        ).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not character_name or not character_description:
        return None

    icon_candidates = sorted(
        (
            path
            for path in character_dir.iterdir()
            if path.is_file() and path.suffix.lower() in CHARACTER_AVATAR_SUFFIXES
        ),
        key=lambda path: path.stat().st_mtime_ns,
    )
    return CharacterDiskRecord(
        character_folder_name=character_dir.name,
        character_name=character_name,
        character_description=character_description,
        icon_path=str(icon_candidates[-1]) if icon_candidates else None,
        created_at_ns=read_character_creation_order(character_dir),
    )


def read_character_creation_order(character_dir: str | Path) -> int:
    """读取不可变角色创建序号；旧角色没有记录时回退目录时间。"""
    directory = Path(character_dir)
    try:
        raw_value = (directory / CHARACTER_CREATED_AT_FILE).read_text(
            encoding="utf-8",
        ).strip()
        return int(raw_value)
    except (OSError, ValueError):
        try:
            return directory.stat().st_mtime_ns
        except OSError:
            return 0
