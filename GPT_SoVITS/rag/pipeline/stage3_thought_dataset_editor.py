"""Character Thought 风险优先人工复核查看器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_PACKAGE_ROOT = PIPELINE_ROOT.parents[1]
if str(PROJECT_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE_ROOT))

try:
    from nicegui import ui
except ImportError as exc:  # pragma: no cover - 运行期依赖提示
    raise SystemExit("缺少 nicegui 依赖，请先执行 `uv sync` 或安装 nicegui。") from exc

from rag.pipeline.schemas import (
    CharacterThoughtReviewRecord,
    Stage2InputArtifact,
    Stage2Utterance,
    Stage3ThoughtImportArtifact,
)


class ThoughtDatasetEditor:
    """提供风险筛选、证据上下文和复核状态编辑的轻量查看器。"""

    def __init__(self, artifact_path: Path, stage2_input_path: Path) -> None:
        """读取观点审查产物及其对应的 Stage 2 场景输入。"""

        self.artifact_path = artifact_path.resolve()
        self.artifact = Stage3ThoughtImportArtifact.model_validate_json(
            self.artifact_path.read_text(encoding="utf-8")
        )
        self.stage2_input = Stage2InputArtifact.model_validate_json(
            stage2_input_path.resolve().read_text(encoding="utf-8")
        )
        self.utterances = {
            utterance.u_id: utterance
            for scene in self.stage2_input.scenes
            for utterance in scene.utterances
        }
        self.scene_utterances = {
            scene.scene_id: scene.utterances
            for scene in self.stage2_input.scenes
        }
        self.risk_filter = "all"
        self.status_filter = "all"
        self.current_point_id: str | None = None
        self.list_column: object | None = None
        self.detail_column: object | None = None

    def filtered_records(self) -> list[CharacterThoughtReviewRecord]:
        """返回符合当前风险与复核状态筛选器的记录。"""

        records = self.artifact.character_thoughts
        if self.risk_filter != "all":
            records = [record for record in records if record.risk_level == self.risk_filter]
        if self.status_filter != "all":
            records = [record for record in records if record.review_status == self.status_filter]
        return sorted(records, key=lambda record: (-record.risk_score, record.point_id))

    def current_record(self) -> CharacterThoughtReviewRecord | None:
        """读取当前选中的观点记录。"""

        records = self.filtered_records()
        if not records:
            return None
        for record in records:
            if record.point_id == self.current_point_id:
                return record
        self.current_point_id = records[0].point_id
        return records[0]

    def select_record(self, point_id: str) -> None:
        """切换当前记录并刷新列表与详情。"""

        self.current_point_id = point_id
        self.refresh()

    def set_risk_filter(self, value: object) -> None:
        """更新风险等级筛选器。"""

        self.risk_filter = str(getattr(value, "value", value))
        self.current_point_id = None
        self.refresh()

    def set_status_filter(self, value: object) -> None:
        """更新人工复核状态筛选器。"""

        self.status_filter = str(getattr(value, "value", value))
        self.current_point_id = None
        self.refresh()

    def set_review_status(self, value: object) -> None:
        """修改当前观点的人工复核状态。"""

        record = self.current_record()
        if record is None:
            return
        record.review_status = str(getattr(value, "value", value))  # type: ignore[assignment]
        self.save()
        self.refresh()

    def set_thought_text(self, value: object) -> None:
        """人工修改当前观点文本，并把复核状态标记为 edited。"""

        record = self.current_record()
        if record is None or record.document is None:
            return
        text = str(getattr(value, "value", value)).strip()
        if not text or text == record.document.thought_text:
            return
        record.document.thought_text = text
        record.document.retrieval_text = f"{record.document.character_id.common_name}：{text}"
        record.review_status = "edited"
        self.save()
        self.refresh()

    def next_high_risk(self) -> None:
        """跳转到下一条尚未批准的高风险记录。"""

        candidates = [
            record
            for record in self.artifact.character_thoughts
            if record.risk_level == "high" and record.review_status not in {"approved", "edited", "rejected"}
        ]
        candidates.sort(key=lambda record: (-record.risk_score, record.point_id))
        if not candidates:
            ui.notify("没有待处理的高风险记录。", color="positive")
            return
        current_index = next(
            (index for index, record in enumerate(candidates) if record.point_id == self.current_point_id),
            -1,
        )
        self.current_point_id = candidates[(current_index + 1) % len(candidates)].point_id
        self.refresh()

    def save(self) -> None:
        """校验并保存当前审查产物。"""

        self.artifact_path.write_text(
            json.dumps(self.artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def evidence_context(self, record: CharacterThoughtReviewRecord) -> list[Stage2Utterance]:
        """返回证据台词及各自前后一条同场景上下文。"""

        evidence_ids = self.record_evidence_ids(record)
        result: list[Stage2Utterance] = []
        seen: set[str] = set()
        for evidence_id in evidence_ids:
            evidence = self.utterances.get(evidence_id)
            if evidence is None:
                continue
            scene_id = next(
                (
                    scene.scene_id
                    for scene in self.stage2_input.scenes
                    if any(item.u_id == evidence_id for item in scene.utterances)
                ),
                "",
            )
            scene_items = self.scene_utterances.get(scene_id, [])
            index = next((i for i, item in enumerate(scene_items) if item.u_id == evidence_id), 0)
            for item in scene_items[max(0, index - 1) : index + 2]:
                if item.u_id not in seen:
                    seen.add(item.u_id)
                    result.append(item)
        return result

    def record_evidence_ids(self, record: CharacterThoughtReviewRecord) -> list[str]:
        """读取最终文档或未解决更新所引用的台词证据 ID。"""

        if record.document is None:
            update_ids = set(record.source_update_ids)
            updates = [
                update for update in self.artifact.linked_updates if update.source_local_id in update_ids
            ]
            return list(dict.fromkeys(u_id for update in updates for u_id in update.evidence_u_ids))
        return record.document.evidence_u_ids

    def refresh(self) -> None:
        """重绘记录列表和当前详情区域。"""

        if self.list_column is None or self.detail_column is None:
            return
        list_column = self.list_column
        detail_column = self.detail_column
        list_column.clear()
        detail_column.clear()
        records = self.filtered_records()
        with list_column:
            ui.label(f"共 {len(records)} 条").classes("text-sm text-slate-500")
            for record in records:
                title = record.document.thought_text if record.document is not None else "未解决的观点链接"
                button = ui.button(
                    f"[{record.risk_level.upper()} {record.risk_score}] {title[:48]}",
                    on_click=lambda _=None, point_id=record.point_id: self.select_record(point_id),
                ).props("flat align=left").classes("w-full text-left")
                if record.point_id == self.current_point_id:
                    button.classes("bg-blue-50")

        record = self.current_record()
        with detail_column:
            if record is None:
                ui.label("当前筛选条件下没有记录。")
                return
            with ui.row().classes("items-center gap-2"):
                ui.badge(record.risk_level.upper(), color="red" if record.risk_level == "high" else "orange")
                ui.badge(f"risk {record.risk_score}")
                ui.badge(record.link_status)
                ui.badge(record.resolved_update_type)
            ui.label(record.point_id).classes("text-xs text-slate-500")
            if record.document is not None:
                ui.textarea(
                    label="Character Thought",
                    value=record.document.thought_text,
                    on_change=self.set_thought_text,
                ).classes("w-full")
                ui.label(
                    f"角色 {record.document.character_id.common_name} · {record.document.epistemic_status} · "
                    f"有效期 {record.document.valid_from}–{record.document.valid_to}"
                )
                ui.label(
                    f"事件 {record.document.about_event_id or '-'} · 事实 {record.document.about_fact_id or '-'} · "
                    f"独立主题 {record.document.standalone_topic_key or '-'}"
                ).classes("text-sm text-slate-600")
                thread_records = [
                    item for item in self.artifact.character_thoughts
                    if item.document is not None
                    and item.document.thought_thread_key == record.document.thought_thread_key
                ]
                ui.label(f"同一 Thought Thread 共 {len(thread_records)} 个状态").classes("text-sm")
                for thread_record in sorted(
                    thread_records,
                    key=lambda item: item.document.valid_from if item.document is not None else 0,
                ):
                    assert thread_record.document is not None
                    ui.label(
                        f"{thread_record.document.valid_from}–{thread_record.document.valid_to}："
                        f"{thread_record.document.thought_text}"
                    ).classes("text-xs text-slate-600")
            ui.select(
                ["unreviewed", "approved", "edited", "rejected", "needs_followup"],
                value=record.review_status,
                label="Review status",
                on_change=self.set_review_status,
            ).classes("w-72")
            ui.separator()
            ui.label("风险原因").classes("font-semibold")
            for reason in record.risk_reasons or ["无自动风险标记"]:
                ui.label(f"• {reason}")
            for error in record.validation_errors:
                ui.label(f"• 阻止入库：{error}").classes("text-red-700")
            ui.separator()
            ui.label("证据及前后文").classes("font-semibold")
            evidence_ids = set(self.record_evidence_ids(record))
            for utterance in self.evidence_context(record):
                marker = "证据" if utterance.u_id in evidence_ids else "上下文"
                ui.label(
                    f"[{marker}] {utterance.u_id} · {utterance.speaker_name or '未知'}：{utterance.zh_text}"
                ).classes("text-sm")

    def run(self) -> None:
        """构建并启动 NiceGUI 页面。"""

        ui.page_title("Character Thought Review")
        with ui.column().classes("w-full max-w-[1500px] mx-auto p-5 gap-4"):
            ui.label("Character Thought 风险复核").classes("text-2xl font-bold")
            with ui.row().classes("items-center gap-3"):
                ui.select(["all", "high", "medium", "low"], value="all", label="风险", on_change=self.set_risk_filter)
                ui.select(
                    ["all", "unreviewed", "approved", "edited", "rejected", "needs_followup"],
                    value="all",
                    label="复核状态",
                    on_change=self.set_status_filter,
                )
                ui.button("下一条高风险", on_click=self.next_high_risk)
            with ui.row().classes("w-full items-start gap-5"):
                self.list_column = ui.column().classes("w-[430px] max-h-[80vh] overflow-auto")
                self.detail_column = ui.column().classes("flex-1 min-w-0")
        self.refresh()
        ui.run(title="Character Thought Review", reload=False)


def _build_parser() -> argparse.ArgumentParser:
    """构建 Character Thought 查看器命令行参数。"""

    parser = argparse.ArgumentParser(description="Character Thought 风险优先数据集查看器")
    parser.add_argument("--input", required=True, help="Stage 3 Character Thought 审查 JSON")
    parser.add_argument("--stage2-input", required=True, help="对应的 Stage 2 输入 JSON")
    return parser


def main() -> None:
    """解析参数并启动查看器。"""

    args = _build_parser().parse_args()
    ThoughtDatasetEditor(Path(args.input), Path(args.stage2_input)).run()


if __name__ in {"__main__", "__mp_main__"}:
    main()
