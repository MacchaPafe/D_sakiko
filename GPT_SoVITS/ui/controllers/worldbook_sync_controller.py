"""使用异步 QProcess 驱动世界书同步 worker。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PyQt5.QtCore import QObject, QProcess, pyqtSignal


class WorldbookSyncController(QObject):
    """管理 worker 生命周期和 NDJSON 协议，不直接打开 Qdrant。"""

    started_signal = pyqtSignal()
    progress_signal = pyqtSignal(str)
    waiting_signal = pyqtSignal(int)
    completed_signal = pyqtSignal(dict)
    failed_signal = pyqtSignal(str)
    readiness_changed = pyqtSignal(str)

    def __init__(self, app_root: Path, parent: QObject | None = None) -> None:
        """创建可复用的单任务 QProcess controller。"""

        super().__init__(parent)
        self._app_root = app_root.resolve()
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_finished)
        self._buffer = ""
        self._stderr = ""
        self._reconcile_pending = False
        self._waiting_for_lock = False
        self._terminal_event_seen = False
        self._readiness = "unavailable"

    @property
    def readiness(self) -> str:
        """返回最近一次同步报告的可用性。"""

        return self._readiness

    def is_rag_available(self) -> bool:
        """供主程序判断派生索引是否允许参与 RAG。"""

        return self._readiness in {"ready", "degraded"}

    def reconcile_all(self) -> None:
        """异步请求全量对账；运行中只登记一次补跑。"""

        self._start_or_queue("reconcile-all")

    def rebuild(self) -> None:
        """异步请求重建三类世界书 collection。"""

        self._start_or_queue("rebuild")

    def cancel_waiting(self) -> bool:
        """只取消尚在等待文件锁的 worker。"""

        if not self._waiting_for_lock or self._process.state() == QProcess.NotRunning:
            return False
        self._process.kill()
        return True

    def _start_or_queue(self, command: str) -> None:
        """启动 worker，或把运行中的多次请求合并成一次补跑。"""

        if self._process.state() != QProcess.NotRunning:
            self._reconcile_pending = True
            return
        self._buffer = ""
        self._stderr = ""
        self._waiting_for_lock = False
        self._terminal_event_seen = False
        self._set_readiness("syncing")
        self._process.setProgram(sys.executable)
        self._process.setArguments(["-m", "rag.worldbook.worker", command, "--app-root", str(self._app_root)])
        self._process.setWorkingDirectory(str(self._app_root / "GPT_SoVITS"))
        self._process.start()

    def _read_stdout(self) -> None:
        """增量解析 worker stdout 的 NDJSON。"""

        self._buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.failed_signal.emit(f"worker 输出了无效协议行: {line}")
                continue
            if isinstance(event, dict):
                self._handle_event(event)

    def _read_stderr(self) -> None:
        """收集 worker 人类诊断，失败时一并展示。"""

        self._stderr += bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")

    def _handle_event(self, event: dict[str, object]) -> None:
        """把协议事件转换为 Qt signals。"""

        event_name = str(event.get("event", ""))
        if event_name == "started":
            self.started_signal.emit()
        elif event_name == "waiting_for_lock":
            self._waiting_for_lock = True
            remaining = event.get("remaining_seconds", 0)
            self.waiting_signal.emit(int(remaining) if isinstance(remaining, int) else 0)
        elif event_name == "progress":
            self._waiting_for_lock = False
            self.progress_signal.emit(str(event.get("stage", "")))
        elif event_name == "completed":
            self._terminal_event_seen = True
            report = event.get("report")
            if isinstance(report, dict):
                readiness = str(report.get("readiness", "unavailable"))
                self._set_readiness(readiness)
                self.completed_signal.emit(report)
        elif event_name == "failed":
            self._terminal_event_seen = True
            self._set_readiness("unavailable")
            self.failed_signal.emit(str(event.get("message") or event.get("reason") or "同步失败"))

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        """处理异常退出，并在需要时至多补跑一次。"""

        self._waiting_for_lock = False
        if exit_code != 0 and not self._terminal_event_seen:
            self._set_readiness("unavailable")
            self.failed_signal.emit(self._stderr.strip() or f"worker 异常退出: {exit_code}")
        if self._reconcile_pending:
            self._reconcile_pending = False
            self.reconcile_all()

    def _set_readiness(self, readiness: str) -> None:
        """更新并广播世界书索引可用性。"""

        if readiness == self._readiness:
            return
        self._readiness = readiness
        self.readiness_changed.emit(readiness)
