"""世界书诊断 collector、滚动日志和按对话导出测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4
import zipfile

from dp_local2 import DSLocalAndVoiceGen
from rag.worldbook.runtime.diagnostics import (
    WorldbookDiagnosticCollector,
    WorldbookDiagnosticStore,
)
from rag.worldbook.runtime.models import (
    DirectWorldbookContext,
    KnownStoryEvent,
    RetrievalCandidate,
    RetrievalTrace,
    WorldbookKnowledgeResult,
    WorldbookTurnSnapshot,
)


def _snapshot() -> WorldbookTurnSnapshot:
    """创建诊断测试使用的冻结快照。"""

    return WorldbookTurnSnapshot(
        root_package_id="root",
        root_package_version="1.0.0",
        package_ids=["root"],
        package_versions={"root": "1.0.0"},
        package_depths={"root": 0},
        character_id="anon",
        series_id="its_mygo",
        timeline_id="bang_dream_original",
        canon_branch="main",
        current_time=4099,
        story_year=3,
        episode=2,
    )


def _collector(
    store: WorldbookDiagnosticStore,
    turn_id: str,
    *,
    chat_id: str = "chat-a",
    persist: bool = False,
) -> WorldbookDiagnosticCollector:
    """创建一份最小单回合诊断 collector。"""

    return WorldbookDiagnosticCollector(
        store,
        chat_id=chat_id,
        turn_id=turn_id,
        snapshot=_snapshot(),
        current_user_text=f"用户消息 {turn_id}",
        recent_conversation_text="上一轮消息",
        model="test/model",
        persist=persist,
    )


class WorldbookDiagnosticsTest(unittest.TestCase):
    """验证诊断只完成一次、内存上限、轮转配置和过滤导出。"""

    def test_collector_finishes_only_once_and_memory_keeps_ten(self) -> None:
        """重复完成不得重复写入，内存只保留最近十回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store = WorldbookDiagnosticStore(Path(directory) / "diagnostics.jsonl")
            first = _collector(store, "turn-0")
            self.assertIsNotNone(first.finish("completed"))
            self.assertIsNone(first.finish("failed"))
            for index in range(1, 12):
                _collector(store, f"turn-{index}").finish("completed")

            recent = store.recent()
            store.close()

        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0].turn_id, "turn-2")
        self.assertEqual(recent[-1].turn_id, "turn-11")

    def test_direct_diagnostic_uses_v2_split_source_fields(self) -> None:
        """直接诊断应分别记录 Event、Thought 候选与实际注入。"""

        event_id = uuid4()
        candidate = RetrievalCandidate(
            entry_id=event_id,
            package_id="root",
            entry_type="story_event",
            payload={"title": "初次相遇"},
            score=0.9,
            final_score=0.9,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = WorldbookDiagnosticStore(Path(directory) / "diagnostics.jsonl")
            collector = _collector(store, "turn-v2")
            collector.record_direct(
                "怎么认识灯",
                WorldbookKnowledgeResult(
                    knowledge=DirectWorldbookContext(
                        events=[
                            KnownStoryEvent(
                                title="初次相遇",
                                summary="爱音摔倒，灯递给她创可贴。",
                                participant_names=["爱音", "灯"],
                            )
                        ]
                    ),
                    event_trace=RetrievalTrace(
                        selected_entry_ids=[event_id],
                        candidates=[candidate],
                    ),
                ),
            )
            record = collector.finish("completed")
            store.close()

        self.assertIsNotNone(record)
        if record is None:
            self.fail("诊断记录没有完成")
        payload = record.to_dict()
        direct = payload["direct_retrieval"]
        self.assertEqual(payload["format_version"], 2)
        self.assertIsInstance(direct, dict)
        if not isinstance(direct, dict):
            self.fail("直接诊断不是字典")
        self.assertEqual(direct["selected_event_ids"], [str(event_id)])
        self.assertEqual(
            direct["injected_context"]["events"][0]["title"],
            "初次相遇",
        )

    def test_timed_rotating_handler_uses_daily_three_backups(self) -> None:
        """磁盘诊断应直接使用标准库按日轮转和三个备份。"""

        with tempfile.TemporaryDirectory() as directory:
            store = WorldbookDiagnosticStore(Path(directory) / "diagnostics.jsonl")

            self.assertEqual(store._handler.backupCount, 3)
            self.assertEqual(store._handler.interval, 86400)
            store.close()

    def test_persistence_off_does_not_write_record(self) -> None:
        """关闭持久化时仍保留内存，但日志文件中不得出现记录。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            store = WorldbookDiagnosticStore(path)
            _collector(store, "memory-only", persist=False).finish("completed")
            store.close()

            content = path.read_text(encoding="utf-8")

        self.assertEqual(content, "")

    def test_export_filters_chat_deduplicates_and_includes_readme(self) -> None:
        """导出只保留目标对话，并按 record_id 合并日志与内存记录。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "diagnostics.jsonl"
            store = WorldbookDiagnosticStore(path)
            _collector(store, "a-1", chat_id="chat-a", persist=True).finish("completed")
            _collector(store, "b-1", chat_id="chat-b", persist=True).finish("failed")
            store._handler.flush()
            with path.open("a", encoding="utf-8") as stream:
                stream.write("{broken-json\n")
            output = root / "export.zip"

            count = store.export_chat("chat-a", output)
            store.close()

            with zipfile.ZipFile(output, "r") as archive:
                names = set(archive.namelist())
                rows = [
                    json.loads(line)
                    for line in archive.read("worldbook_diagnostics.jsonl")
                    .decode("utf-8")
                    .splitlines()
                ]
                summary = json.loads(archive.read("summary.json").decode("utf-8"))

        self.assertEqual(count, 1)
        self.assertEqual({row["chat_id"] for row in rows}, {"chat-a"})
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(
            names,
            {"worldbook_diagnostics.jsonl", "README.txt", "summary.json"},
        )

    def test_backend_emits_the_same_completed_record_to_ui(self) -> None:
        """后台完成 collector 后应把同一份结构化记录发给 UI。"""

        with tempfile.TemporaryDirectory() as directory:
            store = WorldbookDiagnosticStore(Path(directory) / "diagnostics.jsonl")
            collector = _collector(store, "turn-ui")
            backend = DSLocalAndVoiceGen.__new__(DSLocalAndVoiceGen)
            backend._worldbook_diagnostic_collectors = {
                ("chat-a", "turn-ui"): collector
            }
            queue = mock.Mock()

            backend._finish_worldbook_diagnostic(
                "chat-a",
                "turn-ui",
                "completed",
                queue,
            )

            recent = store.recent()
            store.close()

        self.assertEqual(len(recent), 1)
        emitted = queue.put.call_args.args[0]
        self.assertEqual(emitted["type"], "worldbook_diagnostic")
        self.assertEqual(emitted["record"], recent[0].to_dict())


if __name__ == "__main__":
    unittest.main()
