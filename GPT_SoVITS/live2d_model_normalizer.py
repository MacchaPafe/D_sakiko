from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, cast

MotionPosition = Literal["C", "L", "R"]

NORMALIZED_MODEL3_VERSION = 1
DSAKIKO_MODEL3_METADATA_KEY = "DSakiko"
POSITION_MOTION_SUFFIXES: tuple[MotionPosition, ...] = ("C", "L", "R")
L2D_STANDARD_MOTION_GROUPS = [
    "happiness",
    "sadness",
    "anger",
    "disgust",
    "like",
    "surprise",
    "fear",
    "IDLE",
    "text_generating",
    "bye",
    "change_character",
    "idle_motion",
    "talking_motion",
]

DIRECT_MOTION_KEYWORDS: dict[str, frozenset[str]] = {
    "happiness": frozenset(("smile", "kime", "wink", "note")),
    "sadness": frozenset(("sad", "cry")),
    "anger": frozenset(("angry",)),
    "disgust": frozenset(("denial", "confrict")),
    "like": frozenset(("smile", "kime", "wink", "note")),
    "surprise": frozenset(("surprised", "question")),
    "fear": frozenset(("nervous",)),
    "IDLE": frozenset(("idle",)),
    "text_generating": frozenset(("thinking", "check", "look", "nervous")),
    "bye": frozenset(("bye", "finish")),
    "change_character": frozenset(("bye", "join", "kime", "smile", "action", "spin", "maskoff")),
    "idle_motion": frozenset(("idle",)),
    "talking_motion": frozenset(("nod", "sing")),
}

WEAK_MOTION_KEYWORDS: dict[str, frozenset[str]] = {
    "sadness": frozenset(("serious", "denial")),
    "anger": frozenset(("serious", "denial", "confrict")),
    "disgust": frozenset(("serious", "angry")),
    "fear": frozenset(("cry", "serious", "surprised", "confrict")),
    "happiness": frozenset(("play01", "play02", "play", "action")),
    "like": frozenset(("play01", "play02", "play")),
    "surprise": frozenset(("action", "play02")),
    "text_generating": frozenset(("nod", "serious")),
    "talking_motion": frozenset(("play01", "play02")),
    "change_character": frozenset(("play01", "play02")),
}

FALLBACK_MOTION_GROUPS: dict[str, tuple[str, ...]] = {
    "happiness": ("like", "IDLE"),
    "sadness": ("fear", "IDLE"),
    "anger": ("disgust", "sadness", "IDLE"),
    "disgust": ("anger", "sadness", "IDLE"),
    "like": ("happiness", "IDLE"),
    "surprise": ("happiness", "IDLE"),
    "fear": ("sadness", "surprise", "IDLE"),
    "text_generating": ("talking_motion", "IDLE"),
    "bye": ("change_character", "happiness", "IDLE"),
    "change_character": ("bye", "happiness", "IDLE"),
    "idle_motion": ("IDLE",),
    "talking_motion": ("text_generating", "IDLE"),
    "IDLE": ("idle_motion", "happiness"),
}

MODEL3_ASSET_REFERENCE_KEYS = ("Moc", "Physics", "Pose", "DisplayInfo", "UserData")


@dataclass(frozen=True)
class Live2DModelNormalizeResult:
    """记录一次 Live2D 模型规范化的结果。"""

    ok: bool
    converted_old_model: bool = False
    normalized_model3: bool = False
    error_message: str = ""


