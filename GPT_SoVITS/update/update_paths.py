from __future__ import annotations

from pathlib import Path


def get_app_root() -> Path:
    """返回程序根目录，即包含 version.json 和 tools/ 的目录。"""

    return Path(__file__).resolve().parents[2]


def get_version_file(app_root: Path | None = None) -> Path:
    """返回 version.json 路径。"""

    root = app_root if app_root is not None else get_app_root()
    return root / "version.json"


def get_update_root(app_root: Path | None = None) -> Path:
    """返回 .updates 目录。"""

    root = app_root if app_root is not None else get_app_root()
    return root / ".updates"


def get_download_dir(version: str, app_root: Path | None = None) -> Path:
    """返回指定目标版本的下载目录。"""

    return get_update_root(app_root) / "downloads" / version


def get_package_dir(version: str, app_root: Path | None = None) -> Path:
    """返回指定目标版本的解压目录。"""

    return get_update_root(app_root) / "packages" / version


def get_update_log_dir(app_root: Path | None = None) -> Path:
    """返回 logs/update 目录。"""

    root = app_root if app_root is not None else get_app_root()
    return root / "logs" / "update"


def get_update_result_file(app_root: Path | None = None) -> Path:
    """返回主程序读取的最近一次更新结果记录路径。"""

    return get_update_log_dir(app_root) / "last_update_result.json"
