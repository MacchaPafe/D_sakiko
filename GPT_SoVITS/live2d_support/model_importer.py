from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence, cast

from live2d_support.model_normalizer import normalize_live2d_model_for_project

MODEL3_DIRECT_REFERENCE_KEYS = ("Moc", "Physics", "Pose", "DisplayInfo", "UserData")
MODEL2_DIRECT_REFERENCE_KEYS = ("model", "physics", "pose")
REFERENCE_EXTERNAL_ASSETS_DIR = "external_assets"


@dataclass(frozen=True)
class Live2DModelImportResult:
    """记录一次 Live2D 模型导入结果。"""

    model_name: str
    model_json_path: str
    target_dir: str


class Live2DModelImportError(Exception):
    """表示 Live2D 模型导入失败。"""


@dataclass
class _ReferenceCopyContext:
    """保存导入过程中用于去重和避免目标冲突的状态。"""

    source_model_dir: Path
    target_dir: Path
    copied_by_source: dict[str, str]
    used_targets: dict[str, str]


def import_live2d_model_to_extra_model(
        source_model_json_path: str,
        character_folder_name: str,
        live2d_related_dir: str = "../live2d_related",
) -> Live2DModelImportResult:
    """把本地 Live2D 模型复制到指定角色的 extra_model 目录并完成项目规范化。"""
    source_model_json = Path(source_model_json_path).expanduser().resolve(strict=True)
    if not _is_supported_model_json_name(source_model_json.name):
        raise Live2DModelImportError("请选择 .model.json 或 .model3.json 模型配置文件。")

    live2d_related_root = Path(live2d_related_dir).expanduser().resolve(strict=False)
    if _is_relative_to_path(source_model_json, live2d_related_root):
        raise Live2DModelImportError("不能导入当前 live2d_related 目录中的模型。")

    with open(source_model_json, "r", encoding="utf-8") as model_file:
        loaded_data: object = json.load(model_file)
    if not isinstance(loaded_data, dict):
        raise Live2DModelImportError("Live2D 模型 JSON 顶层不是对象。")
    model_data = cast(dict[str, object], loaded_data)

    model_name = _sanitize_model_dir_name(source_model_json.parent.name)
    extra_model_dir = live2d_related_root / character_folder_name / "extra_model"
    extra_model_dir.mkdir(parents=True, exist_ok=True)
    target_dir = _create_unique_model_dir(extra_model_dir, model_name)

    try:
        target_model_json = _copy_model_references_and_write_json(source_model_json, model_data, target_dir)
        normalize_result = normalize_live2d_model_for_project(str(target_model_json))
        if not normalize_result.ok:
            message = normalize_result.error_message or "Live2D 模型规范化失败。"
            raise Live2DModelImportError(message)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return Live2DModelImportResult(
        model_name=target_dir.name,
        model_json_path=str(target_model_json),
        target_dir=str(target_dir),
    )


def _copy_model_references_and_write_json(
        source_model_json: Path,
        model_data: dict[str, object],
        target_dir: Path,
) -> Path:
    """复制模型 JSON 声明的资源文件，并把重写后的模型 JSON 写入目标目录。"""
    context = _ReferenceCopyContext(
        source_model_dir=source_model_json.parent,
        target_dir=target_dir,
        copied_by_source={},
        used_targets={source_model_json.name: str(source_model_json)},
    )
    if source_model_json.name.endswith(".model3.json"):
        _rewrite_model3_references(model_data, context)
    else:
        _rewrite_model2_references(model_data, context)

    target_model_json = target_dir / source_model_json.name
    with open(target_model_json, "w", encoding="utf-8") as model_file:
        json.dump(model_data, model_file, ensure_ascii=False, indent=4)
    return target_model_json


def _rewrite_model3_references(model_data: dict[str, object], context: _ReferenceCopyContext) -> None:
    """重写 Live2D V3 model3.json 中的资源引用。"""
    file_references_value = model_data.get("FileReferences")
    if not isinstance(file_references_value, dict):
        raise Live2DModelImportError("Live2D V3 模型缺少有效的 FileReferences。")
    file_references = cast(dict[str, object], file_references_value)

    _rewrite_file_reference_in_mapping(file_references, MODEL3_DIRECT_REFERENCE_KEYS, context)
    _rewrite_file_reference_list(file_references, "Textures", context)
    _rewrite_entries_file_reference_list(file_references, "Expressions", ("File",), context)
    _rewrite_motion_groups(file_references, "Motions", ("File", "Sound"), context)


def _rewrite_model2_references(model_data: dict[str, object], context: _ReferenceCopyContext) -> None:
    """重写 Live2D V2 model.json 中的资源引用。"""
    _rewrite_file_reference_in_mapping(model_data, MODEL2_DIRECT_REFERENCE_KEYS, context)
    _rewrite_file_reference_list(model_data, "textures", context)
    _rewrite_entries_file_reference_list(model_data, "expressions", ("file", "File"), context)
    _rewrite_motion_groups(model_data, "motions", ("file", "File", "sound", "Sound"), context)


def _rewrite_file_reference_in_mapping(
        mapping: dict[str, object],
        keys: Sequence[str],
        context: _ReferenceCopyContext,
) -> None:
    """重写一个 JSON 对象中若干文件引用字段。"""
    for key in keys:
        if key in mapping:
            mapping[key] = _copy_model_reference(mapping[key], context)


