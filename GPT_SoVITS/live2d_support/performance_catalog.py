"""独立演出的资源目录、描述继承和组合预设。"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from live2d_support.expression_policy import SEMANTIC_EXPRESSION_CANDIDATES, normalized_name_tokens

PERFORMANCE_MOTION_GROUP = "__dsakiko_performance__"
SCHEMA_VERSION = 1
_MOTION_LABELS = {"nod": "点头", "denial": "摇头或否定手势", "bye": "告别手势",
                  "thinking": "思考动作", "look": "转动视线或身体", "idle": "待机动作"}
_EXPRESSION_LABELS = {"smile": "微笑", "bsmile": "笑容", "sad": "悲伤", "cry": "哭泣表情",
                     "angry": "生气", "serious": "认真", "surprised": "惊讶", "shy": "害羞",
                     "thinking": "思考", "pale": "不安", "idle": "平静", "upset": "不悦",
                     "sneer": "冷笑", "smirk": "带笑的表情", "amazed": "惊讶", "hatred": "愤恨"}


def object_mapping(value: object) -> dict[str, object]:
    """将配置对象收窄为字符串键字典。"""
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def read_config(path: Path) -> dict[str, object]:
    """读取可选配置；损坏时保留文件并退回基础目录。"""
    try:
        return object_mapping(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("无法读取演出配置：%s", path)
        return {}


def save_config(path: Path, data: dict[str, object]) -> None:
    """原子保存用户配置，避免部分写入损坏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data, schema_version=SCHEMA_VERSION)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def performance_config_path(model_path: Path) -> Path:
    """每个模型入口有自己的覆盖与预设文件。"""
    return model_path.with_suffix(".performance.json")


def shared_config_path(model_path: Path) -> Path:
    """以角色目录标识共享范围，外部模型使用同目录共享文件。"""
    for parent in model_path.parents:
        if parent.name in {"live2D_model", "live2D_model_costume", "extra_model"}:
            return parent.parent / "performance.shared.json"
    return model_path.parent / "performance.shared.json"


def motion_identity(file: str) -> tuple[str, str]:
    """仅合并同目录动作的方向变体，避免同名资源跨目录冲突。"""
    stem = file.replace("\\", "/").removesuffix(".motion3.json")
    match = re.search(r"_([CLR])$", stem, re.IGNORECASE)
    return (stem[:match.start()], match.group(1).upper()) if match else (stem, "")


def motion_assets(model_data: dict[str, object]) -> list[dict[str, object]]:
    """合并完整索引和兼容动作组，保持元数据并按文件去重。"""
    refs = object_mapping(model_data.get("FileReferences"))
    groups = object_mapping(refs.get("Motions"))
    indexed = object_mapping(model_data.get("DSakiko")).get("MotionAssets", [])
    entries: list[object] = list(indexed) if isinstance(indexed, list) else []
    for group, values in groups.items():
        if not group.startswith("__") and isinstance(values, list):
            entries.extend(values)
    by_file: dict[str, dict[str, object]] = {}
    for entry in entries:
        data = object_mapping(entry)
        file = data.get("File")
        if isinstance(file, str) and file:
            by_file.setdefault(file, data)
    return [by_file[key] for key in sorted(by_file)]


@dataclass(frozen=True)
class MotionAsset:
    """一个具体方向的动作文件及其播放元数据。"""

    file: str
    entry: dict[str, object]