def is_old_l2d_json(old_l2d_json_path: str | None) -> bool:
    """判断是否为需要转换的旧版 Live2D model.json 格式。"""
    if not old_l2d_json_path or not str(old_l2d_json_path).endswith(".model.json"):
        return False
    try:
        with open(old_l2d_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    motions = data.get("motions")
    return isinstance(motions, dict) and "rana" in motions


def is_l2d_model3_json(l2d_json_path: str | None) -> bool:
    """判断是否为 Live2D V3 model3.json。"""
    return bool(l2d_json_path) and str(l2d_json_path).endswith(".model3.json")


def convert_old_l2d_json(old_l2d_json_path: str) -> bool:
    """将旧版 Live2D model.json 动作组转换为项目标准动作组。"""
    with open(old_l2d_json_path, 'r', encoding='utf-8') as f:
        old_data = json.load(f)
    if not isinstance(old_data, dict):
        return False

    new_data = copy.deepcopy(old_data)
    if 'controllers' in new_data:
        del new_data['controllers']

    if 'hit_areas' in new_data:
        del new_data['hit_areas']

    old_motions = old_data.get('motions', {})
    if not isinstance(old_motions, dict):
        return False
    old_rana_value = old_motions.get('rana', [])
    if not isinstance(old_rana_value, list):
        return False
    old_rana_list: list[object] = old_rana_value
    if not old_rana_list:
        return False

    motion_mapping: list[tuple[str, int, int]] = [
        ("happiness", 0, 6),
        ("sadness", 6, 12),
        ("anger", 12, 18),
        ("disgust", 18, 24),
        ("like", 24, 30),
        ("surprise", 30, 36),
        ("fear", 36, 42),
        ("IDLE", 42, 51),
        ("text_generating", 51, 54),
        ("bye", 54, 56),
        ("change_character", 56, 59),
        ("idle_motion", 59, 60),
        ("talking_motion", 60, 61),
    ]

    new_motions: dict[str, object] = {}
    for name, start_idx, end_idx in motion_mapping:
        new_motions[name] = old_rana_list[start_idx:end_idx]

    new_data['motions'] = new_motions

    with open(old_l2d_json_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=4)

    return True


def _as_object_mapping(value: object) -> dict[str, object]:
    """把 JSON 对象值收窄为可读写的字符串键字典。"""
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, object], value)


