from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast


Live2DVersion = Literal["v2", "v3"]
Live2DResolution = Literal["resolved", "absent", "configured_error"]


class Live2DAssetRegistry(Protocol):
    """定义呈现解析模块所需的最小资源注册接口。"""

    def register_live2d_model(self, model_path: Path) -> str:
        """注册模型并返回浏览器可访问的 URL。"""


@dataclass(frozen=True)
class Live2DError:
    """描述不可用的 Live2D 呈现目标。"""

    code: str
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, object]:
        """转换为 WebSocket 契约使用的字典。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class Live2DLayoutPresentation:
    """描述 WebUI 所需的单角色模型布局。"""

    scale: float
    offset_x: float
    offset_y: float

    def to_dict(self) -> dict[str, float]:
        """转换为前端可直接消费的字典。"""
        return {
            "scale": self.scale,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
        }


@dataclass(frozen=True)
class Live2DCapabilities:
    """记录前端选择动作和表情所需的模型能力。"""

    motion_files_by_group: dict[str, tuple[str, ...]]
    expression_ids: tuple[str, ...]
    expressions_by_motion: dict[str, tuple[str | None, ...]]
    semantic_expressions: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, object]:
        """转换为可序列化的能力摘要。"""
        return {
            "motion_groups": sorted(self.motion_files_by_group),
            "motion_files_by_group": {
                group_name: list(files)
                for group_name, files in self.motion_files_by_group.items()
            },
            "expression_ids": list(self.expression_ids),
            "expressions_by_motion": {
                group_name: list(expressions)
                for group_name, expressions in self.expressions_by_motion.items()
            },
            "semantic_expressions": {
                semantic: list(expressions)
                for semantic, expressions in self.semantic_expressions.items()
            },
        }


@dataclass(frozen=True)
class Live2DPresentation:
    """描述当前对话的 Live2D 呈现目标与解析结果。"""

    resolution: Live2DResolution
    target_id: str | None = None
    model_url: str | None = None
    version: Live2DVersion | None = None
    revision: str | None = None
    layout: Live2DLayoutPresentation | None = None
    capabilities: Live2DCapabilities | None = None
    error: Live2DError | None = None

    def to_dict(self) -> dict[str, object]:
        """转换为后端快照和事件的数据契约。"""
        return {
            "resolution": self.resolution,
            "target_id": self.target_id,
            "model_url": self.model_url,
            "version": self.version,
            "revision": self.revision,
            "layout": self.layout.to_dict() if self.layout is not None else None,
            "capabilities": self.capabilities.to_dict() if self.capabilities is not None else None,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True)
class _ManifestCacheEntry:
    """保存一份与模型修订绑定的解析结果。"""

    version: Live2DVersion
    capabilities: Live2DCapabilities


class Live2DPresentationResolver:
    """将对话与角色配置解析为安全的 WebUI Live2D 呈现描述。"""

    def __init__(
        self,
        assets: Live2DAssetRegistry,
        project_root: Path,
        live2d_root: Path,
        gpt_root: Path,
    ) -> None:
        """设置资源注册器与可信目录。"""
        self._assets = assets
        self._project_root = project_root.resolve()
        self._live2d_root = live2d_root.resolve()
        self._gpt_root = gpt_root.resolve()
        self._manifest_cache: dict[tuple[str, int, int], _ManifestCacheEntry] = {}

    def resolve(self, chat: object, character: object) -> Live2DPresentation:
        """解析当前对话的有效 Live2D 呈现目标。"""
        character_name = self._required_string_attribute(character, "character_name")
        character_folder_name = self._required_string_attribute(character, "character_folder_name")
        explicit_target = self._explicit_target(chat, character_name)
        raw_target = explicit_target
        if raw_target is None:
            default_target = getattr(character, "live2d_json", None)
            raw_target = default_target.strip() if isinstance(default_target, str) and default_target.strip() else None
        if raw_target is None:
            return Live2DPresentation(resolution="absent")

        model_path = self._resolve_model_path(raw_target)
        target_id = self._target_id(model_path)
        revision = self._revision(model_path)
        character_root = (self._live2d_root / character_folder_name).resolve()
        if not self._is_relative_to(model_path, character_root):
            return self._configured_error(
                target_id,
                revision,
                "LIVE2D_TARGET_OUTSIDE_CHARACTER",
                "当前对话配置的 Live2D 模型不在该角色资源目录中。",
            )
        if not model_path.is_file():
            return self._configured_error(
                target_id,
                revision,
                "LIVE2D_TARGET_NOT_FOUND",
                "当前对话配置的 Live2D 模型文件不存在。",
            )
        if not (model_path.name.endswith(".model.json") or model_path.name.endswith(".model3.json")):
            return self._configured_error(
                target_id,
                revision,
                "LIVE2D_TARGET_UNSUPPORTED",
                "当前对话配置的文件不是受支持的 Live2D 模型配置。",
            )

        try:
            manifest = self._manifest(model_path)
            layout = self._layout(model_path, manifest.version)
            model_url = self._assets.register_live2d_model(model_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return self._configured_error(
                target_id,
                revision,
                "LIVE2D_TARGET_INVALID",
                "当前对话配置的 Live2D 模型无法解析。",
            )

        return Live2DPresentation(
            resolution="resolved",
            target_id=target_id,
            model_url=model_url,
            version=manifest.version,
            revision=revision,
            layout=layout,
            capabilities=manifest.capabilities,
        )

    @staticmethod
    def _required_string_attribute(target: object, attribute_name: str) -> str:
        """读取必需字符串属性。"""
        value = getattr(target, attribute_name, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Live2D 呈现目标缺少 {attribute_name}")
        return value.strip()

    @staticmethod
    def _explicit_target(chat: object, character_name: str) -> str | None:
        """读取对话元数据中的显式模型目标，不触发默认回退。"""
        meta = getattr(chat, "meta", None)
        models = getattr(meta, "live2d_models", None)
        if not isinstance(models, Mapping):
            return None
        value = models.get(character_name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _resolve_model_path(self, raw_target: str) -> Path:
        """按项目与 GPT 运行时语义规范化模型路径。"""
        raw_path = Path(raw_target).expanduser()
        if raw_path.is_absolute():
            return raw_path.resolve(strict=False)
        candidates = (
            (Path.cwd() / raw_path).resolve(strict=False),
            (self._gpt_root / raw_path).resolve(strict=False),
            (self._project_root / raw_path).resolve(strict=False),
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[1]

    @staticmethod
    def _target_id(model_path: Path) -> str:
        """根据规范化路径生成稳定不透明目标 ID。"""
        digest = hashlib.sha256(str(model_path).encode("utf-8")).hexdigest()[:24]
        return f"live2d_{digest}"

    @staticmethod
    def _revision(model_path: Path) -> str:
        """根据文件状态生成用于幂等与重载的修订号。"""
        try:
            stat = model_path.stat()
            source = f"{model_path}:{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            source = f"{model_path}:missing"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]

    def _manifest(self, model_path: Path) -> _ManifestCacheEntry:
        """读取或复用与当前文件修订绑定的模型 manifest。"""
        stat = model_path.stat()
        cache_key = (str(model_path), stat.st_mtime_ns, stat.st_size)
        cached = self._manifest_cache.get(cache_key)
        if cached is not None:
            return cached

        self._ensure_gpt_import_path()
        from live2d_support.expression_policy import (
            SEMANTIC_EXPRESSION_CANDIDATES,
            select_expression_for_motion,
        )
        from live2d_support.runtime_adapter import detect_live2d_runtime_version

        with model_path.open("r", encoding="utf-8") as model_file:
            loaded = json.load(model_file)
        if not isinstance(loaded, dict):
            raise ValueError("Live2D 模型 JSON 顶层不是对象")
        model_data = cast(dict[str, object], loaded)
        version = detect_live2d_runtime_version(str(model_path))
        motion_files_by_group = self._motion_files(model_data, version)
        expression_ids = self._expression_ids(model_data, version)
        supported_expressions = frozenset(expression_ids)
        expressions_by_motion = {
            group_name: tuple(
                select_expression_for_motion(group_name, motion_file, supported_expressions)
                for motion_file in motion_files
            )
            for group_name, motion_files in motion_files_by_group.items()
        }
        semantic_expressions = {
            semantic: tuple(
                expression_id
                for expression_id in candidates
                if expression_id in supported_expressions
            )
            for semantic, candidates in SEMANTIC_EXPRESSION_CANDIDATES.items()
        }
        entry = _ManifestCacheEntry(
            version=version,
            capabilities=Live2DCapabilities(
                motion_files_by_group=motion_files_by_group,
                expression_ids=tuple(sorted(expression_ids)),
                expressions_by_motion=expressions_by_motion,
                semantic_expressions=semantic_expressions,
            ),
        )
        self._manifest_cache = {
            key: value
            for key, value in self._manifest_cache.items()
            if key[0] != str(model_path)
        }
        self._manifest_cache[cache_key] = entry
        return entry

    def _layout(self, model_path: Path, version: Live2DVersion) -> Live2DLayoutPresentation:
        """读取模型在单角色场景中的布局。"""
        self._ensure_gpt_import_path()
        from live2d_support.layout import get_live2d_layout

        layout = get_live2d_layout(str(model_path), version, "single")
        return Live2DLayoutPresentation(
            scale=layout.scale,
            offset_x=layout.offset_x,
            offset_y=layout.offset_y,
        )

    @staticmethod
    def _motion_files(model_data: dict[str, object], version: Live2DVersion) -> dict[str, tuple[str, ...]]:
        """从 v2/v3 模型 JSON 收集动作组和动作文件。"""
        motions: object = model_data.get("motions")
        if version == "v3":
            references = model_data.get("FileReferences")
            motions = references.get("Motions") if isinstance(references, dict) else None
        if not isinstance(motions, dict):
            return {}

        result: dict[str, tuple[str, ...]] = {}
        for raw_group_name, raw_entries in motions.items():
            if not isinstance(raw_group_name, str) or not isinstance(raw_entries, list):
                continue
            files: list[str] = []
            for raw_entry in raw_entries:
                if isinstance(raw_entry, str) and raw_entry:
                    files.append(raw_entry)
                    continue
                if not isinstance(raw_entry, dict):
                    continue
                raw_file = raw_entry.get("File")
                if not isinstance(raw_file, str):
                    raw_file = raw_entry.get("file")
                if isinstance(raw_file, str) and raw_file:
                    files.append(raw_file)
            if files:
                result[raw_group_name] = tuple(files)
        return result

    @staticmethod
    def _expression_ids(model_data: dict[str, object], version: Live2DVersion) -> set[str]:
        """从 v2/v3 模型 JSON 收集表情 ID。"""
        expressions: object = model_data.get("expressions")
        keys = ("name", "Name")
        if version == "v3":
            references = model_data.get("FileReferences")
            expressions = references.get("Expressions") if isinstance(references, dict) else None
            keys = ("Name", "name")
        if not isinstance(expressions, list):
            return set()

        result: set[str] = set()
        for raw_expression in expressions:
            if not isinstance(raw_expression, dict):
                continue
            for key in keys:
                expression_id = raw_expression.get(key)
                if isinstance(expression_id, str) and expression_id:
                    result.add(expression_id)
                    break
        return result

    def _ensure_gpt_import_path(self) -> None:
        """确保项目现有的顶层 GPT 模块可被安全复用。"""
        gpt_root = str(self._gpt_root)
        if gpt_root not in sys.path:
            sys.path.insert(0, gpt_root)

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        """判断路径是否位于指定可信目录中。"""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _configured_error(
        target_id: str,
        revision: str,
        code: str,
        message: str,
    ) -> Live2DPresentation:
        """创建不会触发默认模型回退的配置错误。"""
        return Live2DPresentation(
            resolution="configured_error",
            target_id=target_id,
            revision=revision,
            error=Live2DError(code=code, message=message, retryable=False),
        )
