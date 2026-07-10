from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .update_models import DownloadedPatch
from .update_paths import get_update_log_dir, get_update_result_file


def launch_detached_process(command: list[str], app_root: Path) -> None:
    """按平台启动不会随主程序终端退出而终止的子进程。"""

    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            command,
            cwd=str(app_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
        return
    subprocess.Popen(
        command,
        cwd=str(app_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def build_apply_command(
    app_root: Path,
    package_dirs: list[Path] | tuple[Path, ...] | None,
    wait_pid: int | None,
    restart_command: list[str] | None,
    log_file: Path | None,
    status_file: Path | None = None,
) -> list[str]:
    """构造调用 tools/apply_update_patch.py 的命令。"""

    script = app_root / "tools" / "apply_update_patch.py"
    command = [
        sys.executable,
        str(script),
        "--app-root",
        str(app_root),
    ]
    for package_dir in package_dirs or ():
        command.extend(["--package", str(package_dir)])
    if wait_pid is not None:
        command.extend(["--wait-pid", str(wait_pid)])
    if restart_command:
        command.extend(["--restart-command", json.dumps(restart_command, ensure_ascii=False)])
    if log_file is not None:
        command.extend(["--log-file", str(log_file)])
    if status_file is not None:
        command.extend(["--status-file", str(status_file)])
    return command


def _make_log_file(downloaded_patches: list[DownloadedPatch], app_root: Path) -> Path:
    """根据补丁链生成日志文件路径。"""

    if downloaded_patches:
        base_version = downloaded_patches[0].patch.base_version
        target_version = downloaded_patches[-1].patch.target_version
    else:
        base_version = "unknown"
        target_version = "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = get_update_log_dir(app_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"update_{base_version}_to_{target_version}_{timestamp}.log"


def launch_update_process(
    downloaded_patches: list[DownloadedPatch],
    app_root: Path,
    main_pid: int,
    restart_command: list[str],
) -> None:
    """启动 updater 子进程，然后由 UI 触发主程序退出。"""

    log_file = _make_log_file(downloaded_patches, app_root)
    status_file = get_update_result_file(app_root)
    package_dirs = [downloaded.package_dir for downloaded in downloaded_patches]
    command = build_apply_command(app_root, package_dirs, main_pid, restart_command, log_file, status_file)
    launch_detached_process(command, app_root)


def build_restart_command(app_root: Path) -> list[str]:
    """按平台返回更新成功后的重启命令。"""

    if sys.platform == "darwin":
        launcher = app_root / "运行主程序.command"
        if launcher.exists():
            return ["/bin/bash", str(launcher)]
    if sys.platform == "win32":
        launcher = app_root / "run.bat"
        if launcher.exists():
            return ["cmd", "/c", str(launcher)]
    return [sys.executable, str(app_root / "GPT_SoVITS" / "main2.py")]