def _is_relative_to_path(child: Path, parent: Path) -> bool:
    """判断 child 是否位于 parent 目录之内。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_model3_reference(model_dir: Path, file_value: str) -> Path:
    """把 model3.json 中的文件引用解析为绝对路径。"""
    reference_path = Path(file_value)
    if reference_path.is_absolute():
        return reference_path.resolve(strict=False)
    return (model_dir / reference_path).resolve(strict=False)


def _move_or_copy_model3_asset(
        source_path: Path,
        target_path: Path,
        model_dir: Path,
        moved_dirs: set[Path],
) -> None:
    """将一个 Live2D V3 资产平铺到 model3.json 同级目录。"""
    source_resolved = source_path.resolve(strict=False)
    target_resolved = target_path.resolve(strict=False)
    if source_resolved == target_resolved:
        return
    if target_path.exists():
        if target_path.is_dir():
            raise IsADirectoryError(f"Live2D V3 资产平铺目标是目录：{target_path}")
        target_path.unlink()

    model_dir_resolved = model_dir.resolve(strict=False)
    if _is_relative_to_path(source_resolved, model_dir_resolved):
        source_parent = source_path.parent
        shutil.move(str(source_path), str(target_path))
        moved_dirs.add(source_parent)
    else:
        shutil.copy2(source_path, target_path)


def _flatten_model3_asset_reference(
        file_value: object,
        model_dir: Path,
        moved_dirs: set[Path],
        flattened_by_source: dict[str, str],
) -> object:
    """平铺一个 model3.json 文件引用并返回写回 JSON 的同级文件名。"""
    if not isinstance(file_value, str) or not file_value:
        return file_value

    source_path = _resolve_model3_reference(model_dir, file_value)
    source_key = str(source_path.resolve(strict=False))
    cached_name = flattened_by_source.get(source_key)
    if cached_name is not None:
        return cached_name

    if not source_path.is_file():
        raise FileNotFoundError(f"Live2D V3 模型引用的文件不存在：{source_path}")

    target_name = source_path.name
    target_path = model_dir / target_name
    _move_or_copy_model3_asset(source_path, target_path, model_dir, moved_dirs)
    flattened_by_source[source_key] = target_name
    return target_name


def _remove_empty_model3_dirs(moved_dirs: Iterable[Path], model_dir: Path) -> None:
    """删除规范化过程中被搬空的模型内部目录。"""
    model_dir_resolved = model_dir.resolve(strict=False)
    for moved_dir in sorted(moved_dirs, key=lambda path: len(path.parts), reverse=True):
        current_dir = moved_dir
        while current_dir != model_dir:
            current_resolved = current_dir.resolve(strict=False)
            if not _is_relative_to_path(current_resolved, model_dir_resolved):
                break
            try:
                current_dir.rmdir()
            except OSError:
                break
            current_dir = current_dir.parent


def _motion_position_from_file(file_name: str) -> MotionPosition | None:
    """从 motion 文件名中读取 C/L/R 位置后缀。"""
    stem = Path(file_name).name.removesuffix(".motion3.json")
    match = re.search(r"_([CLR])$", stem, flags=re.IGNORECASE)
    if match is None:
        return None
    value = match.group(1).upper()
    if value in POSITION_MOTION_SUFFIXES:
        return cast(MotionPosition, value)
    return None


def _motion_keywords_from_name(name: str) -> set[str]:
    """从动作组名或动作文件名中提取用于归组的词根。"""
    stem = Path(name).name.removesuffix(".motion3.json").lower()
    stem = re.sub(r"^(mtn_|motion_)", "", stem)
    stem = re.sub(r"_([clr])$", "", stem)
    keywords: set[str] = set()
    for segment in re.split(r"[_\-]+", stem):
        if not segment or segment in {"mtn", "motion", "c", "l", "r"}:
            continue
        keywords.add(segment)
        trimmed = re.sub(r"\d+$", "", segment)
        if trimmed:
            keywords.add(trimmed)
    return keywords


def _motion_entry_key(entry: dict[str, object]) -> str:
    """为动作条目生成去重键。"""
    file_value = entry.get("File")
    if isinstance(file_value, str):
        return file_value
    return repr(sorted((str(key), repr(value)) for key, value in entry.items()))


def _append_unique_motion_entry(target: list[dict[str, object]], entry: dict[str, object]) -> None:
    """向动作组追加条目，并按 File 字段去重。"""
    entry_key = _motion_entry_key(entry)
    existing_keys = {_motion_entry_key(existing_entry) for existing_entry in target}
    if entry_key not in existing_keys:
        target.append(dict(entry))


def _matching_motion_entries(
        candidates: list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]],
        keywords: frozenset[str],
) -> list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]]:
    """筛选包含指定关键词的动作候选。"""
    return [
        candidate
        for candidate in candidates
        if candidate[1] & keywords
    ]


def _entries_by_position(
        candidates: list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]],
) -> dict[MotionPosition, list[dict[str, object]]]:
    """将动作候选按位置后缀分配到 C/L/R 组。"""
    grouped: dict[MotionPosition, list[dict[str, object]]] = {"C": [], "L": [], "R": []}
    for entry, _keywords, position, _sort_key in sorted(candidates, key=lambda item: item[3]):
        if position is None:
            for suffix in POSITION_MOTION_SUFFIXES:
                _append_unique_motion_entry(grouped[suffix], entry)
        else:
            _append_unique_motion_entry(grouped[position], entry)
    return grouped


def _build_standard_model3_motions(
        candidates: list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]],
) -> dict[str, list[dict[str, object]]]:
    """根据动作候选生成项目标准动作组和位置变体组。"""
    grouped_by_standard: dict[str, dict[MotionPosition, list[dict[str, object]]]] = {}
    all_position_entries = _entries_by_position(candidates)

    for group_name in L2D_STANDARD_MOTION_GROUPS:
        matched_candidates = _matching_motion_entries(candidates, DIRECT_MOTION_KEYWORDS.get(group_name, frozenset()))
        if not matched_candidates:
            matched_candidates = _matching_motion_entries(candidates, WEAK_MOTION_KEYWORDS.get(group_name, frozenset()))
        grouped_by_standard[group_name] = _entries_by_position(matched_candidates)

    for group_name in L2D_STANDARD_MOTION_GROUPS:
        for suffix in POSITION_MOTION_SUFFIXES:
            if grouped_by_standard[group_name][suffix]:
                continue
            for fallback_group in FALLBACK_MOTION_GROUPS.get(group_name, ()):
                fallback_entries = grouped_by_standard.get(fallback_group, {}).get(suffix, [])
                if fallback_entries:
                    grouped_by_standard[group_name][suffix] = [dict(entry) for entry in fallback_entries]
                    break
            if not grouped_by_standard[group_name][suffix] and all_position_entries[suffix]:
                grouped_by_standard[group_name][suffix] = [dict(entry) for entry in all_position_entries[suffix]]

    result: dict[str, list[dict[str, object]]] = {}
    for group_name in L2D_STANDARD_MOTION_GROUPS:
        center_entries = [dict(entry) for entry in grouped_by_standard[group_name]["C"]]
        result[group_name] = center_entries
        for suffix in POSITION_MOTION_SUFFIXES:
            result[f"{group_name}_{suffix}"] = [dict(entry) for entry in grouped_by_standard[group_name][suffix]]
    return result


def _standard_model3_motion_schema_is_complete(motions: object) -> bool:
    """检查 model3.json 是否包含完整的标准动作组结构。"""
    if not isinstance(motions, dict):
        return False
    motion_mapping = cast(dict[str, object], motions)
    for group_name in L2D_STANDARD_MOTION_GROUPS:
        if not isinstance(motion_mapping.get(group_name), list):
            return False
        for suffix in POSITION_MOTION_SUFFIXES:
            if not isinstance(motion_mapping.get(f"{group_name}_{suffix}"), list):
                return False
    return True


def _referenced_model3_files_exist(file_references: dict[str, object], model_dir: Path) -> bool:
    """检查当前 model3.json 声明引用的本地文件是否都存在。"""
    for key in MODEL3_ASSET_REFERENCE_KEYS:
        value = file_references.get(key)
        if isinstance(value, str) and value and not _resolve_model3_reference(model_dir, value).is_file():
            return False

    textures = file_references.get("Textures")
    if isinstance(textures, list):
        for texture in textures:
            if isinstance(texture, str) and texture and not _resolve_model3_reference(model_dir, texture).is_file():
                return False

    expressions = file_references.get("Expressions")
    if isinstance(expressions, list):
        for expression in expressions:
            expression_mapping = _as_object_mapping(expression)
            file_value = expression_mapping.get("File")
            if isinstance(file_value, str) and file_value and not _resolve_model3_reference(model_dir, file_value).is_file():
                return False

    motions = file_references.get("Motions")
    if isinstance(motions, dict):
        for motion_entries in cast(dict[str, object], motions).values():
            if not isinstance(motion_entries, list):
                continue
            for motion_entry in motion_entries:
                motion_mapping = _as_object_mapping(motion_entry)
                for key in ("File", "Sound"):
                    file_value = motion_mapping.get(key)
                    if isinstance(file_value, str) and file_value and not _resolve_model3_reference(model_dir, file_value).is_file():
                        return False
    return True


def _model3_is_normalized(model_data: dict[str, object], file_references: dict[str, object], model_dir: Path) -> bool:
    """判断 model3.json 是否已经按当前项目规则规范化。"""
    metadata = _as_object_mapping(model_data.get(DSAKIKO_MODEL3_METADATA_KEY))
    if metadata.get("NormalizedModel3Version") != NORMALIZED_MODEL3_VERSION:
        return False
    if not _standard_model3_motion_schema_is_complete(file_references.get("Motions")):
        return False
    return _referenced_model3_files_exist(file_references, model_dir)


def _flatten_model3_file_references(
        file_references: dict[str, object],
        model_dir: Path,
) -> list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]]:
    """平铺 FileReferences 中的资产路径，并收集原始动作候选。"""
    moved_dirs: set[Path] = set()
    flattened_by_source: dict[str, str] = {}

    for key in MODEL3_ASSET_REFERENCE_KEYS:
        if key in file_references:
            file_references[key] = _flatten_model3_asset_reference(
                file_references[key],
                model_dir,
                moved_dirs,
                flattened_by_source,
            )

    textures = file_references.get("Textures")
    if isinstance(textures, list):
        for index, texture in enumerate(textures):
            textures[index] = _flatten_model3_asset_reference(texture, model_dir, moved_dirs, flattened_by_source)

    expressions = file_references.get("Expressions")
    if isinstance(expressions, list):
        for expression in expressions:
            expression_mapping = _as_object_mapping(expression)
            if "File" in expression_mapping:
                expression_mapping["File"] = _flatten_model3_asset_reference(
                    expression_mapping["File"],
                    model_dir,
                    moved_dirs,
                    flattened_by_source,
                )

    motion_candidates: list[tuple[dict[str, object], frozenset[str], MotionPosition | None, str]] = []
    motions = file_references.get("Motions")
    if isinstance(motions, dict):
        for group_name, motion_entries in cast(dict[str, object], motions).items():
            if not isinstance(motion_entries, list):
                continue
            for motion_index, motion_entry in enumerate(motion_entries):
                motion_mapping = _as_object_mapping(motion_entry)
                if not motion_mapping:
                    continue
                next_entry = dict(motion_mapping)
                file_value = next_entry.get("File")
                next_entry["File"] = _flatten_model3_asset_reference(
                    file_value,
                    model_dir,
                    moved_dirs,
                    flattened_by_source,
                )
                if "Sound" in next_entry:
                    next_entry["Sound"] = _flatten_model3_asset_reference(
                        next_entry["Sound"],
                        model_dir,
                        moved_dirs,
                        flattened_by_source,
                    )
                flattened_file = next_entry.get("File")
                file_name = flattened_file if isinstance(flattened_file, str) else ""
                keywords = _motion_keywords_from_name(str(group_name)) | _motion_keywords_from_name(file_name)
                position = _motion_position_from_file(file_name)
                sort_key = f"{group_name}:{motion_index:04d}:{file_name}"
                motion_candidates.append((next_entry, frozenset(keywords), position, sort_key))

    _remove_empty_model3_dirs(moved_dirs, model_dir)
    return motion_candidates


def normalize_model3_for_project(model3_json_path: str) -> bool:
    """将 Live2D V3 model3.json 规范化为项目内部可用结构。"""
    model_path = Path(model3_json_path)
    model_dir = model_path.parent
    with open(model3_json_path, 'r', encoding='utf-8') as f:
        loaded_data = json.load(f)
    if not isinstance(loaded_data, dict):
        raise ValueError(f"Live2D V3 模型 JSON 顶层不是对象：{model3_json_path}")
    model_data = cast(dict[str, object], loaded_data)

    file_references_value = model_data.setdefault("FileReferences", {})
    if not isinstance(file_references_value, dict):
        raise ValueError(f"Live2D V3 模型 FileReferences 格式错误：{model3_json_path}")
    file_references = cast(dict[str, object], file_references_value)

    if _model3_is_normalized(model_data, file_references, model_dir):
        return False

    motion_candidates = _flatten_model3_file_references(file_references, model_dir)
    file_references["Motions"] = _build_standard_model3_motions(motion_candidates)
    model_data[DSAKIKO_MODEL3_METADATA_KEY] = {
        "NormalizedModel3Version": NORMALIZED_MODEL3_VERSION,
    }

    with open(model3_json_path, 'w', encoding='utf-8') as f:
        json.dump(model_data, f, ensure_ascii=False, indent=4)

    return True


def normalize_live2d_model_for_project(model_json_path: str | None) -> Live2DModelNormalizeResult:
    """在程序使用 Live2D 模型前完成必要的项目规范化。"""
    if not model_json_path:
        return Live2DModelNormalizeResult(ok=True)

    try:
        if is_old_l2d_json(model_json_path):
            converted = convert_old_l2d_json(model_json_path)
            return Live2DModelNormalizeResult(ok=True, converted_old_model=converted)
        if is_l2d_model3_json(model_json_path):
            normalized = normalize_model3_for_project(model_json_path)
            return Live2DModelNormalizeResult(ok=True, normalized_model3=normalized)
    except Exception as exc:
        return Live2DModelNormalizeResult(ok=False, error_message=str(exc) or exc.__class__.__name__)

    return Live2DModelNormalizeResult(ok=True)
