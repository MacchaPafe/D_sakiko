"""Launch the configured Saki renderer and Python application together."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "d_sakiko_config.json"


def renderer_mode() -> str:
    override = os.environ.get("DSAKIKO_RENDERER", "").strip().lower()
    if override in {"electron", "pygame"}:
        return override
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as stream:
            configured = str(json.load(stream).get("ui_state", {}).get("live2d_renderer", "pygame")).lower()
            return configured if configured in {"pygame", "electron"} else "pygame"
    except (OSError, json.JSONDecodeError, AttributeError):
        # Pygame is the compatibility renderer and remains the conservative
        # choice when no explicit renderer has been configured.
        return "pygame"


def wait_for_bridge(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for port in (9876, 9877):
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
            return True
        except OSError:
            time.sleep(0.2)
    return False


def electron_command(electron_root: Path) -> Path:
    """Return the local Electron launcher for the current platform."""
    name = "electron.cmd" if os.name == "nt" else "electron"
    return electron_root / "node_modules" / ".bin" / name


def main() -> int:
    python_executable = Path(sys.executable)
    main_script = ROOT / "GPT_SoVITS" / "main2.py"
    electron_root = ROOT / "electron_frontend"
    mode = renderer_mode()
    python_process = subprocess.Popen([str(python_executable), str(main_script)], cwd=main_script.parent)
    electron_process = None
    try:
        if mode == "electron":
            if not wait_for_bridge():
                print("Bridge 在 30 秒内未就绪，未启动 Electron。", file=sys.stderr)
                return 1
            electron_executable = electron_command(electron_root)
            if not electron_executable.is_file():
                print(f"Electron 依赖不存在：{electron_executable}", file=sys.stderr)
                return 1
            electron_process = subprocess.Popen([str(electron_executable), "."], cwd=electron_root)
        return python_process.wait()
    finally:
        if python_process.poll() is None:
            python_process.terminate()
        if electron_process is not None and electron_process.poll() is None:
            electron_process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
