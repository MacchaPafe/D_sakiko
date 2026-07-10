from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from repair.repair_paths import get_repair_log_dir, get_repair_result_file
from update.update_launcher import launch_detached_process


def build_apply_repair_command(
    app_root: Path,
    plan_file: Path,
    wait_pid: int | None,
    restart_command: list[str] | None,
    log_file: Path,
    status_file: Path,
) -> list[str]:
    """构造独立修复执行器命令。"""

    command = [
        sys.executable,
        str(app_root / "tools" / "apply_repair.py"),
        "--app-root",
        str(app_root),
        "--plan",
        str(plan_file),
        "--log-file",
        str(log_file),
        "--status-file",
        str(status_file),
    ]
    if wait_pid is not None:
        command.extend(["--wait-pid", str(wait_pid)])
    if restart_command:
        command.extend(["--restart-command", json.dumps(restart_command, ensure_ascii=False)])
    return command


def launch_repair_process(
    app_root: Path,
    plan_file: Path,
    main_pid: int,
    restart_command: list[str],
) -> None:
    """启动 detached 修复进程，由调用方随后关闭主程序。"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_dir = get_repair_log_dir(app_root)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"repair_{timestamp}.log"
    command = build_apply_repair_command(
        app_root,
        plan_file,
        main_pid,
        restart_command,
        log_file,
        get_repair_result_file(app_root),
    )
    launch_detached_process(command, app_root)