@dataclass
class PerformanceCatalog:
    """向编辑器、LLM 和播放器提供同源且用途明确的投影。"""

    model_path: Path
    version: str
    motions: dict[str, dict[str, MotionAsset]] = field(default_factory=dict)
    expressions: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, dict[str, str]] = field(default_factory=dict)
    inherited_descriptions: dict[str, dict[str, str]] = field(default_factory=dict)
    sources: dict[str, dict[str, str]] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)
    presets: list[dict[str, object]] = field(default_factory=list)
    config: dict[str, object] = field(default_factory=dict)
    shared: dict[str, object] = field(default_factory=dict)
    series: str = "default"

    def motion(self, motion_id: str, direction: str = "C") -> MotionAsset | None:
        """方向由宿主决定；缺少变体时使用基础或正面资源。"""
        variants = self.motions.get(motion_id, {})
        return variants.get(direction) or variants.get("") or variants.get("C")

    def resource_fingerprint(self, kind: str, resource_id: str) -> str:
        """仅在编辑或复核时计算内容摘要，不把路径或摘要当成资源 ID。"""
        files = ({direction: asset.file for direction, asset in self.motions.get(resource_id, {}).items()}
                 if kind == "motions" else {"": self.expressions.get(resource_id, "")})
        digest = hashlib.sha256()
        for direction, file in sorted(files.items()):
            if not file:
                continue
            digest.update(direction.encode())
            try:
                digest.update((self.model_path.parent / file).read_bytes())
            except OSError:
                digest.update(b"missing")
        return digest.hexdigest()

    def description_needs_review(self, kind: str, resource_id: str) -> bool:
        """资源内容与上次保存描述时不同时提示复核，不自动删除描述。"""
        recorded = object_mapping(object_mapping(self.config.get("fingerprints")).get(kind)).get(resource_id)
        return isinstance(recorded, str) and recorded != self.resource_fingerprint(kind, resource_id)

    def valid_presets(self) -> list[dict[str, object]]:
        """保留无效预设供编辑器修复，但不发送给 LLM。"""
        return [dict(item) for item in self.presets
                if isinstance(item.get("motion"), str) and isinstance(item.get("expression"), str)
                and item.get("motion") in self.motions and item.get("expression") in self.expressions]

    def prompt_projection(self) -> dict[str, object]:
        """只公开有描述的逻辑资源与组合，不公开方向和路径。"""
        return {"motions": self.descriptions.get("motions", {}),
                "expressions": self.descriptions.get("expressions", {}),
                "presets": self.valid_presets()}

    def runtime_projection(self) -> dict[str, object]:
        """导出跨语言解析所需资源和兼容候选，不复制语义推断规则。"""
        return {"version": self.version,
                "motions": {key: {direction: asset.file for direction, asset in variants.items()}
                            for key, variants in self.motions.items()},
                "expressions": self.expressions,
                "groups": self.groups,
                "semantic_expressions": {
                    key: [item for item in candidates if item in self.expressions]
                    for key, candidates in SEMANTIC_EXPRESSION_CANDIDATES.items()}}


