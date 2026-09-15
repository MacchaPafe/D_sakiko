"""Windows 上验证 macOS 命令、转义与状态跟踪，不打开 Terminal。"""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launcher_actions import LauncherProcesses, build_command
from launcher_macos import TerminalProcess, start_in_terminal, run_request


class MacLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_macos_venv_and_root_directory(self):
        script = self.root / "GPT_SoVITS/main2.py"
        script.parent.mkdir()
        script.touch()
        with patch("launcher_actions.sys.platform", "darwin"):
            runner = LauncherProcesses(self.root)
            self.assertEqual(runner.executable, str(self.root / ".venv/bin/python"))
            command, cwd = build_command("desktop", self.root, sys.executable)
            self.assertEqual(cwd, self.root)
            self.assertEqual(command[-1], str(script))

    def test_opener_exit_is_not_program_exit(self):
        status = self.root / "status.json"
        process = TerminalProcess(Mock(poll=Mock(return_value=0)), status)
        self.assertIsNone(process.poll())
        status.write_text(json.dumps({"pid": 123}), encoding="utf-8")
        with patch("launcher_macos.os.kill"):
            self.assertIsNone(process.poll())
        status.write_text(json.dumps({"returncode": 0}), encoding="utf-8")
        self.assertEqual(process.poll(), 0)

    def test_automation_denied_is_failure(self):
        process = TerminalProcess(Mock(poll=Mock(return_value=1)), self.root / "missing")
        self.assertEqual(process.poll(), 1)

    def test_shell_arguments_are_escaped(self):
        log = self.root / "中文 ' & !.log"
        command = [sys.executable, "-u", "a ' & !.py"]
        with patch("launcher_macos.subprocess.Popen") as popen:
            start_in_terminal(command, self.root, self.root, log)
        args = popen.call_args.args[0]
        self.assertEqual(args[0], "/usr/bin/osascript")
        self.assertEqual(shlex.split(args[-1]), [sys.executable,
            str(self.root / "launcher/launcher_macos.py"), "--run", str(log.with_suffix(".request.json"))])
        self.assertEqual(json.loads(log.with_suffix(".request.json").read_text(encoding="utf-8"))["command"], command)

    def test_runner_environment_and_exit_code(self):
        status = self.root / "status.json"
        request = self.root / "request.json"
        request.write_text(json.dumps({"status": str(status), "cwd": str(self.root),
            "root": str(self.root), "command": ["python", "fixture.py"]}), encoding="utf-8")
        with patch("launcher_macos.signal.SIGHUP", 1, create=True), \
             patch("launcher_macos.signal.signal"), \
             patch("launcher_macos.subprocess.Popen", return_value=Mock(wait=Mock(return_value=7))) as popen:
            self.assertEqual(run_request(request), 7)
        self.assertEqual(json.loads(status.read_text())["returncode"], 7)
        self.assertEqual(popen.call_args.kwargs["env"]["VIRTUAL_ENV"], str(self.root / ".venv"))
        self.assertNotIn("stdout", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
