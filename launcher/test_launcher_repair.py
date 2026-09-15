"""仅验证修复编排，不联网、不覆盖程序文件、不启动修复器。"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "launcher"), str(ROOT / "GPT_SoVITS")]
from PyQt5.QtWidgets import QApplication, QMessageBox
from launcher_repair import LauncherRepairDialog


class RepairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch("launcher_repair.QTimer.singleShot"):
            self.dialog = LauncherRepairDialog(ROOT, Mock(running={}))
        self.dialog.panel = Mock()

    def tearDown(self):
        self.dialog.threads = []
        self.dialog.install_timer.stop()
        self.dialog.close()

    def test_running_program_blocks_prepare(self):
        self.dialog.processes.running = {"webui": Mock(process=Mock(poll=Mock(return_value=None)))}
        with patch("launcher_repair.RepairPrepareThread") as thread:
            self.dialog.start_prepare()
            thread.assert_not_called()
        self.dialog.panel.set_error.assert_called_once()

    def test_declined_confirmation_does_not_prepare(self):
        with patch("launcher_repair.QMessageBox.question", return_value=QMessageBox.No), \
             patch("launcher_repair.RepairPrepareThread") as thread:
            self.dialog.start_prepare()
            thread.assert_not_called()

    def test_cancelled_download_cannot_install(self):
        self.dialog.cancel()
        self.dialog.downloaded(Mock(plan_file=Path("unused")))
        with patch("launcher_repair.launch_repair_process") as launch:
            self.dialog.install_when_idle()
            launch.assert_not_called()

    def test_waits_for_threads_and_restarts_launcher(self):
        worker = Mock(isRunning=Mock(return_value=True))
        self.dialog.threads = [worker]
        self.dialog.prepared = Mock(plan_file=ROOT / "unused-plan.json")
        with patch("launcher_repair.launch_repair_process") as launch, \
             patch("launcher_repair.QApplication") as app:
            self.dialog.install_when_idle()
            launch.assert_not_called()
            worker.isRunning.return_value = False
            self.dialog.install_when_idle()
            launch.assert_called_once_with(ROOT, ROOT / "unused-plan.json", os.getpid(),
                                           [sys.executable, str(ROOT / "launcher/launcher.py")])
            app.instance.return_value.quit.assert_called_once()

    def test_launch_failure_keeps_window_open(self):
        self.dialog.prepared = Mock(plan_file=ROOT / "unused-plan.json")
        with patch("launcher_repair.launch_repair_process", side_effect=OSError("test")), \
             patch("launcher_repair.QApplication") as app:
            self.dialog.install_when_idle()
            app.instance.assert_not_called()
        self.dialog.panel.set_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
