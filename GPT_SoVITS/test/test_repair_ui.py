from __future__ import annotations

# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QLabel


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GPT_SoVITS"))
sys.path.insert(0, str(ROOT))

from repair.repair_checker import FetchedManifest, RepairCandidate, RepairCheckResult
from repair.repair_manifest import RepairFileEntry, RepairManifest
from ui_main.components.repair_dialog import RepairDialog


class RepairDialogTest(unittest.TestCase):
    """验证程序文件修复确认弹窗的关键交互状态。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建所有测试共用的 Qt 应用。"""

        cls.app = QApplication.instance() or QApplication([])

    def make_result(self) -> RepairCheckResult:
        """构造包含修改和缺失文件的检查结果。"""

        entries = (
            RepairFileEntry(path="GPT_SoVITS/main2.py", sha256="a" * 64, size=100, mode="0644"),
            RepairFileEntry(path="tools/apply_repair.py", sha256="b" * 64, size=200, mode="0755"),
        )
        manifest = RepairManifest(
            schema=1,
            app_id="D_sakiko",
            channel="stable",
            version="3.2.0",
            platform="macos",
            arch="arm64",
            min_repair_client_version="1.0.0",
            generated_at="2026-07-10T00:00:00+00:00",
            files=entries,
        )
        fetched = FetchedManifest(
            manifest=manifest,
            content=b"{}",
            sha256="c" * 64,
            source_base_url="https://cdn",
        )
        return RepairCheckResult(
            fetched_manifest=fetched,
            candidates=(
                RepairCandidate(entries[0], "modified", "d" * 64),
                RepairCandidate(entries[1], "missing", None),
            ),
            base_urls=("https://cdn",),
        )

    def test_dialog_lists_paths_without_categories_and_warns_about_overwrite(self) -> None:
        """详情应平铺路径，确认文案应说明手动修改会丢失。"""

        dialog = RepairDialog(self.make_result())
        labels = [label.text() for label in dialog.findChildren(QLabel)]

        self.assertEqual(dialog.file_list.count(), 2)
        self.assertEqual(dialog.file_list.item(0).text(), "GPT_SoVITS/main2.py")
        self.assertIn("手动修改过这些文件", "\n".join(labels))
        self.assertEqual(dialog.primary_button.text(), "备份并修复")

    def test_dialog_disables_confirmation_while_preparing(self) -> None:
        """下载校验阶段应显示进度和取消入口。"""

        dialog = RepairDialog(self.make_result())
        dialog.set_preparing()
        dialog.set_progress(100, 300, "GPT_SoVITS/main2.py")

        self.assertFalse(dialog.primary_button.isEnabled())
        self.assertFalse(dialog.cancel_button.isHidden())
        self.assertEqual(dialog.progress_bar.value(), 100)

        cancellations: list[bool] = []
        dialog.cancelRequested.connect(lambda: cancellations.append(True))  # noqa
        dialog.reject()
        self.assertEqual(cancellations, [True])

    def test_more_functions_exposes_manual_repair_entry(self) -> None:
        """更多功能窗口应把手动修复回调接到独立按钮。"""

        from qtUI import MoreFunctionWindow

        calls: list[str] = []
        dialog = MoreFunctionWindow(
            lambda: None,
            check_update_fun=lambda: None,
            check_repair_fun=lambda: calls.append("repair"),
        )
        dialog.check_repair_btn.click()
        self.app.processEvents()

        self.assertEqual(calls, ["repair"])


if __name__ == "__main__":
    unittest.main()
