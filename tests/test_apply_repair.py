from __future__ import annotations

# ruff: noqa: E402

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from tools.apply_repair import (
    RepairExecutionFailure,
    RepairResultRecorder,
    apply_repair_plan,
    load_repair_plan,
    precheck_targets,
)
from update.operation_lock import OperationLockBusy, acquire_operation_lock, get_operation_owner_file


def sha256(content: bytes) -> str:
    """计算测试内容的 SHA256。"""

    return hashlib.sha256(content).hexdigest()


def write_plan(
    app_root: Path,
    files: list[dict[str, object]],
    *,
    name: str = "repair_3.2.0_test",
) -> Path:
    """在允许目录写出测试 repair plan。"""

    plan_dir = app_root / ".updates" / "repair" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema": 1,
        "app_id": "D_sakiko",
        "version": "3.2.0",
        "platform": "macos",
        "arch": "arm64",
        "manifest_sha256": "f" * 64,
        "staging_dir": f".updates/repair/staging/{name}",
        "files": files,
    }
    plan_file = plan_dir / f"{name}.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    return plan_file


def stage_file(app_root: Path, run_name: str, content: bytes) -> tuple[str, str]:
    """写入测试 staging object 并返回相对路径与 hash。"""

    value = sha256(content)
    path = app_root / ".updates" / "repair" / "staging" / run_name / "objects" / value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.relative_to(app_root).as_posix(), value


class ApplyRepairTest(unittest.TestCase):
    """验证独立修复器的预检、锁、事务和结果记录。"""

    def test_applies_modified_and_missing_files(self) -> None:
        """修复器应备份修改文件并创建缺失文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            (app_root / "version.json").write_text('{"version":"3.2.0"}', encoding="utf-8")
            old = b"old"
            new = b"new official"
            target = app_root / "main.py"
            target.write_bytes(old)
            staged_one, target_sha = stage_file(app_root, "repair_3.2.0_test", new)
            staged_two, missing_sha = stage_file(app_root, "repair_3.2.0_test", b"missing official")
            plan_file = write_plan(
                app_root,
                [
                    {
                        "path": "main.py",
                        "original_state": "modified",
                        "original_sha256": sha256(old),
                        "sha256": target_sha,
                        "size": len(new),
                        "mode": "0644",
                        "staged_file": staged_one,
                    },
                    {
                        "path": "missing.py",
                        "original_state": "missing",
                        "original_sha256": None,
                        "sha256": missing_sha,
                        "size": len(b"missing official"),
                        "mode": "0755",
                        "staged_file": staged_two,
                    },
                ],
            )
            plan = load_repair_plan(plan_file)
            backup = app_root / ".updates" / "repair" / "backups" / plan_file.stem
            backup.mkdir(parents=True)
            with patch("tools.apply_repair.detect_platform", return_value="macos"), patch(
                "tools.apply_repair.detect_arch", return_value="arm64"
            ):
                count = apply_repair_plan(app_root, plan, backup)

            self.assertEqual(count, 2)
            self.assertEqual(target.read_bytes(), new)
            self.assertEqual((app_root / "missing.py").read_bytes(), b"missing official")
            self.assertEqual((backup / "files" / "main.py").read_bytes(), old)

    def test_precheck_aborts_whole_batch_after_local_change(self) -> None:
        """检查后再次变化的目标应在任何备份替换前中止整批。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            current = app_root / "main.py"
            current.write_bytes(b"changed after scan")
            staged, target_sha = stage_file(app_root, "repair_3.2.0_test", b"official")
            plan_file = write_plan(
                app_root,
                [
                    {
                        "path": "main.py",
                        "original_state": "modified",
                        "original_sha256": sha256(b"state at scan"),
                        "sha256": target_sha,
                        "size": len(b"official"),
                        "mode": "0644",
                        "staged_file": staged,
                    }
                ],
            )
            plan = load_repair_plan(plan_file)

            with self.assertRaises(RuntimeError):
                precheck_targets(app_root, plan)

            self.assertEqual(current.read_bytes(), b"changed after scan")

    def test_failure_rolls_back_already_replaced_file(self) -> None:
        """后续文件写入失败时应恢复此前已替换目标。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            (app_root / "version.json").write_text('{"version":"3.2.0"}', encoding="utf-8")
            first = app_root / "first.py"
            second = app_root / "second.py"
            first.write_bytes(b"first old")
            second.write_bytes(b"second old")
            staged_first, first_sha = stage_file(app_root, "repair_3.2.0_test", b"first new")
            staged_second, second_sha = stage_file(app_root, "repair_3.2.0_test", b"second new")
            plan_file = write_plan(
                app_root,
                [
                    {
                        "path": "first.py",
                        "original_state": "modified",
                        "original_sha256": sha256(b"first old"),
                        "sha256": first_sha,
                        "size": len(b"first new"),
                        "mode": "0644",
                        "staged_file": staged_first,
                    },
                    {
                        "path": "second.py",
                        "original_state": "modified",
                        "original_sha256": sha256(b"second old"),
                        "sha256": second_sha,
                        "size": len(b"second new"),
                        "mode": "0644",
                        "staged_file": staged_second,
                    },
                ],
            )
            plan = load_repair_plan(plan_file)
            backup = app_root / ".updates" / "repair" / "backups" / plan_file.stem
            backup.mkdir(parents=True)
            original_copy = __import__("shutil").copy2

            def failing_copy(source: Path, destination: Path) -> Path:
                """只在复制第二个 staging 文件时注入失败。"""

                if Path(source).resolve() == (app_root / staged_second).resolve():
                    raise OSError("injected failure")
                return original_copy(source, destination)

            with patch("tools.apply_repair.detect_platform", return_value="macos"), patch(
                "tools.apply_repair.detect_arch", return_value="arm64"
            ), patch("tools.apply_repair.shutil.copy2", side_effect=failing_copy):
                with self.assertRaises(RepairExecutionFailure) as context:
                    apply_repair_plan(app_root, plan, backup)

            self.assertTrue(context.exception.rollback_succeeded)
            self.assertEqual(first.read_bytes(), b"first old")
            self.assertEqual(second.read_bytes(), b"second old")

    def test_operation_lock_is_non_blocking_and_owner_is_diagnostic(self) -> None:
        """第二个事务应立即失败，释放后遗留锁文件不应阻止重试。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            with acquire_operation_lock(app_root, "repair"):
                self.assertTrue(get_operation_owner_file(app_root).exists())
                with self.assertRaises(OperationLockBusy):
                    with acquire_operation_lock(app_root, "update"):
                        self.fail("不应获得第二把锁")
            self.assertFalse(get_operation_owner_file(app_root).exists())
            with acquire_operation_lock(app_root, "update"):
                pass

    def test_failed_result_is_unnotified_and_uses_relative_log(self) -> None:
        """失败结果应等待主程序提示，并保存可迁移的相对日志路径。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir)
            status = app_root / "logs" / "repair" / "last_repair_result.json"
            log = app_root / "logs" / "repair" / "repair.log"
            recorder = RepairResultRecorder(app_root, status, log)
            recorder.write("failed", failed_count=1)
            result = json.loads(status.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["notified"], False)
        self.assertEqual(result["log_file"], "logs/repair/repair.log")


if __name__ == "__main__":
    unittest.main()
