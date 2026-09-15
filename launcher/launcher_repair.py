"""复用主程序修复组件；不加载主窗口、角色配置或模型。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from repair.repair_checker import get_configured_repair_base_urls
from repair.repair_launcher import launch_repair_process
from ui_main.components.repair_dialog import RepairDialog
from ui_main.threads.repair_controller import RepairCheckThread, RepairPrepareThread


class LauncherRepairDialog(QDialog):
    def __init__(self, root: Path, processes, parent=None):
        super().__init__(parent)
        self.root = root
        self.processes = processes
        self.threads = []
        self.worker = None
        self.panel = None
        self.result = None
        self.prepared = None
        self.cancelled = False
        self.setWindowTitle("修复程序")
        self.resize(600, 530)
        self.layout = QVBoxLayout(self)
        self.message = QLabel("正在检查程序文件…")
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)
        self.check_button = QPushButton("重新检查")
        self.check_button.clicked.connect(self.check)
        self.layout.addWidget(self.check_button)
        self.cancel_button = QPushButton("取消检查")
        self.cancel_button.clicked.connect(self.cancel)
        self.layout.addWidget(self.cancel_button)
        self.install_timer = QTimer(self)
        self.install_timer.setInterval(100)
        self.install_timer.timeout.connect(self.install_when_idle)
        QTimer.singleShot(0, self.check)

    def busy(self):
        return any(thread.isRunning() for thread in self.threads)

    def programs_running(self):
        return any(item.process.poll() is None for item in self.processes.running.values())

    def check(self):
        if self.busy():
            return
        urls = get_configured_repair_base_urls()
        if not urls:
            self.message.setText("尚未配置程序文件修复资源地址。")
            self.cancel_button.hide()
            return
        self.cancelled = False
        self.check_button.setEnabled(False)
        self.cancel_button.show()
        self.message.setText("正在检查程序文件…")
        self.worker = RepairCheckThread(urls, self)
        self.threads.append(self.worker)
        self.worker.progressChanged.connect(lambda done, total, path: self.message.setText(f"正在检查 {done}/{total}\n{path}"))
        self.worker.checkFinished.connect(self.checked)
        self.worker.checkFailed.connect(lambda reason, message: self.message.setText(message))
        self.worker.finished.connect(lambda: self.check_button.setEnabled(True))
        self.worker.finished.connect(self.cancel_button.hide)
        self.worker.start()

    def checked(self, result):
        if self.cancelled:
            self.message.setText("已取消检查。")
            return
        if not result.candidates:
            self.message.setText("程序文件完整，无需修复。")
            return
        self.result = result
        self.message.hide()
        self.check_button.hide()
        self.panel = RepairDialog(result, self)
        self.panel.setWindowFlags(Qt.Widget)
        self.layout.addWidget(self.panel)
        self.panel.prepareRequested.connect(self.start_prepare)
        self.panel.cancelRequested.connect(self.cancel)
        self.panel.later_button.clicked.disconnect()
        self.panel.later_button.clicked.connect(self.reject)
        self.panel.show()

    def start_prepare(self):
        if self.busy() or self.install_timer.isActive():
            return
        if self.programs_running():
            self.panel.set_error("请先关闭启动器打开的程序，再修复。")
            return
        reply = QMessageBox.question(self, "备份并修复",
            "请确认桌面端、WebUI、小剧场和配置工具均已关闭（包括从其他入口启动的程序）。\n\n"
            "修复将覆盖差异列表中的本地修改，原文件会先备份。准备完成后将退出启动器，修复成功后重新打开。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.cancelled = False
        self.prepared = None
        self.panel.set_preparing()
        self.worker = RepairPrepareThread(self.result, self)
        self.threads.append(self.worker)
        self.worker.progressChanged.connect(self.panel.set_progress)
        self.worker.prepareFailed.connect(self.prepare_failed)
        self.worker.prepareFinished.connect(self.downloaded)
        self.worker.start()

    def prepare_failed(self, reason, message):
        if reason == "cancelled" or self.cancelled:
            self.panel.set_cancelled()
        else:
            self.panel.set_error(message)

    def cancel(self):
        self.cancelled = True
        self.prepared = None
        self.install_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
        if self.panel is not None:
            self.panel.set_cancelled()
        else:
            self.message.setText("正在取消检查，请稍候…" if self.busy() else "已取消检查。")

    def downloaded(self, prepared):
        if self.cancelled:
            self.panel.set_cancelled()
            return
        self.prepared = prepared
        self.panel.set_launching()
        self.panel.set_status("修复文件已校验，正在准备关闭启动器并应用修复。")
        self.install_timer.start()

    def install_when_idle(self):
        # 等所有 QThread 收尾，避免退出应用时销毁运行中的线程。
        if self.busy():
            return
        self.install_timer.stop()
        if self.prepared is None or self.cancelled:
            return
        if self.programs_running():
            self.panel.set_error("检测到程序仍在运行，请关闭后重试。")
            self.prepared = None
            return
        try:
            launch_repair_process(self.root, self.prepared.plan_file, os.getpid(),
                                  [sys.executable, str(self.root / "launcher/launcher.py")])
        except Exception as exc:
            self.panel.set_error(f"无法启动修复器：{exc}")
            self.prepared = None
            return
        self.accept()
        QApplication.instance().quit()

    def reject(self):
        if self.busy() or self.install_timer.isActive():
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy() or self.install_timer.isActive():
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
