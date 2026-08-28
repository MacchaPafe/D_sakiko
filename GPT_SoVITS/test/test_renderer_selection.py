from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = ROOT / "tools" / "launch_runtime.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("test_launch_runtime_module", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RendererSelectionTest(unittest.TestCase):
    def test_config_and_environment_override_select_renderer(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({"ui_state": {"live2d_renderer": "pygame"}}),
                encoding="utf-8",
            )
            launcher.CONFIG_PATH = config_path
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "pygame")
            with patch.dict(os.environ, {"DSAKIKO_RENDERER": "electron"}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "electron")

    def test_missing_or_invalid_config_uses_compatibility_pygame_default(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as directory:
            launcher.CONFIG_PATH = Path(directory) / "missing.json"
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "pygame")
            launcher.CONFIG_PATH.write_text(
                json.dumps({"ui_state": {"live2d_renderer": "invalid"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "pygame")

    def test_electron_command_is_platform_specific(self):
        launcher = load_launcher()
        root = Path("/tmp/electron")
        with patch.object(launcher.os, "name", "nt"):
            self.assertEqual(launcher.electron_command(root).name, "electron.cmd")
        with patch.object(launcher.os, "name", "posix"):
            self.assertEqual(launcher.electron_command(root).name, "electron")

    def test_electron_command_candidates_have_cross_platform_fallbacks(self):
        launcher = load_launcher()
        root = Path("/tmp/electron")
        with patch.object(launcher.os, "name", "nt"):
            self.assertTrue(any(path.name == "electron.exe" for path in launcher.electron_command_candidates(root)))
        with patch.object(launcher.os, "name", "posix"):
            self.assertTrue(any(path.name == "Electron" for path in launcher.electron_command_candidates(root)))

    def test_main2_and_launcher_share_pygame_fallback(self):
        import main2
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "missing.json"
            with patch.object(main2, "project_root", directory), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(main2.resolve_renderer_mode(), "pygame")


if __name__ == "__main__":
    unittest.main()
