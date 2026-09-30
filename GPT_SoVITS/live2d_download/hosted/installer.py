"""V3 云端模型安装：隔离解包、引用平铺、规范化后再发布。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from live2d_support.model_normalizer import MODEL3_ASSET_REFERENCE_KEYS, normalize_model3_for_project
from live2d_support.performance_catalog import load_performance_catalog, motion_assets, performance_config_path
from .catalog import CHARACTERS, PROJECT_ROOT
from .service import DownloadError, check_cancel


@dataclass(frozen=True)
class InstallResult:
    """向界面报告安装目录及角色是否还需补齐描述。"""
    path: Path
    new_character: bool
    description_missing: bool


def _read_json(path):
    """读取对象 JSON，拒绝损坏配置。"""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise DownloadError("模型包中的 JSON 配置格式不正确")
    return data


def _write_json(path, data):
    """以 UTF-8 保存重写后的模型和演出配置。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def _safe_parts(name):
    """校验 ZIP 内相对路径，避免越界、重名设备和 Windows 路径歧义。"""
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    devices = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    if not parts or name.startswith("/") or any(
            p in {".", ".."} or re.search(r'[<>:"|?*\x00-\x1f]', p) or p.endswith((".", " "))
            or p.split(".")[0].upper() in devices for p in parts):
        raise DownloadError("模型包包含不安全的文件路径")
    return parts


def _extract(archive, destination, cancel):
    """逐块解压本次任务文件，支持取消并检查重复路径及解包上限。"""
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 2 * 1024 ** 3:
            raise DownloadError("模型包解压后的文件数量或大小超过限制")
        seen = set()
        for entry in entries:
            check_cancel(cancel)
            parts = _safe_parts(entry.filename)
            if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
                continue
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                raise DownloadError("模型包不支持链接文件或加密文件")
            if entry.is_dir():
                continue
            key = "/".join(parts).casefold()
            if key in seen:
                raise DownloadError("模型包包含重复文件路径")
            seen.add(key)
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open("xb") as output:
                while True:
                    check_cancel(cancel)
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)


def _flatten(source, destination, cancel):
    """平铺完整模型资源并同步重写引用，保留独立演出的逻辑 ID。"""
    files = sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    models = [p for p in files if p.name.lower().endswith(".model3.json")]
    if len(models) != 1:
        raise DownloadError("模型包必须包含唯一的 .model3.json 文件")
    if any(p.name.lower().endswith(".model.json") for p in files):
        raise DownloadError("V3 模型包不能混入 V2 模型配置")
    model = models[0]
    data = _read_json(model)
    refs = data.get("FileReferences")
    if not isinstance(refs, dict) or not refs.get("Moc") or not refs.get("Textures"):
        raise DownloadError("模型包缺少 Moc 或贴图引用")
    if not isinstance(refs["Textures"], list):
        raise DownloadError("模型贴图引用格式不正确")
    descriptions = [p for p in files if p.name.lower() == "character_description.txt"]
    if len(descriptions) > 1:
        raise DownloadError("模型包中包含多份角色描述，无法确定使用哪一份")
    description = descriptions[0].read_text(encoding="utf-8-sig") if descriptions else None

    # 补入包内未登记的动作，避免安装后完整演出目录丢失资源。
    assets = [dict(entry) for entry in motion_assets(data)]
    known = {(model.parent / str(entry["File"]).replace("\\", "/")).resolve() for entry in assets}
    for file in files:
        if file.name.lower().endswith(".motion3.json") and file.resolve() not in known:
            if not isinstance(_read_json(file).get("Curves"), list):
                raise DownloadError("模型包中的动作文件格式不正确")
            assets.append({"File": os.path.relpath(file, model.parent).replace("\\", "/")})
    metadata = data.setdefault("DSakiko", {})
    if not isinstance(metadata, dict):
        raise DownloadError("模型包中的 DSakiko 配置格式不正确")
    metadata["MotionAssets"] = assets
    _write_json(model, data)
    original = load_performance_catalog(model)
    logical_ids = {asset.file: key for key, variants in original.motions.items() for asset in variants.values()}
    mapping, used = {}, set()
    for file in files:
        if file in descriptions:
            continue
        check_cancel(cancel)
        name = file.name
        if file == model:
            name = name[:-len(".model3.json")] + ".model3.json"
        if name.casefold() in used:
            prefix = hashlib.sha256(file.parent.relative_to(source).as_posix().encode()).hexdigest()[:8]
            name = prefix + "_" + name
            while name.casefold() in used:
                name = "asset_" + name
        used.add(name.casefold())
        mapping[file.resolve()] = name
        shutil.copy2(file, destination / name)

    def rewrite(value):
        """只允许引用本次解包内已有文件，并映射到平铺后的名称。"""
        if not isinstance(value, str) or not value:
            raise DownloadError("模型资源引用不能为空")
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            raise DownloadError("模型包不能引用外部绝对路径")
        target = (model.parent / normalized).resolve()
        if target not in mapping:
            raise DownloadError(f"模型包缺少引用的资源：{value}")
        return mapping[target]

    for key in MODEL3_ASSET_REFERENCE_KEYS:
        if key in refs:
            refs[key] = rewrite(refs[key])
    refs["Textures"] = [rewrite(file) for file in refs["Textures"]]
    expressions = refs.setdefault("Expressions", [])
    if not isinstance(expressions, list):
        raise DownloadError("模型表情列表格式不正确")
    for entry in expressions:
        entry["File"] = rewrite(entry["File"])
    for entry in assets:
        original_file = entry["File"]
        if original_file in logical_ids:
            entry["LogicalId"] = logical_ids[original_file]
        entry["File"] = rewrite(original_file)
        if "Sound" in entry:
            entry["Sound"] = rewrite(entry["Sound"])
    for entries in refs.get("Motions", {}).values():
        for entry in entries:
            for key in ("File", "Sound"):
                if key in entry:
                    entry[key] = rewrite(entry[key])
    target_model = destination / mapping[model.resolve()]
    _write_json(target_model, data)
    sidecar = performance_config_path(model)
    config = _read_json(sidecar) if sidecar.exists() else {}
    shared = original.shared.get("series", {}).get(original.series, {})
    for kind in ("motions", "expressions"):
        if isinstance(shared.get(kind), dict):
            config[kind] = {**shared[kind], **config.get(kind, {})}
            config.setdefault("series", original.series)
    if config:
        if isinstance(config.get("bindings"), dict):
            config["bindings"] = {rewrite(file): value for file, value in config["bindings"].items()}
        if isinstance(config.get("custom_expressions"), dict):
            config["custom_expressions"] = {key: rewrite(file) for key, file in config["custom_expressions"].items()}
        _write_json(performance_config_path(target_model), config)
    normalize_model3_for_project(str(target_model), downloaded=True)
    return target_model, description


