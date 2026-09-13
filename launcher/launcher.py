"""图形启动器入口；即使 GUI 依赖缺失，也保留可见提示和日志。"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "launcher"))
    try:
        from launcher_ui import run
        return run(root)
    except Exception:
        detail = traceback.format_exc()
        log_path = root / "logs/launcher/launcher_error.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(detail, encoding="utf-8")
            message = f"启动器打开失败。请检查软件包中的 Python、PyQt5 和界面依赖是否完整。\n\n详情：{log_path}"
        except OSError:
            message = "启动器打开失败，且日志目录写入失败。请检查软件包是否完整以及目录写入权限。\n\n" + detail
        if sys.stderr is not None:
            print(detail, file=sys.stderr)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "数字小祥 · 启动器", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
