from __future__ import annotations

from threading import Event
import time
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from .service import Cancelled, DownloadError, check_cancel


class Signals(QObject):
    done = pyqtSignal(int, object, str)
    progress = pyqtSignal(int, int, int)


class Work(QRunnable):
    def __init__(self, identifier, function, event, signals):
        """保存后台任务参数及主线程信号接收对象。"""
        super().__init__()
        self.identifier, self.function, self.event, self.signals = identifier, function, event, signals

    def run(self):
        """在线程池中执行任务，将结果和经过处理的错误发回主线程。"""
        last = 0.0
        def progress(done, total):
            """更新并限制进度信号频率，避免大量网络块阻塞界面。"""
            nonlocal last
            now = time.monotonic()
            if now - last > 0.08 or done == total:
                # Qt int 是 32 位；使用千字节避免大 ZIP 字节数溢出。
                self.signals.progress.emit(self.identifier, done // 1024, total // 1024)
                last = now
        try:
            check_cancel(self.event)
            value = self.function(self.event, progress)
            self.signals.done.emit(self.identifier, value, "")
        except Cancelled:
            self.signals.done.emit(self.identifier, None, "下载已取消")
        except DownloadError as exc:
            self.signals.done.emit(self.identifier, None, str(exc))
        except Exception:
            self.signals.done.emit(self.identifier, None, "请求或文件处理失败，请重试")


class JobHub(QObject):
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        """创建独立下载池与读取池，统一持有任务信号和取消事件。"""
        super().__init__(parent)
        self.signals = Signals(self)
        self.signals.done.connect(self._done)
        self.signals.progress.connect(self._progress)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.download_pool = QThreadPool(self)
        self.download_pool.setMaxThreadCount(1)
        self.jobs = {}
        self.next_id = 0

    @property
    def busy(self):
        """判断是否仍有下载或安装任务，供页面导航锁定使用。"""
        return any(job[3] for job in self.jobs.values())

    def submit(self, function, callback, progress=None, download=False):
        """创建独立取消事件并提交任务；下载串行、目录和预览限量并行。"""
        self.next_id += 1
        identifier = self.next_id
        event = Event()
        self.jobs[identifier] = (event, callback, progress, download)
        worker = Work(identifier, function, event, self.signals)
        (self.download_pool if download else self.pool).start(worker)
        self.busy_changed.emit(self.busy)
        return identifier

    def cancel(self, identifier):
        """请求停止指定任务，实际完成前仍保留任务记录。"""
        if identifier in self.jobs:
            self.jobs[identifier][0].set()

    def cancel_reads(self):
        """取消旧页面的查询和预览，不中断资源下载。"""
        for event, _, _, download in self.jobs.values():
            if not download:
                event.set()

    def cancel_all(self):
        """请求停止所有任务，供关闭窗口时等待后台退出。"""
        for event, *_ in self.jobs.values():
            event.set()

    def idle(self):
        """同时检查任务记录和线程池，确认后台已经完全停止。"""
        return not self.jobs and not self.pool.activeThreadCount() and not self.download_pool.activeThreadCount()

    def _done(self, identifier, value, error):
        """在主线程交付任务结果并更新下载忙碌状态。"""
        job = self.jobs.pop(identifier, None)
        if job:
            job[1](value, error)
        self.busy_changed.emit(self.busy)

    def _progress(self, identifier, done, total):
        """在主线程把进度分发给仍有效的任务回调。"""
        job = self.jobs.get(identifier)
        if job and job[2]:
            job[2](done, total)
