from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices


def show_file_in_manager(file_path: str | Path | None) -> None:
    """在系统文件管理器中定位文件，无法选中时至少打开其所在目录。"""

    if file_path is None:
        return

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return

        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
            return
    except OSError:
        pass

    directory = path if path.is_dir() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
