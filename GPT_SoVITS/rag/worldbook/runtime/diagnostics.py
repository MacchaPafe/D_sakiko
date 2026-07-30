"""收集、短期滚动保存并按对话导出世界书诊断。"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import threading
from typing import Literal
from uuid import uuid4
import zipfile

from log import get_logger

from .models import WorldbookKnowledgeResult, WorldbookTurnSnapshot
from .tools import WorldbookToolDiagnostic


logger = get_logger(__name__)


class DirectRetrievalDiagnostic:
    """保存一次直接双来源检索的内部诊断数据。"""

    def __init__(
        self,
        query: str,
        result: WorldbookKnowledgeResult,
    ) -> None:
        """复制直接检索数据，避免后续调用修改。"""

        self.query = query
        self.result = result.model_copy(deep=True)

    def to_dict(self) -> dict[str, object]:
        """转换成可写入诊断 JSON 的字典。"""

        return {
            "query": self.query,
            "thought_candidates": [
                item.model_dump(mode="json")
                for item in self.result.thought_trace.candidates
            ],
            "selected_thought_ids": [
                str(entry_id)
                for entry_id in self.result.thought_trace.selected_entry_ids
            ],
            "event_candidates": [
                item.model_dump(mode="json")
                for item in self.result.event_trace.candidates
            ],
            "selected_event_ids": [
                str(entry_id)
                for entry_id in self.result.event_trace.selected_entry_ids
            ],
            "linked_event_ids": [
                str(entry_id) for entry_id in self.result.linked_event_ids
            ],
            "unauthorized_linked_event_ids": [
                str(entry_id)
                for entry_id in self.result.unauthorized_linked_event_ids
            ],
            "deduplicated_event_ids": [
                str(entry_id)
                for entry_id in self.result.deduplicated_event_ids
            ],
            "source_failures": [
                item.model_dump(mode="json")
                for item in self.result.source_failures
            ],
            "source_durations_sec": dict(self.result.source_durations_sec),
            "injected_context": self.result.knowledge.model_dump(mode="json"),
        }


class WorldbookDiagnosticRecord:
    """表示一个已完成、失败或取消的世界书对话回合。"""

    def __init__(
        self,
        *,
        record_id: str,
        chat_id: str,
        turn_id: str,
        status: Literal["completed", "failed", "cancelled"],
        started_at: str,
        finished_at: str,
        snapshot: WorldbookTurnSnapshot,
        current_user_text: str,
        recent_conversation_text: str,
        model: str,
        direct_retrieval: DirectRetrievalDiagnostic | None,
        tool_calls: list[WorldbookToolDiagnostic],
        candidate_response: str,
        final_response: str,
        errors: list[str],
    ) -> None:
        """保存一份只包含允许字段的不可变诊断记录。"""

        self.record_id = record_id
        self.chat_id = chat_id
        self.turn_id = turn_id
        self.status = status
        self.started_at = started_at
        self.finished_at = finished_at
        self.snapshot = snapshot
        self.current_user_text = current_user_text
        self.recent_conversation_text = recent_conversation_text
        self.model = model
        self.direct_retrieval = direct_retrieval
        self.tool_calls = list(tool_calls)
        self.candidate_response = candidate_response
        self.final_response = final_response
        self.errors = list(errors)

    def to_dict(self) -> dict[str, object]:
        """转换成独立带格式版本的 JSON 记录。"""

        return {
            "format_version": 2,
            "record_id": self.record_id,
            "chat_id": self.chat_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "snapshot": self.snapshot.model_dump(mode="json"),
            "current_user_text": self.current_user_text,
            "recent_conversation_text": self.recent_conversation_text,
            "model": self.model,
            "direct_retrieval": (
                self.direct_retrieval.to_dict()
                if self.direct_retrieval is not None
                else None
            ),
            "tool_calls": [
                {
                    "tool_name": item.tool_name,
                    "arguments": dict(item.arguments),
                    "selected_entry_ids": [
                        str(entry_id) for entry_id in item.selected_entry_ids
                    ],
                    "candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in item.candidates
                    ],
                    "result": dict(item.result),
                    "duration_sec": item.duration_sec,
                    "thought_candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in item.thought_candidates
                    ],
                    "selected_thought_ids": [
                        str(entry_id)
                        for entry_id in item.thought_selected_entry_ids
                    ],
                    "event_candidates": [
                        candidate.model_dump(mode="json")
                        for candidate in item.event_candidates
                    ],
                    "selected_event_ids": [
                        str(entry_id)
                        for entry_id in item.event_selected_entry_ids
                    ],
                    "linked_event_ids": [
                        str(entry_id) for entry_id in item.linked_event_ids
                    ],
                    "unauthorized_linked_event_ids": [
                        str(entry_id)
                        for entry_id in item.unauthorized_linked_event_ids
                    ],
                    "deduplicated_event_ids": [
                        str(entry_id)
                        for entry_id in item.deduplicated_event_ids
                    ],
                    "source_failures": [
                        failure.model_dump(mode="json")
                        for failure in item.source_failures
                    ],
                    "source_durations_sec": dict(item.source_durations_sec),
                }
                for item in self.tool_calls
            ],
            "candidate_response": self.candidate_response,
            "final_response": self.final_response,
            "errors": list(self.errors),
        }


class WorldbookDiagnosticStore:
    """保留最近十回合内存记录，并可选写入按日滚动 JSONL。"""

    def __init__(
        self,
        log_path: Path,
        *,
        memory_limit: int = 10,
        backup_count: int = 3,
    ) -> None:
        """配置线程安全内存队列和标准库日轮转 handler。"""

        self._records: deque[WorldbookDiagnosticRecord] = deque(maxlen=memory_limit)
        self._lock = threading.Lock()
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._disk_logger = logging.getLogger(
            f"worldbook-diagnostics-{id(self)}"
        )
        self._disk_logger.setLevel(logging.INFO)
        self._disk_logger.propagate = False
        self._handler = TimedRotatingFileHandler(
            self.log_path,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            utc=False,
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._disk_logger.addHandler(self._handler)

    def append(
        self,
        record: WorldbookDiagnosticRecord,
        *,
        persist: bool,
    ) -> None:
        """始终写入内存；持久化开启时额外写入滚动日志。"""

        with self._lock:
            self._records.append(record)
        if not persist:
            return
        try:
            self._disk_logger.info(
                json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
            )
        except Exception:
            logger.exception("写入世界书诊断日志失败")

    def recent(self, limit: int = 10) -> list[WorldbookDiagnosticRecord]:
        """返回本次程序运行中最新的若干诊断记录。"""

        with self._lock:
            records = list(self._records)
        return records[-max(0, limit) :]

    def export_chat(self, chat_id: str, output_path: Path) -> int:
        """把当前日志和内存中属于一个对话的记录去重导出为 ZIP。"""

        records: dict[str, dict[str, object]] = {}
        for path in self._diagnostic_log_paths():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("跳过损坏的世界书诊断行：%s", path)
                        continue
                    if (
                        isinstance(raw, dict)
                        and raw.get("chat_id") == chat_id
                        and isinstance(raw.get("record_id"), str)
                    ):
                        records[str(raw["record_id"])] = raw
            except OSError:
                logger.exception("读取世界书诊断日志失败：%s", path)
        for record in self.recent(10):
            if record.chat_id == chat_id:
                records[record.record_id] = record.to_dict()
        ordered = sorted(
            records.values(),
            key=lambda item: str(item.get("started_at") or ""),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            jsonl = "\n".join(
                json.dumps(item, ensure_ascii=False) for item in ordered
            )
            if jsonl:
                jsonl += "\n"
            archive.writestr("worldbook_diagnostics.jsonl", jsonl)
            archive.writestr(
                "README.txt",
                "该压缩包只包含所选对话的世界书诊断。"
                "其中可能含相关用户消息、模型文本和检索内容；不包含 API Key、音频或完整 system prompt。\n",
            )
            archive.writestr(
                "summary.json",
                json.dumps(
                    {
                        "format_version": 2,
                        "chat_id": chat_id,
                        "record_count": len(ordered),
                        "exported_at": _utc_now(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return len(ordered)

    def close(self) -> None:
        """刷新并关闭滚动日志 handler。"""

        self._handler.flush()
        self._handler.close()
        self._disk_logger.removeHandler(self._handler)

    def _diagnostic_log_paths(self) -> list[Path]:
        """返回当前文件和标准轮转备份文件。"""

        paths = [self.log_path]
        paths.extend(sorted(self.log_path.parent.glob(f"{self.log_path.name}.*")))
        return [path for path in paths if path.is_file()]


class WorldbookDiagnosticCollector:
    """在一个世界书启用回合中渐进收集信息并且只完成一次。"""

    def __init__(
        self,
        store: WorldbookDiagnosticStore,
        *,
        chat_id: str,
        turn_id: str,
        snapshot: WorldbookTurnSnapshot,
        current_user_text: str,
        recent_conversation_text: str,
        model: str,
        persist: bool,
    ) -> None:
        """创建尚未完成的单回合 collector。"""

        self._store = store
        self._persist = persist
        self._chat_id = chat_id
        self._turn_id = turn_id
        self._snapshot = snapshot
        self._current_user_text = current_user_text
        self._recent_conversation_text = recent_conversation_text
        self._model = model
        self._started_at = _utc_now()
        self._direct: DirectRetrievalDiagnostic | None = None
        self._tool_calls: list[WorldbookToolDiagnostic] = []
        self._candidate_response = ""
        self._final_response = ""
        self._errors: list[str] = []
        self._finished = False

    def record_direct(
        self,
        query: str,
        result: WorldbookKnowledgeResult,
    ) -> None:
        """保存直接 Event 与 Thought 双来源查询详情。"""

        self._direct = DirectRetrievalDiagnostic(query, result)

    def record_tools(self, calls: list[WorldbookToolDiagnostic]) -> None:
        """保存本轮全部隐藏世界书工具诊断。"""

        self._tool_calls = list(calls)

    def set_candidate_response(self, text: str) -> None:
        """保存 JSON 格式收口之前的模型候选回答。"""

        self._candidate_response = text

    def set_final_response(self, text: str) -> None:
        """保存最终用户可见的回复文本。"""

        self._final_response = text

    def add_error(self, message: str) -> None:
        """追加一条不含凭据的诊断错误说明。"""

        if message:
            self._errors.append(message)

    def finish(
        self,
        status: Literal["completed", "failed", "cancelled"],
    ) -> WorldbookDiagnosticRecord | None:
        """幂等完成记录并交给内存／滚动存储。"""

        if self._finished:
            return None
        self._finished = True
        record = WorldbookDiagnosticRecord(
            record_id=uuid4().hex,
            chat_id=self._chat_id,
            turn_id=self._turn_id,
            status=status,
            started_at=self._started_at,
            finished_at=_utc_now(),
            snapshot=self._snapshot,
            current_user_text=self._current_user_text,
            recent_conversation_text=self._recent_conversation_text,
            model=self._model,
            direct_retrieval=self._direct,
            tool_calls=self._tool_calls,
            candidate_response=self._candidate_response,
            final_response=self._final_response,
            errors=self._errors,
        )
        self._store.append(record, persist=self._persist)
        return record


def _utc_now() -> str:
    """返回便于跨时区排序的 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()