class V3Installer:
    """仅处理托管 V3 安装，不调用或改变旧 V2 安装器。"""
    def __init__(self, service, project_root=PROJECT_ROOT):
        """保存下载服务和项目根目录，便于隔离测试安装。"""
        self.service = service
        self.project_root = Path(project_root)

    def _target(self, resource, selection):
        """校验来源和接收角色，并拒绝覆盖已有的新角色。"""
        if resource.kind != "model" or resource.character != selection.source or selection.source not in CHARACTERS:
            raise DownloadError("模型来源与当前选择不一致")
        if selection.mode not in {"new", "existing"}:
            raise DownloadError("请选择模型安装用途")
        character_id = selection.source if selection.mode == "new" else selection.target_id
        if not character_id or _safe_parts(character_id) != (character_id,):
            raise DownloadError("接收角色标识不合法")
        root = self.project_root / "live2d_related"
        character = root / character_id
        if character.is_symlink():
            raise DownloadError("接收角色目录不能是链接")
        if selection.mode == "new":
            display_name = CHARACTERS[selection.source]["display_name"]
            if character.exists() or any(p.read_text(encoding="utf-8-sig").strip() == display_name
                                        for p in root.glob("*/name.txt")):
                raise DownloadError("该角色已存在，请使用“为已有角色添加服装”入口")
        elif not character.is_dir():
            raise DownloadError("接收角色目录不存在，请重新选择")
        return character

    def download(self, resource, selection, cache_root, cancel, progress):
        """下载后立即安装，成功、失败或取消均清理本次临时包。"""
        self._target(resource, selection)
        Path(cache_root).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="v3-", dir=str(cache_root)) as folder:
            archive = self.service.download(resource, Path(folder), cancel, progress)
            check_cancel(cancel)
            return self.install_archive(archive, resource, selection, cancel)

    def install_archive(self, archive, resource, selection, cancel):
        """在同盘临时目录完整验证后发布，失败不留下半成品角色或模型。"""
        check_cancel(cancel)
        character = self._target(resource, selection)
        root = character.parent
        root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix=".v3-install-", dir=str(root)) as stage:
                stage = Path(stage)
                extracted, model_dir = stage / "unpacked", stage / "model"
                extracted.mkdir()
                model_dir.mkdir()
                _extract(archive, extracted, cancel)
                _, description = _flatten(extracted, model_dir, cancel)
                check_cancel(cancel)
                self._target(resource, selection)
                if selection.mode == "new":
                    ready = stage / "character"
                    ready.mkdir()
                    (ready / "name.txt").write_text(CHARACTERS[selection.source]["display_name"], encoding="utf-8")
                    if description is not None:
                        (ready / "character_description.txt").write_text(description, encoding="utf-8")
                    model_dir.rename(ready / "live2D_model")
                    check_cancel(cancel)
                    ready.rename(character)
                    return InstallResult(character, True, not bool(description and description.strip()))

                default = character / "live2D_model"
                has_default = any(p.name.lower().endswith((".model.json", ".model3.json")) for p in default.rglob("*.json"))
                parent = character / ("extra_model" if has_default else "live2D_model")
                if parent.is_symlink():
                    raise DownloadError("模型安装目录不能是链接")
                parent.mkdir(exist_ok=True)
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", resource.title).strip(". ")[:100] or resource.model_id
                try:
                    _safe_parts(name)
                except DownloadError:
                    name = "model"
                target, index = parent / name, 2
                while target.exists():
                    target = parent / f"{name}_{index}"
                    index += 1
                description_path = character / "character_description.txt"
                copied_description = False
                try:
                    # 给其他身份换装时不把来源角色的人设写入接收角色。
                    same_character = selection.target_id == selection.source or (
                        (character / "name.txt").is_file() and
                        (character / "name.txt").read_text(encoding="utf-8-sig").strip() == CHARACTERS[selection.source]["display_name"])
                    if same_character and description is not None and not description_path.exists():
                        with description_path.open("x", encoding="utf-8") as output:
                            copied_description = True
                            output.write(description)
                    missing = not description_path.is_file() or not description_path.read_text(encoding="utf-8-sig").strip()
                    check_cancel(cancel)
                    model_dir.rename(target)
                except Exception:
                    if copied_description:
                        description_path.unlink()
                    raise
                return InstallResult(target, False, missing)
        except DownloadError:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as error:
            raise DownloadError("V3 模型解包或安装失败，请检查模型包后重试") from error
