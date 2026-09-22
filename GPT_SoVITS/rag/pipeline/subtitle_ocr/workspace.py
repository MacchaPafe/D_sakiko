"""管理字幕 OCR review 草稿、编辑历史和安全保存。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .models import OCRReviewArtifact, ReviewStatus, SubtitleReviewEvent, utc_now_text
from .storage import atomic_write_model, json_file_sha256, load_review


@dataclass(slots=True)
class OCRReviewWorkspace:
    """管理单集字幕审核文件的内存草稿和撤销历史。"""

    path: Path
    artifact: OCRReviewArtifact
    loaded_sha256: str
    dirty: bool = False
    undo_stack: list[OCRReviewArtifact] = field(default_factory=list)
    redo_stack: list[OCRReviewArtifact] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> OCRReviewWorkspace:
        """从磁盘加载一个字幕审核工作区。"""

        target = Path(path).resolve()
        return cls(
            path=target,
            artifact=load_review(target),
            loaded_sha256=json_file_sha256(target),
        )

    def _begin_change(self) -> None:
        """在变更前保存可撤销快照。"""

        self.undo_stack.append(self.artifact.model_copy(deep=True))
        self.redo_stack.clear()

    def _finish_change(self) -> None:
        """完成一次变更并更新修订信息。"""

        self.artifact.revision += 1
        self.artifact.updated_at = utc_now_text()
        self.dirty = True

    def event(self, event_id: str) -> SubtitleReviewEvent:
        """按稳定 ID 返回一个字幕事件。"""

        for event in self.artifact.events:
            if event.event_id == event_id:
                return event
        raise KeyError(f"未知字幕事件: {event_id}")

    def update_event(
        self,
        event_id: str,
        *,
        text: str,
        start_ms: int,
        end_ms: int,
        status: ReviewStatus = "accepted",
    ) -> SubtitleReviewEvent:
        """修改字幕正文和时间，并把事件标记为人工编辑。"""

        if not text.strip():
            raise ValueError("非删除字幕的正文不能为空")
        if end_ms <= start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间")
        self._begin_change()
        event = self.event(event_id)
        event.text = text.strip()
        event.start_ms = start_ms
        event.end_ms = end_ms
        event.status = status
        event.human_edited = True
        event.deletion_reason = None
        self._sort_events()
        self._finish_change()
        return event

    def set_status(self, event_id: str, status: ReviewStatus) -> SubtitleReviewEvent:
        """修改一个字幕事件的审核状态。"""

        self._begin_change()
        event = self.event(event_id)
        event.status = status
        event.human_edited = True
        if status != "deleted":
            event.deletion_reason = None
        self._finish_change()
        return event

    def delete_events(self, event_ids: list[str], reason: str) -> int:
        """批量软删除字幕事件并保留原因。"""

        selected = set(event_ids)
        if not selected:
            return 0
        self._begin_change()
        changed = 0
        for event in self.artifact.events:
            if event.event_id not in selected:
                continue
            event.status = "deleted"
            event.deletion_reason = reason.strip() or "人工删除"
            event.human_edited = True
            changed += 1
        if changed == 0:
            self.undo_stack.pop()
            return 0
        self._finish_change()
        return changed

    def restore_events(self, event_ids: list[str]) -> int:
        """批量恢复软删除字幕并标记为人工通过。"""

        selected = set(event_ids)
        if not selected:
            return 0
        self._begin_change()
        changed = 0
        for event in self.artifact.events:
            if event.event_id not in selected or event.status != "deleted":
                continue
            event.status = "accepted"
            event.deletion_reason = None
            event.human_edited = True
            changed += 1
        if changed == 0:
            self.undo_stack.pop()
            return 0
        self._finish_change()
        return changed

    def delete_time_range(self, start_ms: int, end_ms: int, reason: str) -> int:
        """软删除与指定时间范围有交集的全部字幕。"""

        if end_ms <= start_ms:
            raise ValueError("批量删除时间范围无效")
        event_ids = [
            event.event_id
            for event in self.artifact.events
            if event.end_ms > start_ms and event.start_ms < end_ms
        ]
        return self.delete_events(event_ids, reason)

    def add_event(self, start_ms: int, end_ms: int, text: str) -> SubtitleReviewEvent:
        """新增一条人工字幕事件。"""

        if not text.strip():
            raise ValueError("新增字幕正文不能为空")
        if end_ms <= start_ms:
            raise ValueError("新增字幕结束时间必须晚于开始时间")
        self._begin_change()
        event = SubtitleReviewEvent(
            event_id=f"ocr_event:manual:{uuid4()}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=text.strip(),
            status="accepted",
            reasons=["manual_addition"],
            confidence=1.0,
            representative_timestamp_ms=(start_ms + end_ms) // 2,
            human_edited=True,
        )
        self.artifact.events.append(event)
        self._sort_events()
        self._finish_change()
        return event

    def split_event(self, event_id: str, split_ms: int, second_text: str) -> tuple[str, str]:
        """在指定时间把一条字幕拆成两条。"""

        event = self.event(event_id)
        if not event.start_ms < split_ms < event.end_ms:
            raise ValueError("拆分时间必须位于字幕事件内部")
        if not second_text.strip():
            raise ValueError("拆分后的第二条字幕正文不能为空")
        self._begin_change()
        original_end = event.end_ms
        event.end_ms = split_ms
        event.status = "accepted"
        event.human_edited = True
        second = event.model_copy(
            deep=True,
            update={
                "event_id": f"ocr_event:manual:{uuid4()}",
                "start_ms": split_ms,
                "end_ms": original_end,
                "text": second_text.strip(),
                "representative_timestamp_ms": (split_ms + original_end) // 2,
                "observation_timestamps_ms": [],
                "candidates": [],
                "evidence_full_frame": None,
                "evidence_crop": None,
            },
        )
        self.artifact.events.append(second)
        self._sort_events()
        self._finish_change()
        return event.event_id, second.event_id

    def merge_events(self, first_id: str, second_id: str, text: str) -> SubtitleReviewEvent:
        """合并两条相邻字幕并软删除第二条。"""

        first = self.event(first_id)
        second = self.event(second_id)
        ordered = sorted((first, second), key=lambda item: item.start_ms)
        if ordered[0].status == "deleted" or ordered[1].status == "deleted":
            raise ValueError("不能合并已删除字幕")
        self._begin_change()
        target, consumed = ordered
        target.start_ms = min(first.start_ms, second.start_ms)
        target.end_ms = max(first.end_ms, second.end_ms)
        target.text = text.strip() or f"{target.text}\n{consumed.text}"
        target.status = "accepted"
        target.human_edited = True
        target.reasons = sorted(set([*target.reasons, *consumed.reasons, "manual_merge"]))
        consumed.status = "deleted"
        consumed.deletion_reason = f"合并到 {target.event_id}"
        consumed.human_edited = True
        self._finish_change()
        return target

    def undo(self) -> bool:
        """撤销上一个会话内编辑命令。"""

        if not self.undo_stack:
            return False
        self.redo_stack.append(self.artifact.model_copy(deep=True))
        self.artifact = self.undo_stack.pop()
        self.dirty = True
        return True

    def redo(self) -> bool:
        """重做刚撤销的会话内编辑命令。"""

        if not self.redo_stack:
            return False
        self.undo_stack.append(self.artifact.model_copy(deep=True))
        self.artifact = self.redo_stack.pop()
        self.dirty = True
        return True

    def save(self) -> Path:
        """检测外部修改后原子保存当前 review 草稿。"""

        if self.path.exists() and json_file_sha256(self.path) != self.loaded_sha256:
            raise RuntimeError("review JSON 已被其他程序修改，请重新加载后再保存")
        validated = OCRReviewArtifact.model_validate(self.artifact.model_dump(mode="json"))
        atomic_write_model(validated, self.path)
        self.artifact = validated
        self.loaded_sha256 = json_file_sha256(self.path)
        self.dirty = False
        return self.path

    def _sort_events(self) -> None:
        """按字幕开始、结束和稳定 ID 排序。"""

        self.artifact.events.sort(
            key=lambda event: (event.start_ms, event.end_ms, event.event_id)
        )
