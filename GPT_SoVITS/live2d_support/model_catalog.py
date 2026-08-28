from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .model_version import Live2DVersion, detect_live2d_runtime_version


Live2DModelKind = Literal["default", "extra"]


@dataclass(frozen=True)
class Live2DModelOption:
    """描述一个角色可选的 Live2D 模型。"""

    option_id: str
    display_name: str
    kind: Live2DModelKind
    model_json_path: Path
    model_directory: Path
    available: bool
    version: Live2DVersion | None
    error_message: str | None

    @property
    def is_default(self) -> bool:
        """返回该选项是否为角色默认模型。"""
        return self.kind == "default"


class Live2DModelCatalog:
    """统一枚举角色默认模型与额外服装。"""

    def __init__(self, live2d_root: Path, project_root: Path | None = None) -> None:
        """设置可信 Live2D 根目录和项目根目录。"""
        self._live2d_root = live2d_root.resolve()
        self._project_root = (project_root or self._live2d_root.parent).resolve()

    def list_options(self, character_folder_name: str) -> tuple[Live2DModelOption, ...]:
        """列出指定角色的默认模型和额外服装。"""
        character_root = self._character_root(character_folder_name)
        if character_root is None:
            return ()

        options: list[Live2DModelOption] = []
        default_option = self._option_from_directory(
            character_folder_name,
            "默认",
            "default",
            character_root / "live2D_model",
        )
        if default_option is not None:
            options.append(default_option)

        extra_root = character_root / "extra_model"
        if extra_root.is_dir():
            extra_directories = sorted(
                (path for path in extra_root.iterdir() if path.is_dir()),
                key=lambda path: self._natural_sort_key(path.name),
            )
            for model_directory in extra_directories:
                option = self._option_from_directory(
                    character_folder_name,
                    model_directory.name,
                    "extra",
                    model_directory,
                )
                if option is not None:
                    options.append(option)
        return tuple(options)

    def find_option(self, character_folder_name: str, option_id: str) -> Live2DModelOption | None:
        """根据不透明标识查找当前仍存在的模型选项。"""
        return next(
            (
                option
                for option in self.list_options(character_folder_name)
                if option.option_id == option_id
            ),
            None,
        )

    def find_by_path(
        self,
        character_folder_name: str,
        configured_path: str | Path,
    ) -> Live2DModelOption | None:
        """根据存档中的绝对或相对路径匹配模型选项。"""
        candidates = self._configured_path_candidates(configured_path)
        return next(
            (
                option
                for option in self.list_options(character_folder_name)
                if option.model_json_path in candidates
            ),
            None,
        )

    def resolve_configured_path(self, configured_path: str | Path) -> Path:
        """将存档中的模型路径解析为稳定绝对路径。"""
        candidates = self._configured_path_candidates(configured_path)
        return next((path for path in candidates if path.exists()), candidates[0])

    def _character_root(self, character_folder_name: str) -> Path | None:
        """返回经边界检查的角色根目录。"""
        if not character_folder_name or Path(character_folder_name).name != character_folder_name:
            return None
        character_root = (self._live2d_root / character_folder_name).resolve()
        if not character_root.is_relative_to(self._live2d_root) or not character_root.is_dir():
            return None
        return character_root

    def _option_from_directory(
        self,
        character_folder_name: str,
        display_name: str,
        kind: Live2DModelKind,
        model_directory: Path,
    ) -> Live2DModelOption | None:
        """从一个模型目录生成优先使用 v3 的选项。"""
        if not model_directory.is_dir():
            return None
        model3_paths = sorted(model_directory.rglob("*.model3.json"))
        model2_paths = sorted(model_directory.rglob("*.model.json"))
        candidates = model3_paths or model2_paths
        if not candidates:
            return None
        model_path = candidates[0].resolve()
        if not model_path.is_relative_to(model_directory.resolve()):
            return None

        version: Live2DVersion | None = None
        error_message: str | None = None
        try:
            with model_path.open("r", encoding="utf-8") as model_file:
                model_data = json.load(model_file)
            if not isinstance(model_data, dict):
                raise ValueError("模型 JSON 顶层不是对象")
            version = detect_live2d_runtime_version(str(model_path))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            error_message = str(error) or "模型 JSON 无法解析"

        return Live2DModelOption(
            option_id=self._option_id(character_folder_name, kind, model_path),
            display_name=display_name,
            kind=kind,
            model_json_path=model_path,
            model_directory=model_directory.resolve(),
            available=error_message is None,
            version=version,
            error_message=error_message,
        )

    def _configured_path_candidates(self, configured_path: str | Path) -> tuple[Path, ...]:
        """生成兼容旧存档相对路径语义的候选路径。"""
        raw_path = Path(configured_path).expanduser()
        if raw_path.is_absolute():
            return (raw_path.resolve(),)
        paths = (
            (Path.cwd() / raw_path).resolve(),
            (self._project_root / raw_path).resolve(),
            (self._project_root / "GPT_SoVITS" / raw_path).resolve(),
        )
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _option_id(
        character_folder_name: str,
        kind: Live2DModelKind,
        model_path: Path,
    ) -> str:
        """生成不暴露主机路径的稳定选项标识。"""
        identity = f"{character_folder_name}\0{kind}\0{model_path}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f"live2d_option_{digest}"

    @staticmethod
    def _natural_sort_key(value: str) -> tuple[tuple[int, int | str], ...]:
        """把名称转换为支持数字的稳定自然排序键。"""
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", value)
            if part
        )
