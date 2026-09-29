"""独立资源下载器入口；V2 服务保留在 live2d_download。"""
from pathlib import Path
import os
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from live2d_download.hosted.ui import DownloadWizardWindow


def main():
    # 旧 V2 安装器仍使用相对于 GPT_SoVITS 的资源路径。
    """初始化独立下载器运行环境、中文字体与角色列表并启动窗口。"""
    os.chdir(SCRIPT_DIR)
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFontDatabase, QFont
    from PyQt5.QtCore import Qt
    from log import setup_logging, shutdown_logging
    setup_logging()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    from character import GetCharacterAttributes
    characters = GetCharacterAttributes().character_class_list
    font_id = QFontDatabase.addApplicationFont(str(PROJECT_ROOT / "font/msyh.ttc"))
    if font_id != -1:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0]))
    window = DownloadWizardWindow(characters)
    window.show()
    try:
        return app.exec_()
    finally:
        shutdown_logging()


if __name__ == "__main__":
    sys.exit(main())