def load_performance_catalog(model_path: str | Path) -> PerformanceCatalog:
    """从当前入口及可选描述文件建立目录，不修改模型文件。"""
    path = Path(model_path).resolve()
    data = read_config(path)
    version = "v3" if isinstance(data.get("FileReferences"), dict) else "v2"
    catalog = PerformanceCatalog(path, version)
    catalog.config = read_config(performance_config_path(path))
    catalog.shared = read_config(shared_config_path(path))
    if version != "v3":
        return catalog
    refs = object_mapping(data.get("FileReferences"))
    bindings = object_mapping(catalog.config.get("bindings"))
    file_to_id: dict[str, str] = {}
    assets = motion_assets(data)
    families_by_name: dict[str, set[str]] = {}
    for entry in assets:
        identity, _ = motion_identity(str(entry["File"]))
        families_by_name.setdefault(Path(identity).name, set()).add(identity)
    for entry in assets:
        file = str(entry["File"])
        if not (path.parent / file).is_file():
            continue
        motion_id, direction = motion_identity(file)
        # 普通目录名不成为公开 ID；同名冲突才保留目录命名空间。
        if len(families_by_name[Path(motion_id).name]) == 1:
            motion_id = Path(motion_id).name
        else:
            motion_id = hashlib.sha256(str(Path(motion_id).parent).encode()).hexdigest()[:8] + "_" + Path(motion_id).name
        if isinstance(entry.get("LogicalId"), str):
            motion_id = str(entry["LogicalId"])
        explicit_id = bindings.get(file)
        if isinstance(explicit_id, str) and explicit_id.strip():
            motion_id = explicit_id.strip()
        catalog.motions.setdefault(motion_id, {})[direction] = MotionAsset(file, entry)
        file_to_id[file] = motion_id
    expressions = refs.get("Expressions", [])
    if isinstance(expressions, list):
        for raw in expressions:
            entry = object_mapping(raw)
            name, file = entry.get("Name"), entry.get("File")
            if isinstance(name, str) and isinstance(file, str) and (path.parent / file).is_file():
                catalog.expressions[name] = file
    for group, raw_entries in object_mapping(refs.get("Motions")).items():
        if not isinstance(raw_entries, list) or group.startswith("__"):
            continue
        ids = [file_to_id[str(item.get("File"))] for item in raw_entries
               if isinstance(item, dict) and str(item.get("File")) in file_to_id]
        catalog.groups[group] = list(dict.fromkeys(ids))
    series = str(catalog.config.get("series") or "default")
    catalog.series = series
    shared_series = object_mapping(object_mapping(catalog.shared.get("series")).get(series))
    for kind, resources, labels in (("motions", catalog.motions, _MOTION_LABELS),
                                    ("expressions", catalog.expressions, _EXPRESSION_LABELS)):
        local = object_mapping(catalog.config.get(kind))
        common = object_mapping(shared_series.get(kind))
        catalog.descriptions[kind], catalog.sources[kind] = {}, {}
        catalog.inherited_descriptions[kind] = {}
        for resource_id in sorted(resources):
            fallback = next((labels[token] for token in normalized_name_tokens(resource_id) if token in labels), "")
            local_value, shared_value = local.get(resource_id), common.get(resource_id)
            inherited = shared_value
            catalog.inherited_descriptions[kind][resource_id] = inherited if isinstance(inherited, str) and inherited.strip() else fallback
            description = local_value if isinstance(local_value, str) else shared_value
            if not isinstance(description, str) or not description.strip():
                description = fallback
            if description:
                catalog.descriptions[kind][resource_id] = description
            catalog.sources[kind][resource_id] = "model" if isinstance(local_value, str) else "shared" if isinstance(shared_value, str) else "name"
    presets = catalog.config.get("presets", [])
    catalog.presets = [object_mapping(item) for item in presets if isinstance(item, dict)] if isinstance(presets, list) else []
    return catalog


def performance_motion_entries(catalog: PerformanceCatalog) -> list[dict[str, object]]:
    """生成原生和浏览器都能稳定引用的完整动作序列。"""
    entries = {asset.file: asset.entry for variants in catalog.motions.values() for asset in variants.values()}
    return [dict(entries[key]) for key in sorted(entries)]


def projected_model_document(model_path: Path) -> dict[str, object]:
    """为浏览器生成内部动作组视图，保持磁盘模型和用户分组不变。"""
    data = read_config(model_path)
    from .mask_actions import MASK_ACTIONS, MASK_MOTION_GROUP, mask_actions
    actions = mask_actions(str(model_path))
    if not isinstance(data.get("FileReferences"), dict):
        groups = object_mapping(data.get("motions"))
        groups[MASK_MOTION_GROUP] = [{"file": actions[key]} for key in MASK_ACTIONS if key in actions]
        data["motions"] = groups
        return data
    refs = object_mapping(data.get("FileReferences"))
    groups = object_mapping(refs.get("Motions"))
    entries = performance_motion_entries(load_performance_catalog(model_path))
    groups[PERFORMANCE_MOTION_GROUP] = [dict(entry, File=str(entry["File"]) + "?single=1") for entry in entries]
    groups[MASK_MOTION_GROUP] = [{"File": actions[action]} for action in MASK_ACTIONS if action in actions]
    refs["Motions"] = groups
    data["FileReferences"] = refs
    return data


def cubism_motion_json(data: dict[str, object]) -> str:
    """保持 Cubism 原生解析器接受的十进制数和文件末尾换行。"""
    serialized = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)

    def decimal_number(match: re.Match[str]) -> str:
        """只展开数值 token 的指数形式，不修改字符串内容。"""
        return format(Decimal(match.group(1)), "f") if match.group(1) else match.group(0)

    return re.sub(r'"(?:\\.|[^"\\])*"|(-?\d+(?:\.\d+)?[eE][+-]?\d+)', decimal_number, serialized) + "\n"
