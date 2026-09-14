"""复用主程序更新组件，提供启动器自己的更新生命周期。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

# 延迟到用户点击更新时导入；不加载 qtUI、配置或模型。
from ui_main.components.update_dialog import UpdateDialog
from ui_main.threads.update_controller import ReleaseNotesThread, UpdateCheckThread, UpdateDownloadThread
from update.update_checker import get_configured_index_urls
from update.update_launcher import launch_update_process


class LauncherUpdateDialog(QDialog):
    def __init__(self, root: Path, processes, parent=None):
        super().__init__(parent)
        self.root = root
        self.processes = processes
        self.threads = []
        self.download = None
        self.panel = None
        self.plan = None
        self.prepared = None
        self.cancelled = False
        self.setWindowTitle("检查更新")
        self.resize(600, 570)
        self.layout = QVBoxLayout(self)
        self.message = QLabel("正在检查更新…")
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)
        self.check_button = QPushButton("重新检查")
        self.check_button.clicked.connect(self.check)
        self.layout.addWidget(self.check_button)
        self.install_timer = QTimer(self)
        self.install_timer.setInterval(100)
        self.install_timer.timeout.connect(self.install_when_idle)
        QTimer.singleShot(0, self.check)

    def busy(self):
        return any(thread.isRunning() for thread in self.threads)

    def check(self):
        if self.busy():
            return
        urls = get_configured_index_urls()
        if not urls:
            self.message.setText("尚未配置更新索引 URL。")
            return
        self.check_button.setEnabled(False)
        self.message.setText("正在检查更新…")
        thread = UpdateCheckThread(urls, self)
        self.threads.append(thread)
        thread.updateAvailable.connect(self.available)
        thread.noUpdate.connect(lambda: self.message.setText("当前已是最新版本。"))
        thread.checkFailed.connect(lambda message: self.message.setText(f"检查更新失败：{message}"))
        thread.finished.connect(lambda: self.check_button.setEnabled(True))
        thread.start()

    def available(self, plan):
        self.plan = plan
        self.message.hide()
        self.check_button.hide()
        self.panel = UpdateDialog(plan, self)
        self.panel.setWindowFlags(Qt.Widget)
        self.layout.addWidget(self.panel)
        self.panel.downloadAndInstallRequested.connect(self.start_download)
        self.panel.cancelRequested.connect(self.cancel)
        # 内嵌弹窗的“稍后”交给外层处理，避免后台线程被销毁。
        self.panel.later_button.clicked.disconnect()
        self.panel.later_button.clicked.connect(self.reject)
        self.panel.show()
        urls = tuple(url for release in plan.releases for url in release.notes_urls)
        if urls:
            thread = ReleaseNotesThread(urls, self)
            self.threads.append(thread)
            thread.notesLoaded.connect(self.panel.set_release_notes)
            thread.start()

    def programs_running(self):
        return any(item.process.poll() is None for item in self.processes.running.values())

    def start_download(self):
        if self.download is not None and self.download.isRunning():
            return
        if self.programs_running():
            self.panel.set_error("请先关闭启动器打开的程序，再安装更新。")
            return
        reply = QMessageBox.question(self, "下载并安装更新",
            "请确认桌面端、WebUI、小剧场和配置工具均已关闭（包括从其他入口启动的程序）。\n\n"
            "下载完成后将退出启动器并自动安装，成功后重新打开启动器。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.cancelled = False
        self.prepared = None
        self.panel.set_downloading()
        self.download = UpdateDownloadThread(self.plan, self)
        self.threads.append(self.download)
        self.download.progressChanged.connect(self.panel.set_download_progress)
        self.download.statusChanged.connect(self.panel.set_status)
        self.download.downloadFailed.connect(self.panel.set_error)
        self.download.downloadFinished.connect(self.downloaded)
        self.download.start()

    def cancel(self):
        self.cancelled = True
        self.prepared = None
        self.install_timer.stop()
        if self.download is not None and self.download.isRunning():
            self.download.cancel()
            self.panel.set_status("正在取消下载…")
        else:
            self.reject()

    def downloaded(self, patches):
        if self.cancelled:
            self.panel.set_error("已取消安装。")
            return
        self.prepared = patches
        self.panel.set_installing()
        self.panel.set_status("补丁已准备完成，正在准备关闭启动器并安装。")
        self.install_timer.start()

    def install_when_idle(self):
        # 信号发出时 QThread 可能仍在收尾，等待全部结束再退出应用。
        if self.busy():
            return
        self.install_timer.stop()
        if not self.prepared or self.cancelled:
            return
        if self.programs_running():
            self.panel.set_error("检测到程序仍在运行，请关闭后重试。")
            self.prepared = None
            return
        try:
            launch_update_process(self.prepared, self.root, os.getpid(),
                                  [sys.executable, str(self.root / "launcher/launcher.py")])
        except Exception as exc:
            self.panel.set_error(f"无法启动安装器：{exc}")
            self.prepared = None
            return
        self.accept()
        QApplication.instance().quit()

    def reject(self):
        if self.busy() or self.install_timer.isActive():
            if self.panel:
                self.panel.set_status("更新任务尚未结束；下载中可点击取消，待任务结束后关闭。")
            else:
                self.message.setText("正在检查更新，请等待检查结束后关闭。")
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy() or self.install_timer.isActive():
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
