"""自定义表情的标准文件、所有权和可恢复保存。"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from live2d_support.performance_catalog import (
    cubism_motion_json, object_mapping, performance_config_path,
)


@dataclass(frozen=True)
class ExpressionParameter:
    """记录运行时提供的真实参数范围和显示名称。"""

    id: str
    name: str
    minimum: float
    maximum: float
    default: float

    def payload(self) -> dict[str, object]:
        """生成可跨进程传递的数据。"""
        return asdict(self)


def read_document(path: Path, optional: bool = False) -> dict[str, object]:
    """严格读取对象，拒绝覆盖损坏的配置。"""
    if optional and not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置必须是 JSON 对象：{path.name}")
    return data


def expression_document(parameters: list[dict[str, object]], fade_in: float, fade_out: float,
                        specs: list[ExpressionParameter]) -> dict[str, object]:
    """校验混合操作和实际模型范围，生成标准 Cubism 表情。"""
    if not all(math.isfinite(value) and 0 <= value <= 60 for value in (fade_in, fade_out)):
        raise ValueError("淡入淡出时间必须在 0 到 60 秒之间")
    by_id = {item.id: item for item in specs}
    seen: set[str] = set()
    entries: list[dict[str, object]] = []
    for entry in parameters:
        key, blend = str(entry.get("Id", "")), str(entry.get("Blend", "Add"))
        if key not in by_id or key in seen:
            raise ValueError(f"参数缺失或重复：{key}")
        if blend not in {"Add", "Multiply", "Overwrite"}:
            raise ValueError(f"不支持的混合方式：{blend}")
        value = float(entry["Value"])
        if not math.isfinite(value):
            raise ValueError(f"参数值必须是有限数值：{key}")
        spec = by_id[key]
        target = value if blend == "Overwrite" else spec.default + value if blend == "Add" else spec.default * value
        if not spec.minimum - 1e-6 <= target <= spec.maximum + 1e-6:
            raise ValueError(f"参数超出模型范围：{key}")
        entries.append({"Id": key, "Value": value, "Blend": blend})
        seen.add(key)
    return {"Type": "Live2D Expression", "FadeInTime": fade_in, "FadeOutTime": fade_out,
            "Parameters": entries}


def stage_bytes(path: Path, content: bytes) -> Path:
    """在目标目录预写完整内容，返回可原子替换的临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".expression-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def commit_files(changes: dict[Path, bytes | None]) -> None:
    """提交关联文件，普通写入失败时恢复已经改动的文件。"""
    backups: dict[Path, Path | None] = {}
    staged: dict[Path, Path | None] = {}
    applied: list[Path] = []
    keep_backups = False
    try:
        # 先完成全部磁盘写入，提交和回滚都只重命名，避免磁盘已满时无法恢复。
        for path, content in changes.items():
            backups[path] = stage_bytes(path, path.read_bytes()) if path.exists() else None
            staged[path] = stage_bytes(path, content) if content is not None else None
        for path, temporary in staged.items():
            if temporary is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(temporary, path)
            applied.append(path)
    except OSError as error:
        failed: list[str] = []
        for path in reversed(applied):
            backup = backups[path]
            try:
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(backup, path)
            except OSError:
                failed.append(f"{path}（备份：{backup}）")
        if failed:
            keep_backups = True
            raise OSError("保存失败，部分文件无法自动恢复；已保留备份：\n" + "\n".join(failed)) from error
        raise
    finally:
        leftovers = list(staged.values()) + ([] if keep_backups else list(backups.values()))
        for temporary in leftovers:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class CustomExpressionStore:
    """集中管理一个模型的自定义资源，不修改自带表情。"""

    def __init__(self, model_path: Path) -> None:
        """固定模型入口及其独立配置位置。"""
        self.model_path = model_path.resolve()
        self.config_path = performance_config_path(self.model_path)

    def documents(self) -> tuple[dict[str, object], dict[str, object]]:
        """读取最新数据，保留外部修改和未知字段。"""
        model = read_document(self.model_path)
        if not isinstance(model.get("FileReferences"), dict):
            raise ValueError("自定义表情仅支持 V3 模型")
        return model, read_document(self.config_path, optional=True)

    def owned(self) -> dict[str, object]:
        """返回显式登记的自定义表情，不能按文件名推断所有权。"""
        return object_mapping(self.documents()[1].get("custom_expressions"))

    def target(self, name: str) -> Path:
        """为不同模型入口分配独立目录并阻止越界写入。"""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name.lower() == "auto":
            raise ValueError("英文名称须以字母开头，仅含字母、数字和下划线，最长 64 字符，且不能为 auto")
        path = self.model_path.parent / "expressions" / "custom" / self.model_path.stem / f"{name}.exp3.json"
        if not path.resolve().is_relative_to(self.model_path.parent):
            raise ValueError("表情保存位置不能超出模型目录")
        return path

    def save(self, name: str, description: str, document: dict[str, object],
             specs: list[ExpressionParameter], existing: bool = False) -> None:
        """保存资源、标准引用和 AI 描述，失败时回滚关联文件。"""
        target = self.target(name)
        if not description.strip():
            raise ValueError("请填写供 AI 选择的中文描述")
        parameters = document.get("Parameters")
        if not isinstance(parameters, list) or not parameters:
            raise ValueError("请至少选择一个参数")
        normalized = expression_document(parameters, float(document.get("FadeInTime", 0.5)),
                                         float(document.get("FadeOutTime", 0.5)), specs)
        model, config = self.documents()
        refs = object_mapping(model["FileReferences"])
        entries = list(refs.get("Expressions", []))
        owners = object_mapping(config.get("custom_expressions"))
        relative = target.relative_to(self.model_path.parent).as_posix()
        matching = [entry for entry in entries if isinstance(entry, dict) and str(entry.get("Name", "")).casefold() == name.casefold()]
        if existing:
            if owners.get(name) != relative or len(matching) != 1 or matching[0].get("File") != relative:
                raise ValueError("该表情不是本编辑器拥有的资源，或引用已被外部修改")
        elif matching or name in owners or target.exists():
            raise ValueError("此英文名称或目标文件已存在，请换一个名称")
        else:
            entries.append({"Name": name, "File": relative})
        refs["Expressions"] = entries
        model["FileReferences"] = refs
        owners[name] = relative
        config.setdefault("schema_version", 1)
        config["custom_expressions"] = owners
        descriptions = object_mapping(config.get("expressions"))
        descriptions[name] = description.strip()
        config["expressions"] = descriptions
        fingerprints = object_mapping(config.get("fingerprints"))
        recorded = object_mapping(fingerprints.get("expressions"))
        recorded.pop(name, None)
        fingerprints["expressions"] = recorded
        config["fingerprints"] = fingerprints
        commit_files({target: cubism_motion_json(normalized).encode("utf-8"),
                      self.config_path: cubism_motion_json(config).encode("utf-8"),
                      self.model_path: cubism_motion_json(model).encode("utf-8")})

    def referenced_presets(self, name: str) -> list[str]:
        """列出将失效的组合，保留预设供用户修复。"""
        presets = self.documents()[1].get("presets", [])
        return [str(item.get("name") or item.get("id") or "未命名组合") for item in presets
                if isinstance(item, dict) and item.get("expression") == name]

    def delete(self, name: str) -> None:
        """只删除确认归属的资源；失效组合继续留在编辑器中。"""
        target = self.target(name)
        model, config = self.documents()
        owners = object_mapping(config.get("custom_expressions"))
        relative = target.relative_to(self.model_path.parent).as_posix()
        refs = object_mapping(model["FileReferences"])
        entries = list(refs.get("Expressions", []))
        matching = [entry for entry in entries if isinstance(entry, dict) and entry.get("Name") == name]
        if owners.get(name) != relative or len(matching) != 1 or matching[0].get("File") != relative:
            raise ValueError("只能删除本编辑器创建且引用未改变的自定义表情")
        remaining = [entry for entry in entries if entry not in matching]
        if any(isinstance(entry, dict) and entry.get("File") == relative for entry in remaining):
            raise ValueError("文件还被其他表情引用，无法删除")
        refs["Expressions"] = remaining
        model["FileReferences"] = refs
        owners.pop(name)
        config["custom_expressions"] = owners
        for key in ("expressions",):
            values = object_mapping(config.get(key))
            values.pop(name, None)
            config[key] = values
        fingerprints = object_mapping(config.get("fingerprints"))
        recorded = object_mapping(fingerprints.get("expressions"))
        recorded.pop(name, None)
        fingerprints["expressions"] = recorded
        config["fingerprints"] = fingerprints
        commit_files({self.model_path: cubism_motion_json(model).encode("utf-8"),
                      self.config_path: cubism_motion_json(config).encode("utf-8"), target: None})
