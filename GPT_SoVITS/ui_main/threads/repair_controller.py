from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from repair.repair_checker import (
    PreparedRepair,
    RepairCancelled,
    RepairCheckResult,
    RepairVersionUnsupported,
    check_integrity,
    prepare_repair,
)
from update.update_paths import get_app_root


class RepairCheckThread(QThread):
    """在后台获取修复清单并扫描本地文件。"""

    progressChanged = pyqtSignal(int, int, str)
    checkFinished = pyqtSignal(object)
    checkFailed = pyqtSignal(str, str)

    def __init__(self, base_urls: tuple[str, ...], parent: object | None = None) -> None:
        """保存修复资源源列表。"""

        super().__init__(parent)
        self.base_urls = base_urls
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消尚未触达正式文件的检查。"""

        self._cancelled = True

    def run(self) -> None:
        """执行完整性检查并发出结构化结果。"""

        try:
            result = check_integrity(
                get_app_root(),
                self.base_urls,
                progress_callback=self.progressChanged.emit,
                cancelled=lambda: self._cancelled,
            )
            self.checkFinished.emit(result)
        except RepairCancelled as exc:
            self.checkFailed.emit("cancelled", str(exc))
        except RepairVersionUnsupported as exc:
            self.checkFailed.emit("unsupported", str(exc))
        except Exception as exc:
            self.checkFailed.emit("unavailable", str(exc))


class RepairPrepareThread(QThread):
    """在后台下载并校验修复 objects，随后生成 repair plan。"""

    progressChanged = pyqtSignal(int, int, str)
    prepareFinished = pyqtSignal(object)
    prepareFailed = pyqtSignal(str, str)

    def __init__(self, check_result: RepairCheckResult, parent: object | None = None) -> None:
        """保存用户已经确认的检查结果。"""

        super().__init__(parent)
        self.check_result = check_result
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消下载和 staging 准备。"""

        self._cancelled = True

    def run(self) -> None:
        """下载全部修复资源并发出本地计划。"""

        try:
            prepared: PreparedRepair = prepare_repair(
                get_app_root(),
                self.check_result,
                progress_callback=self.progressChanged.emit,
                cancelled=lambda: self._cancelled,
            )
            self.prepareFinished.emit(prepared)
        except RepairCancelled as exc:
            self.prepareFailed.emit("cancelled", str(exc))
        except Exception as exc:
            self.prepareFailed.emit("failed", str(exc))