def _rewrite_file_reference_list(
        mapping: dict[str, object],
        key: str,
        context: _ReferenceCopyContext,
) -> None:
    """重写一个 JSON 对象中的文件引用列表。"""
    values = mapping.get(key)
    if not isinstance(values, list):
        return
    for index, value in enumerate(values):
        values[index] = _copy_model_reference(value, context)


def _rewrite_entries_file_reference_list(
        mapping: dict[str, object],
        key: str,
        reference_keys: Sequence[str],
        context: _ReferenceCopyContext,
) -> None:
    """重写对象列表中的文件引用字段。"""
    entries = mapping.get(key)
    if not isinstance(entries, list):
        return
    for entry in entries:
        if isinstance(entry, dict):
            _rewrite_file_reference_in_mapping(cast(dict[str, object], entry), reference_keys, context)


def _rewrite_motion_groups(
        mapping: dict[str, object],
        key: str,
        reference_keys: Sequence[str],
        context: _ReferenceCopyContext,
) -> None:
    """重写动作组列表中的动作文件和声音文件引用。"""
    motion_groups = mapping.get(key)
    if not isinstance(motion_groups, dict):
        return
    for entries in cast(dict[str, object], motion_groups).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                _rewrite_file_reference_in_mapping(cast(dict[str, object], entry), reference_keys, context)


def _copy_model_reference(file_value: object, context: _ReferenceCopyContext) -> object:
    """复制一个模型资源引用，并返回写入目标 JSON 的相对路径。"""
    if not isinstance(file_value, str) or not file_value:
        return file_value

    source_path = _resolve_source_reference(context.source_model_dir, file_value)
    source_key = str(source_path.resolve(strict=False))
    cached_reference = context.copied_by_source.get(source_key)
    if cached_reference is not None:
        return cached_reference
    if not source_path.is_file():
        raise Live2DModelImportError(f"Live2D 模型引用的文件不存在：{source_path}")

    preferred_relative_path = _preferred_target_relative_path(file_value, source_path)
    target_relative_path = _reserve_target_relative_path(preferred_relative_path, source_key, context)
    target_path = (context.target_dir / target_relative_path).resolve(strict=False)
    if not _is_relative_to_path(target_path, context.target_dir.resolve(strict=False)):
        raise Live2DModelImportError(f"Live2D 模型引用的目标路径非法：{target_relative_path.as_posix()}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    target_reference = target_relative_path.as_posix()
    context.copied_by_source[source_key] = target_reference
    return target_reference


def _resolve_source_reference(source_model_dir: Path, file_value: str) -> Path:
    """把模型 JSON 中的文件引用解析成源文件绝对路径。"""
    direct_path = Path(file_value)
    if direct_path.is_absolute():
        return direct_path.resolve(strict=False)
    normalized_reference = file_value.replace("\\", "/")
    relative_parts = PurePosixPath(normalized_reference).parts
    return source_model_dir.joinpath(*relative_parts).resolve(strict=False)


def _preferred_target_relative_path(file_value: str, source_path: Path) -> Path:
    """根据原始引用生成目标目录内优先使用的相对路径。"""
    normalized_reference = file_value.replace("\\", "/")
    relative_reference = PurePosixPath(normalized_reference)
    if Path(file_value).is_absolute() or ".." in relative_reference.parts:
        return Path(REFERENCE_EXTERNAL_ASSETS_DIR) / source_path.name
    return Path(*relative_reference.parts)


def _reserve_target_relative_path(
        preferred_relative_path: Path,
        source_key: str,
        context: _ReferenceCopyContext,
) -> Path:
    """为一个源文件预留不冲突的目标相对路径。"""
    relative_path = preferred_relative_path
    suffix_index = 2
    while True:
        relative_key = relative_path.as_posix()
        existing_source = context.used_targets.get(relative_key)
        if existing_source is None or existing_source == source_key:
            context.used_targets[relative_key] = source_key
            return relative_path
        relative_path = _with_unique_suffix(preferred_relative_path, suffix_index)
        suffix_index += 1


def _with_unique_suffix(relative_path: Path, suffix_index: int) -> Path:
    """为目标相对路径追加数字后缀。"""
    return relative_path.with_name(f"{relative_path.stem}_{suffix_index}{relative_path.suffix}")


def _create_unique_model_dir(extra_model_dir: Path, model_name: str) -> Path:
    """在 extra_model 目录下创建一个不覆盖既有模型的唯一目录。"""
    candidate = extra_model_dir / model_name
    suffix_index = 2
    while candidate.exists():
        candidate = extra_model_dir / f"{model_name}_{suffix_index}"
        suffix_index += 1
    candidate.mkdir(parents=True)
    return candidate


def _sanitize_model_dir_name(name: str) -> str:
    """把源模型父目录名转换成可用的本地模型目录名。"""
    sanitized_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not sanitized_name:
        return "imported_model"
    return sanitized_name


def _is_supported_model_json_name(file_name: str) -> bool:
    """判断文件名是否是支持导入的 Live2D 模型 JSON。"""
    return file_name.endswith(".model3.json") or file_name.endswith(".model.json")


def _is_relative_to_path(child: Path, parent: Path) -> bool:
    """判断 child 是否位于 parent 目录之内。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
