from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QThread, pyqtSignal

from update.update_checker import (
    build_update_plan,
    detect_arch,
    detect_platform,
    fetch_first_available_index,
    fetch_release_notes,
    read_current_version,
)
from update.update_downloader import download_and_prepare_patch
from update.update_models import DownloadedPatch, ReleaseNoteUrl, UpdatePlan
from update.update_paths import get_app_root, get_download_dir, get_package_dir, get_version_file


class UpdateCheckThread(QThread):
    """在后台检查远端 update_index.json，避免阻塞主 UI。"""

    updateAvailable = pyqtSignal(object)
    noUpdate = pyqtSignal()
    checkFailed = pyqtSignal(str)

    def __init__(self, index_urls: list[str] | tuple[str, ...], parent: object | None = None) -> None:
        """保存更新索引 URL 列表。"""

        super().__init__(parent)
        self.index_urls = tuple(index_urls)

    def run(self) -> None:
        """执行更新检查并发出结果信号。"""

        if not self.index_urls:
            self.noUpdate.emit()
            return
        try:
            app_root = get_app_root()
            current_version = read_current_version(get_version_file(app_root))
            index, _source_url = fetch_first_available_index(self.index_urls)
            plan = build_update_plan(index, current_version, detect_platform(), detect_arch())
            if plan is None:
                self.noUpdate.emit()
                return
            self.updateAvailable.emit(plan)
        except Exception as exc:
            self.checkFailed.emit(str(exc))


class UpdateDownloadThread(QThread):
    """在后台下载、校验并解压更新补丁链。"""

    progressChanged = pyqtSignal(int, int, float)
    statusChanged = pyqtSignal(str)
    downloadFinished = pyqtSignal(object)
    downloadFailed = pyqtSignal(str)

    def __init__(self, update_plan: UpdatePlan, parent: object | None = None) -> None:
        """保存更新计划。"""

        super().__init__(parent)
        self.update_plan = update_plan
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消下载。"""

        self._cancelled = True

    def _make_progress_callback(self, offset: int, total: int) -> Callable[[int, int, float], None]:
        """构造单个补丁下载进度回调。"""

        def _callback(downloaded: int, _patch_total: int, speed: float) -> None:
            if self._cancelled:
                raise RuntimeError("用户取消下载")
            self.progressChanged.emit(offset + downloaded, total, speed)

        return _callback

    def run(self) -> None:
        """按补丁链顺序下载并准备补丁包。"""

        try:
            app_root = get_app_root()
            downloaded_patches: list[DownloadedPatch] = []
            offset = 0
            total = self.update_plan.total_size
            for patch in self.update_plan.patches:
                if self._cancelled:
                    raise RuntimeError("用户取消下载")
                self.statusChanged.emit(f"正在下载 {patch.base_version} -> {patch.target_version}")
                downloaded = download_and_prepare_patch(
                    patch=patch,
                    download_dir=get_download_dir(patch.target_version, app_root),
                    package_dir=get_package_dir(patch.target_version, app_root),
                    progress_callback=self._make_progress_callback(offset, total),
                )
                downloaded_patches.append(downloaded)
                offset += patch.size
                self.progressChanged.emit(offset, total, 0.0)
            self.statusChanged.emit("补丁准备完成")
            self.downloadFinished.emit(downloaded_patches)
        except Exception as exc:
            self.downloadFailed.emit(str(exc))


class ReleaseNotesThread(QThread):
    """在后台拉取完整 release_notes.md。"""

    notesLoaded = pyqtSignal(str, str)
    notesFailed = pyqtSignal(str)

    def __init__(self, notes_urls: tuple[ReleaseNoteUrl, ...], parent: object | None = None) -> None:
        """保存 release notes 候选 URL。"""

        super().__init__(parent)
        self.notes_urls = notes_urls

    def run(self) -> None:
        """拉取 release notes 并发出结果。"""

        try:
            markdown, source_name = fetch_release_notes(self.notes_urls)
            self.notesLoaded.emit(markdown, source_name)
        except Exception as exc:
            self.notesFailed.emit(str(exc))
