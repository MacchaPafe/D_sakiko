"""为 Stage2 数据集复核编辑器创建独立 PyInstaller 发布目录。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


PUBLISH_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PUBLISH_ROOT.parent
EDITOR_ENTRY = (
    REPOSITORY_ROOT
    / "GPT_SoVITS"
    / "rag"
    / "pipeline"
    / "stage2_dataset_editor.py"
)
VENV_ROOT = PUBLISH_ROOT / ".venv"
DIST_ROOT = PUBLISH_ROOT / "dist"
WORK_ROOT = PUBLISH_ROOT / "build"
CACHE_ROOT = PUBLISH_ROOT / ".pyinstaller-cache"
APP_NAME = "Stage2DatasetEditor"


def expected_venv_python() -> Path:
    """返回当前平台中隔离虚拟环境的 Python 路径。"""

    relative_path = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return (VENV_ROOT / relative_path).absolute()


def ensure_build_environment() -> None:
    """确认构建脚本正在 publish_pipeline 的隔离环境中运行。"""

    expected_python = expected_venv_python()
    if Path(sys.prefix).resolve() != VENV_ROOT.resolve():
        raise SystemExit(
            "请使用 publish_pipeline/.venv 中的 Python 运行本脚本：\n"
            f"  {expected_python} {Path(__file__).resolve()}"
        )
    missing_packages = [
        package_name
        for package_name in ("nicegui", "pydantic", "PyInstaller")
        if find_spec(package_name) is None
    ]
    if missing_packages:
        raise SystemExit(
            "隔离环境缺少构建依赖："
            + "、".join(missing_packages)
            + "\n请先安装 publish_pipeline/requirements.txt。"
        )


def nicegui_package_dir() -> Path:
    """返回隔离环境中 NiceGUI 的静态资源目录。"""

    spec = find_spec("nicegui")
    if spec is None or spec.origin is None:
        raise SystemExit("无法定位 NiceGUI 安装目录。")
    return Path(spec.origin).resolve().parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析构建脚本参数。"""

    parser = argparse.ArgumentParser(description="构建 Stage2 数据集复核编辑器")
    parser.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="JSON",
        help="复制到发布目录 data/ 的 Stage2 输入文件；可以重复传入",
    )
    parser.add_argument(
        "--clean",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="构建前清理 PyInstaller 缓存（默认启用）",
    )
    return parser.parse_args(argv)


def validate_data_paths(values: list[str]) -> list[Path]:
    """校验并解析要随发布目录复制的 JSON 文件。"""

    paths: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".json":
            raise SystemExit(f"--data 必须指向现有 JSON 文件：{path}")
        paths.append(path)
    return paths


def build_command(clean: bool) -> list[str]:
    """生成只使用当前隔离环境的 PyInstaller 命令。"""

    command = [
        str(expected_venv_python()),
        "-m",
        "PyInstaller",
        "--name",
        APP_NAME,
        "--onedir",
        "--noconfirm",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(WORK_ROOT),
        "--specpath",
        str(PUBLISH_ROOT),
        "--paths",
        str(EDITOR_ENTRY.parent),
        "--add-data",
        f"{nicegui_package_dir()}{os.pathsep}nicegui",
    ]
    if clean:
        command.append("--clean")
    command.append(str(EDITOR_ENTRY))
    return command


def build_environment() -> dict[str, str]:
    """让 PyInstaller 缓存完全位于独立发布工作区。"""

    environment = dict(os.environ)
    environment["PYINSTALLER_CONFIG_DIR"] = str(CACHE_ROOT)
    return environment


def copy_distribution_data(data_paths: list[Path]) -> Path:
    """建立发布数据目录并复制指定的待复核文件。"""

    app_dir = DIST_ROOT / APP_NAME
    if not app_dir.is_dir():
        raise RuntimeError(f"PyInstaller 未生成预期发布目录：{app_dir}")
    data_dir = app_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for source_path in data_paths:
        shutil.copy2(source_path, data_dir / source_path.name)
    return app_dir


def main(argv: list[str] | None = None) -> None:
    """执行隔离构建并输出发布目录位置。"""

    args = parse_args(argv)
    ensure_build_environment()
    data_paths = validate_data_paths(args.data)
    subprocess.run(
        build_command(clean=args.clean),
        cwd=PUBLISH_ROOT,
        env=build_environment(),
        check=True,
    )
    app_dir = copy_distribution_data(data_paths)
    print(f"构建完成：{app_dir}")
    if data_paths:
        print(f"已复制 {len(data_paths)} 个 Stage2 JSON 到：{app_dir / 'data'}")
    else:
        print(f"请在发送前把 Stage2 JSON 放入：{app_dir / 'data'}")


if __name__ == "__main__":
    main()
