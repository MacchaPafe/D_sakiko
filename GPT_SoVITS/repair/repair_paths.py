from __future__ import annotations

from datetime import datetime
from pathlib import Path

from update.update_paths import get_app_root, get_update_root


def get_repair_root(app_root: Path | None = None) -> Path:
    """返回修复缓存根目录。"""

    return get_update_root(app_root) / "repair"


def get_repair_object_cache(app_root: Path | None = None) -> Path:
    """返回已校验修复 object 的缓存目录。"""

    return get_repair_root(app_root) / "objects"


def get_repair_staging_root(app_root: Path | None = None) -> Path:
    """返回修复 staging 根目录。"""

    return get_repair_root(app_root) / "staging"


def get_repair_plan_dir(app_root: Path | None = None) -> Path:
    """返回 repair plan 保存目录。"""

    return get_repair_root(app_root) / "plans"


def get_repair_backup_root(app_root: Path | None = None) -> Path:
    """返回修复备份根目录。"""

    return get_repair_root(app_root) / "backups"


def get_repair_log_dir(app_root: Path | None = None) -> Path:
    """返回可见的修复日志目录。"""

    root = app_root if app_root is not None else get_app_root()
    return root / "logs" / "repair"


def get_repair_result_file(app_root: Path | None = None) -> Path:
    """返回主程序读取的最近修复结果记录。"""

    return get_repair_log_dir(app_root) / "last_repair_result.json"


def make_repair_run_id(version: str, now: datetime | None = None) -> str:
    """生成不会在正常重复修复中冲突的运行标识。"""

    current = now or datetime.now()
    return f"repair_{version}_{current.strftime('%Y%m%d_%H%M%S_%f')}"
