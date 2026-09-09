from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TextIO

class TeeWriter:
    """将输出同时写入原始终端和日志文件。"""

    def __init__(self, primary: TextIO, log_file: TextIO) -> None:
        """保存终端输出流和日志输出流。"""

        self.primary = primary
        self.log_file = log_file

    def write(self, text: str) -> int:
        """写入一段文本到两个输出目标。"""

        self.primary.write(text)
        self.log_file.write(text)
        return len(text)

    def flush(self) -> None:
        """刷新两个输出目标。"""

        self.primary.flush()
        self.log_file.flush()


def wait_for_process_exit(pid: int, timeout: float = 60.0) -> None:
    """等待主程序进程退出，超时后抛出 RuntimeError。"""

    if pid <= 0:
        return
    try:
        import psutil
    except Exception:
        psutil = None

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if psutil is not None:
            if not psutil.pid_exists(pid):
                return
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return
        time.sleep(0.5)
    raise RuntimeError(f"等待进程退出超时：PID={pid}")


def setup_logging(log_file: Path | None) -> TextIO | None:
    """将更新过程输出同时写入终端和日志文件。"""

    if log_file is None:
        return None
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = log_file.open("a", encoding="utf-8")
    sys.stdout = TeeWriter(sys.stdout, handle)
    sys.stderr = TeeWriter(sys.stderr, handle)
    print(f"[日志] 更新日志：{log_file}")
    return handle


def parse_restart_command(command_text: str) -> list[str]:
    """解析重启命令，支持 JSON list 或普通字符串。"""

    text = command_text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(data, list):
        return [str(item) for item in data if str(item).strip()]
    if isinstance(data, str) and data.strip():
        return [data.strip()]
    raise RuntimeError("--restart-command 必须是 JSON list 或非空字符串")


def restart_app(command: list[str]) -> None:
    """更新成功后重启主程序。"""

    if not command:
        return
    subprocess.Popen(command, close_fds=os.name != "nt")
    print(f"[重启] 已启动：{command}")
