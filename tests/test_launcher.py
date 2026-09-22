from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))
from launcher_actions import ENTRIES, LauncherProcesses, RunningEntry, build_command, console_python, preferred_font


class LauncherActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="启动 测试 & ! ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for entry in ENTRIES:
            path = self.root / entry.script
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def package(self):
        path = self.root / "更新 包"
        path.mkdir()
        (path / "manifest.json").write_text("{}")
        (path / "patch.hdiff").write_bytes(b"fixture")
        return path

    def test_all_legacy_tools_launch_without_bat_files(self):
        expected = {"desktop": "main2.py", "theater": "multi_char_main.py", "config": "dsakiko_configuration.py",
                    "downloader": "live2d_downloader_ui.py", "editor": "live2d_viewer.py"}
        for key, script in expected.items():
            with self.subTest(key=key):
                args, cwd = build_command(key, self.root, sys.executable)
                self.assertEqual(args, [sys.executable, "-u", str(self.root / "GPT_SoVITS" / script)])
                self.assertEqual(cwd, self.root / "GPT_SoVITS")
        self.assertFalse(list(self.root.rglob("*.bat")))

    def test_webui_starts_pairing_under_isolated_python(self):
        main = self.root / "dsakiko_webui/backend/main.py"
        main.write_text("import os, sys, json; print(json.dumps(os.getcwd())); print(sys.argv[1:])", encoding="utf-8")
        args, cwd = build_command("webui", self.root, sys.executable)
        result = subprocess.run([args[0], "-I", *args[1:]], cwd=cwd, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout.decode("utf-8")
        self.assertEqual(json.loads(text.splitlines()[0]), str(self.root))
        self.assertIn("--open-pairing", text)

    def test_pythonw_uses_same_environment_console_python(self):
        python = self.root / "python.exe"
        python.touch()
        self.assertEqual(console_python(str(self.root / "pythonw.exe")), python)

    def test_missing_program_and_runtime_report_paths(self):
        (self.root / "GPT_SoVITS/main2.py").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "main2.py"):
            build_command("desktop", self.root, sys.executable)
        with self.assertRaisesRegex(FileNotFoundError, "Python"):
            console_python(str(self.root / "missing.exe"))

    def test_update_waits_for_launcher_and_restarts_it(self):
        package = self.package()
        args, cwd = build_command("update", self.root, sys.executable, package=package, wait_pid=123)
        self.assertEqual(cwd, self.root)
        self.assertEqual(args[args.index("--wait-pid") + 1], "123")
        self.assertEqual(args[args.index("--package") + 1], str(package))
        self.assertEqual(json.loads(args[args.index("--restart-command") + 1]),
                         [sys.executable, str(self.root / "launcher/launcher.py")])
        self.assertIn("--status-file", args)
        with self.assertRaises(ValueError):
            build_command("update", self.root, sys.executable, package=package)

    def test_update_rejects_incomplete_package(self):
        with self.assertRaisesRegex(ValueError, "manifest.json"):
            build_command("update", self.root, sys.executable, package=self.root, wait_pid=123)

    def test_child_exit_code_and_output_are_preserved(self):
        script = self.root / "GPT_SoVITS/main2.py"
        script.write_text("import os; print(os.getcwd()); print('启动日志'); raise SystemExit(7)", encoding="utf-8")
        runner = LauncherProcesses(self.root)
        log = runner.start("desktop")
        runner.running["desktop"].process.wait(timeout=15)
        self.assertEqual(runner.finished(), [("desktop", 7, log)])
        self.assertIn("启动日志", log.read_text(encoding="utf-8"))
        self.assertIn(str(script.parent), log.read_text(encoding="utf-8"))
        self.assertFalse(runner.running)

    def test_duplicate_and_update_while_running_are_blocked(self):
        runner = LauncherProcesses(self.root)
        runner.running["desktop"] = RunningEntry(Mock(poll=Mock(return_value=None)), self.root / "log")
        with self.assertRaises(RuntimeError):
            runner.start("desktop")
        with self.assertRaises(RuntimeError):
            runner.start("update", package=self.package())

    def test_spawn_failure_does_not_leave_running_entry(self):
        runner = LauncherProcesses(self.root)
        with patch("launcher_actions.subprocess.Popen", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                runner.start("desktop")
        self.assertFalse(runner.running)

    def test_font_selection_matches_main_program_without_deleting_fonts(self):
        folder = self.root / "font"
        folder.mkdir()
        self.assertEqual(preferred_font(self.root), folder / "msyh.ttc")
        for name in ("custom_font_9.ttf", "custom_font_100.ttf", "custom_font_invalid.ttf"):
            (folder / name).touch()
        self.assertEqual(preferred_font(self.root).name, "custom_font_100.ttf")
        self.assertEqual(len(list(folder.iterdir())), 3)


_RUN_GUI_TESTS = os.environ.get("DSAKIKO_LAUNCHER_GUI_TESTS") == "1"
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from launcher_ui import LauncherWindow
except ImportError:
    LauncherWindow = None


@unittest.skipUnless(_RUN_GUI_TESTS and LauncherWindow is not None, "GUI 测试需要交互式桌面（显式设置 DSAKIKO_LAUNCHER_GUI_TESTS=1）")
class LauncherUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.runner = Mock()
        self.runner.running = {}
        self.runner.finished.return_value = []
        self.window = LauncherWindow(ROOT, self.runner)
        self.window.timer.stop()
        self.addCleanup(self.window.close)

    def test_six_buttons_start_the_correct_tool(self):
        self.assertEqual(len(self.window.buttons), 7)
        for key in self.window.buttons:
            if key != "update":
                self.window.buttons[key].click()
                self.runner.start.assert_called_with(key, package=None)
                self.assertFalse(self.window.buttons[key].isEnabled())

    def test_launch_failure_shows_message_and_keeps_button(self):
        self.runner.start.side_effect = FileNotFoundError("missing fixture")
        with patch("launcher_ui.QMessageBox.warning") as warning:
            self.window.buttons["desktop"].click()
        warning.assert_called_once()
        self.assertTrue(self.window.buttons["desktop"].isEnabled())

    def test_exit_reenables_button_and_reports_failure(self):
        self.window.buttons["desktop"].setEnabled(False)
        self.runner.finished.return_value = [("desktop", 7, ROOT / "logs/fixture.log")]
        with patch("launcher_ui.QMessageBox.warning") as warning:
            self.window.poll_processes()
        warning.assert_called_once()
        self.assertTrue(self.window.buttons["desktop"].isEnabled())
        self.assertIn("异常退出", self.window.notice.text())

    def test_cancel_update_does_not_start_anything(self):
        with patch("launcher_ui.QFileDialog.getExistingDirectory", return_value=""):
            self.window.launch("update")
        self.runner.start.assert_not_called()

    def test_update_checks_running_tools_before_picker(self):
        self.runner.running = {"desktop": RunningEntry(Mock(poll=Mock(return_value=None)), ROOT / "log")}
        with patch("launcher_ui.QMessageBox.information") as info, patch("launcher_ui.QFileDialog.getExistingDirectory") as picker:
            self.window.launch("update")
        info.assert_called_once()
        picker.assert_not_called()

    def test_update_handoff_only_after_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / "manifest.json").write_text("{}")
            (package / "patch.hdiff").touch()
            with patch("launcher_ui.QFileDialog.getExistingDirectory", return_value=temp), patch("launcher_ui.QMessageBox.question", return_value=QMessageBox.No):
                self.window.launch("update")
            self.runner.start.assert_not_called()
            with patch("launcher_ui.QFileDialog.getExistingDirectory", return_value=temp), patch("launcher_ui.QMessageBox.question", return_value=QMessageBox.Yes), patch.object(self.window, "close") as close:
                self.window.launch("update")
            self.runner.start.assert_called_once_with("update", package=package)
            close.assert_called_once()

    def test_close_does_not_terminate_child_processes(self):
        child = Mock()
        self.runner.running = {"desktop": RunningEntry(child, ROOT / "log")}
        self.window.close()
        child.terminate.assert_not_called()
        child.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
