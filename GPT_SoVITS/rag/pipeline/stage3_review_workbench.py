"""NiceGUI 驱动的统一 Stage 3 审核工作台。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys
from uuid import uuid4

from pydantic import BaseModel

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_PACKAGE_ROOT = PIPELINE_ROOT.parents[1]
if str(PROJECT_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE_ROOT))

try:
    from nicegui import run, ui
except ImportError as exc:  # pragma: no cover - 运行期依赖提示
    raise SystemExit("缺少 nicegui 依赖，请先执行 `uv sync` 或安装 nicegui。") from exc

from rag.models import CharacterId
from rag.pipeline.review_models import ReviewFields
from rag.pipeline.review_reason_catalog import (
    ReviewItemKind,
    disposition_badge_label,
    disposition_description,
    review_reason_by_key,
    review_reason_options,
)
from rag.pipeline.schemas import (
    LoreEntryPayload,
    RelationObservationReviewRecord,
    StoryEventPayload,
)
from rag.pipeline.stage3_document_models import (
    LoreEntryReviewRecord,
    Stage3DocumentReviewArtifact,
    StoryEventReviewRecord,
)
from rag.pipeline.stage3_lore_models import LoreDedupDecisionRecord, Stage3LoreDecisionsArtifact
from rag.pipeline.stage3_relation_models import (
    RelationStateDraft,
    RelationTypeContentDraft,
    RelationTypeReviewRecord,
    Stage3RelationReviewArtifact,
    UnmergedRelationObservationDecision,
)
from rag.pipeline.stage3_review_operations import (
    AssignRelationObservationCommand,
    AssignThoughtUpdateCommand,
    BatchCompleteReviewCommand,
    ClearLoreDecisionCommand,
    CompleteItemReviewCommand,
    CompleteLoreDecisionCommand,
    MarkItemFollowupCommand,
    MergeRelationTypesCommand,
    MergeThoughtThreadsCommand,
    ReplaceLoreDocumentCommand,
    ReplaceRelationContentCommand,
    ReplaceStoryDocumentCommand,
    ReplaceThoughtContentCommand,
    ResolveIdentityCommand,
    RestoreGeneratedContentCommand,
    SplitRelationTypeCommand,
    SplitThoughtThreadCommand,
    UpdateItemNotesCommand,
)
from rag.pipeline.stage3_review_regeneration import (
    RegenerationResult,
    Stage3ReviewRegenerator,
)
from rag.pipeline.stage3_review_workspace import ArtifactSlot, ReviewWorkspace
from rag.pipeline.stage3_thought_models import (
    Stage3ThoughtReviewArtifact,
    ThoughtStateDraft,
    ThoughtThreadContentDraft,
    ThoughtThreadReviewRecord,
    ThoughtUpdateEvidence,
    UnassignedThoughtUpdateDecision,
)


DEFAULT_BUILD_SPEC = (
    PROJECT_PACKAGE_ROOT / "rag" / "annotated_data" / "its_mygo" / "worldbook_build.json"
)

LORE_DEDUP_ACTION_OPTIONS = {
    "keep_separate": "分别保留（全部发布）",
    "merge": "合并为一条（仅保留所选候选）",
    "drop": "整组丢弃（全部不发布）",
}

LORE_DECISION_STATUS_LABELS = {
    "pending": "待处理",
    "completed": "已完成",
    "automatic": "自动完成",
}


@dataclass(frozen=True, slots=True)
class ReviewListItem:
    """表示左侧统一审核队列中的一项。"""

    slot_key: str
    item_id: str
    kind: str
    title: str
    review_status: str
    disposition: str | None
    risk_level: str
    identity_pending: bool
    human_edited: bool


class Stage3ReviewWorkbench:
    """提供统一队列、类型化表单、证据对比与审核操作。"""

    def __init__(self, build_spec_path: Path) -> None:
        """创建工作区并初始化界面选择状态。"""

        self.workspace = ReviewWorkspace(build_spec_path)
        self.regenerator = Stage3ReviewRegenerator(build_spec_path)
        self.current_section = "overview"
        self.current_item_id: str | None = None
        self.search_text = ""
        self.status_filter = "all"
        self.selected_items: set[tuple[str, str]] = set()
        self.last_audit_messages: list[str] = []

        self.navigation_column: ui.column | None = None
        self.queue_column: ui.column | None = None
        self.detail_column: ui.column | None = None
        self.evidence_column: ui.column | None = None
        self.summary_column: ui.column | None = None
        self.status_badge: ui.badge | None = None
        self.dirty_badge: ui.badge | None = None

    def build_ui(self) -> None:
        """构建工作台页面。"""

        ui.page_title("Stage 3 审核工作台")
        ui.add_css(
            """
            body { background: #f4f7fb; color: #172033; }
            .workbench-shell { width: min(1880px, calc(100vw - 28px)); margin: 0 auto; }
            .wb-card { background: rgba(255,255,255,.94); border: 1px solid #dce4ef;
                       border-radius: 16px; box-shadow: 0 10px 28px rgba(39,56,82,.06); }
            .queue-card { cursor: pointer; border-left: 4px solid #a7b5c8; }
            .queue-card.active { border-left-color: #315efb; background: #f2f6ff; }
            .mono-wrap { white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, monospace; }
            .field-hint { color: #6d7a90; font-size: 12px; }
            """
        )
        with ui.header().classes("workbench-shell wb-card mt-3 px-5 py-3 items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("Stage 3 审核工作台").classes("text-2xl font-bold")
                ui.label(str(self.workspace.resolved.path)).classes("text-xs text-slate-500")
            with ui.row().classes("items-center gap-2"):
                self.status_badge = ui.badge()
                self.dirty_badge = ui.badge()
                ui.button("撤销", on_click=self.undo).props("outline")
                ui.button("重做", on_click=self.redo).props("outline")
                ui.button("保存当前", on_click=self.save_current).props(
                    "outline id=wb-save-current"
                )
                ui.button("保存全部", on_click=self.save_all).props("unelevated color=primary")
                ui.button("重生成", on_click=self.open_regeneration_dialog).props(
                    "outline color=warning"
                )
                ui.button("运行审计", on_click=self.run_audit).props("outline color=secondary")

        with ui.row().classes("workbench-shell w-full items-start gap-4 py-4").style("flex-wrap: nowrap;"):
            with ui.column().classes("gap-4").style("flex: 0 0 300px; min-width: 260px;"):
                with ui.card().classes("wb-card w-full p-4 gap-3"):
                    ui.label("审核域").classes("text-lg font-semibold")
                    self.navigation_column = ui.column().classes("w-full gap-2")
                with ui.card().classes("wb-card w-full p-4 gap-3"):
                    ui.input(
                        "搜索标题、ID、角色",
                        on_change=lambda event: self.set_search(_event_value(event)),
                    ).props("outlined dense clearable")
                    ui.select(
                        {
                            "all": "全部状态",
                            "unreviewed": "未审核",
                            "needs_followup": "待跟进",
                            "completed": "已完成",
                        },
                        value="all",
                        on_change=lambda event: self.set_status_filter(_event_value(event)),
                    ).props("outlined dense")
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("审核队列").classes("text-lg font-semibold")
                        ui.button("批量审核通过", on_click=self.batch_complete).props("flat dense")
                    with ui.scroll_area().classes("w-full").style("height: 690px;"):
                        self.queue_column = ui.column().classes("w-full gap-2")

            with ui.column().classes("gap-4").style("flex: 1 1 680px; min-width: 480px;"):
                with ui.card().classes("wb-card w-full p-5 gap-4"):
                    self.summary_column = ui.column().classes("w-full gap-3")
                with ui.card().classes("wb-card w-full p-5 gap-4"):
                    self.detail_column = ui.column().classes("w-full gap-4")

            with ui.column().classes("gap-4").style("flex: 0 1 480px; min-width: 320px;"):
                with ui.card().classes("wb-card w-full p-5 gap-4"):
                    ui.label("证据与对比").classes("text-lg font-semibold")
                    self.evidence_column = ui.column().classes("w-full gap-4")

        self._install_shortcuts()
        self.refresh()

    def _install_shortcuts(self) -> None:
        """安装保存、撤销与重做快捷键。"""

        ui.add_head_html(
            """
            <script>
            window.addEventListener('keydown', function(event) {
              if (!(event.metaKey || event.ctrlKey)) return;
              const key = event.key.toLowerCase();
              const active = event.target;
              if (key === 's') {
                event.preventDefault();
                document.getElementById('wb-save-current')?.click();
              }
            });
            </script>
            """
        )

    def set_section(self, section: str) -> None:
        """切换审核域并重置当前条目。"""

        self.current_section = section
        self.current_item_id = None
        self.refresh()

    def set_search(self, value: str) -> None:
        """更新队列文本搜索。"""

        self.search_text = value.strip().lower()
        self.refresh_queue()

    def set_status_filter(self, value: str) -> None:
        """更新队列审核状态过滤器。"""

        self.status_filter = value
        self.refresh_queue()

    def select_item(self, item: ReviewListItem) -> None:
        """选择一个审核单位并切换对应文件。"""

        self.workspace.select(item.slot_key)
        self.current_item_id = item.item_id
        self.refresh()

    def toggle_selected(self, item: ReviewListItem, checked: bool) -> None:
        """更新批量审核显式选择集合。"""

        key = (item.slot_key, item.item_id)
        if checked:
            self.selected_items.add(key)
        else:
            self.selected_items.discard(key)

    def all_items(self) -> list[ReviewListItem]:
        """把工作区所有审核产物投影为统一队列项。"""

        items: list[ReviewListItem] = []
        for slot in self.workspace.slots.values():
            artifact = slot.artifact
            if isinstance(artifact, Stage3DocumentReviewArtifact):
                items.extend(self._document_items(slot, artifact))
            elif isinstance(artifact, Stage3RelationReviewArtifact):
                items.extend(self._relation_items(slot, artifact))
            elif isinstance(artifact, Stage3ThoughtReviewArtifact):
                items.extend(self._thought_items(slot, artifact))
            elif isinstance(artifact, Stage3LoreDecisionsArtifact):
                items.extend(self._lore_decision_items(slot, artifact))
        return items

    @staticmethod
    def _document_items(
        slot: ArtifactSlot,
        artifact: Stage3DocumentReviewArtifact,
    ) -> list[ReviewListItem]:
        """投影 Story Event 与 Lore Entry 队列项。"""

        result = [
            _ordinary_list_item(
                slot.key,
                record.candidate_id,
                "story",
                record.effective_document().title,
                record,
                record.reviewed_document is not None,
                bool(record.identity_suggestions),
            )
            for record in artifact.story_events
        ]
        result.extend(
            _ordinary_list_item(
                slot.key,
                record.candidate_id,
                "lore",
                record.effective_document().title,
                record,
                record.reviewed_document is not None,
                bool(record.identity_suggestions),
            )
            for record in artifact.lore_entries
        )
        return result

    @staticmethod
    def _relation_items(
        slot: ArtifactSlot,
        artifact: Stage3RelationReviewArtifact,
    ) -> list[ReviewListItem]:
        """投影 Relation Type 与未归属 Observation 队列项。"""

        result = [
            _ordinary_list_item(
                slot.key,
                record.relation_type_id,
                "relation",
                (
                    f"{record.subject_character_id.value} → {record.object_character_id.value} · "
                    f"{record.effective_content().semantic_label}"
                ),
                record,
                record.reviewed_content is not None,
                bool(record.identity_suggestions),
            )
            for record in artifact.relation_types
        ]
        result.extend(
            _ordinary_list_item(
                slot.key,
                record.observation_id,
                "relation_unmerged",
                f"未归属 Observation · {record.observation_id}",
                record,
                False,
                False,
            )
            for record in artifact.unmerged_observations
        )
        return result

    @staticmethod
    def _thought_items(
        slot: ArtifactSlot,
        artifact: Stage3ThoughtReviewArtifact,
    ) -> list[ReviewListItem]:
        """投影 Thought Thread 与未归属 Update 队列项。"""

        result = [
            _ordinary_list_item(
                slot.key,
                record.thought_thread_id,
                "thought",
                (
                    f"{record.character_id.value} · {record.effective_content().canonical_subject} / "
                    f"{record.effective_content().thought_aspect}"
                ),
                record,
                record.reviewed_content is not None,
                bool(record.identity_suggestions),
            )
            for record in artifact.threads
        ]
        result.extend(
            _ordinary_list_item(
                slot.key,
                record.update_id,
                "thought_unassigned",
                f"未归属 Update · {record.update_id}",
                record,
                False,
                False,
            )
            for record in artifact.unassigned_updates
        )
        return result

    @staticmethod
    def _lore_decision_items(
        slot: ArtifactSlot,
        artifact: Stage3LoreDecisionsArtifact,
    ) -> list[ReviewListItem]:
        """投影 Lore 重复组队列项。"""

        return [
            ReviewListItem(
                slot_key=slot.key,
                item_id=record.group_id,
                kind="lore_decision",
                title=f"Lore 重复组 · {len(record.candidate_ids)} 项",
                review_status="completed" if record.status != "pending" else "unreviewed",
                disposition=record.action,
                risk_level="low",
                identity_pending=False,
                human_edited=record.reviewed_document is not None,
            )
            for record in artifact.decisions
        ]

    def filtered_items(self) -> list[ReviewListItem]:
        """返回当前审核域与过滤条件下的队列项。"""

        allowed_kinds = {
            "story": {"story"},
            "lore": {"lore"},
            "relation": {"relation", "relation_unmerged"},
            "thought": {"thought", "thought_unassigned"},
            "lore_decisions": {"lore_decision"},
        }.get(self.current_section, set())
        result = [item for item in self.all_items() if item.kind in allowed_kinds]
        if self.status_filter != "all":
            result = [item for item in result if item.review_status == self.status_filter]
        if self.search_text:
            result = [
                item
                for item in result
                if self.search_text in f"{item.title} {item.item_id}".lower()
            ]
        return sorted(
            result,
            key=lambda item: (
                item.review_status == "completed",
                item.risk_level != "high",
                item.title,
                item.item_id,
            ),
        )

    def refresh(self) -> None:
        """刷新顶部、导航、队列、详情和证据区。"""

        self.refresh_header()
        self.refresh_navigation()
        self.refresh_queue()
        self.refresh_summary()
        self.refresh_detail()
        self.refresh_evidence()

    def refresh_header(self) -> None:
        """刷新工作区总体状态。"""

        freshness = self.workspace.all_freshness()
        missing = sum(item.missing for item in freshness)
        stale = sum(bool(item.stale_sources) for item in freshness)
        if self.status_badge is not None:
            self.status_badge.text = f"缺失 {missing} · 过期 {stale}"
        if self.dirty_badge is not None:
            self.dirty_badge.text = f"未保存 {len(self.workspace.dirty_keys())}"

    def refresh_navigation(self) -> None:
        """刷新审核域导航和数量。"""

        if self.navigation_column is None:
            return
        self.navigation_column.clear()
        counts: dict[str, int] = {
            "story": 0,
            "lore": 0,
            "relation": 0,
            "thought": 0,
            "lore_decisions": 0,
        }
        for item in self.all_items():
            if item.kind == "story":
                counts["story"] += 1
            elif item.kind == "lore":
                counts["lore"] += 1
            elif item.kind.startswith("relation"):
                counts["relation"] += 1
            elif item.kind.startswith("thought"):
                counts["thought"] += 1
            elif item.kind == "lore_decision":
                counts["lore_decisions"] += 1
        labels = (
            ("overview", "总览", len(self.workspace.slots)),
            ("story", "Story Events", counts["story"]),
            ("lore", "Lore Entries", counts["lore"]),
            ("relation", "Relations", counts["relation"]),
            ("thought", "Thoughts", counts["thought"]),
            ("lore_decisions", "Lore 去重", counts["lore_decisions"]),
        )
        with self.navigation_column:
            for section, label, count in labels:
                button = ui.button(
                    f"{label}  ·  {count}",
                    on_click=lambda target=section: self.set_section(target),
                ).props("flat align=left")
                button.classes("w-full")
                if section == self.current_section:
                    button.props("color=primary")

    def refresh_queue(self) -> None:
        """刷新当前审核队列。"""

        if self.queue_column is None:
            return
        self.queue_column.clear()
        with self.queue_column:
            if self.current_section == "overview":
                ui.label("从上方选择一个审核域开始处理。").classes("text-sm text-slate-500")
                return
            for item in self.filtered_items():
                card = ui.card().classes("queue-card w-full p-3 gap-2")
                if item.item_id == self.current_item_id:
                    card.classes(add="active")
                with card:
                    with ui.row().classes("w-full items-start gap-2"):
                        ui.checkbox(
                            value=(item.slot_key, item.item_id) in self.selected_items,
                            on_change=lambda event, target=item: self.toggle_selected(
                                target, _event_checked(event)
                            ),
                        ).props("dense")
                        with ui.column().classes("gap-0 flex-1"):
                            ui.label(item.title).classes("text-sm font-semibold")
                            ui.label(item.item_id).classes("text-xs text-slate-500 break-all")
                    with ui.row().classes("gap-1 flex-wrap"):
                        ui.badge(item.review_status)
                        if item.disposition in {"publish", "reject", "exclude"}:
                            ui.badge(disposition_badge_label(item.disposition)).props("outline")
                        if item.risk_level == "high":
                            ui.badge("高风险").props("color=negative")
                        elif item.risk_level == "medium":
                            ui.badge("中风险").props("color=warning")
                        if item.identity_pending:
                            ui.badge("身份待确认").props("color=warning")
                        if item.human_edited:
                            ui.badge("人工修改").props("color=secondary")
                card.on("click", lambda _event=None, target=item: self.select_item(target))

    def refresh_summary(self) -> None:
        """刷新当前文件和全包完成度摘要。"""

        if self.summary_column is None:
            return
        self.summary_column.clear()
        all_items = self.all_items()
        completed = sum(item.review_status == "completed" for item in all_items)
        with self.summary_column:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(
                    f"{self.workspace.resolved.spec.display_name} · 审核进度 {completed}/{len(all_items)}"
                ).classes("text-xl font-semibold")
                ui.badge(self.current_section)
            with ui.row().classes("gap-2 flex-wrap"):
                for result in self.workspace.all_freshness():
                    if result.missing:
                        ui.badge(f"{result.key}: 待生成").props("color=warning")
                    elif result.stale_sources:
                        ui.badge(f"{result.key}: 来源过期").props("color=negative")
                    else:
                        ui.badge(f"{result.key}: 最新").props("color=positive outline")
            if self.last_audit_messages:
                with ui.expansion("最近一次构建审计", icon="fact_check").classes("w-full"):
                    for message in self.last_audit_messages:
                        ui.label(message).classes("text-sm mono-wrap")

    def refresh_detail(self) -> None:
        """按当前选择构建类型化编辑表单与审核操作。"""

        if self.detail_column is None:
            return
        self.detail_column.clear()
        with self.detail_column:
            if self.current_section == "overview":
                self._build_overview()
                return
            located = self._locate_current()
            if located is None:
                ui.label("请从左侧选择一个审核项。").classes("text-slate-500")
                return
            slot, record = located
            ui.label(f"{slot.label} · {self.current_item_id}").classes("text-lg font-semibold")
            if isinstance(record, ReviewFields):
                self._build_risk_summary(record)
            if isinstance(record, StoryEventReviewRecord):
                self._build_story_form(slot.key, record)
            elif isinstance(record, LoreEntryReviewRecord):
                self._build_lore_form(slot.key, record)
            elif isinstance(record, RelationTypeReviewRecord):
                self._build_relation_form(slot.key, record)
            elif isinstance(record, ThoughtThreadReviewRecord):
                self._build_thought_form(slot.key, record)
            elif isinstance(record, LoreDedupDecisionRecord):
                self._build_lore_decision_form(slot.key, record)
            elif isinstance(record, (UnmergedRelationObservationDecision, UnassignedThoughtUpdateDecision)):
                ui.label(record.generated_reason).classes("text-sm text-slate-700")
            if isinstance(record, ReviewFields):
                self._build_review_actions(slot.key, record)

    def _build_overview(self) -> None:
        """构建缺失文件、过期来源与使用提示总览。"""

        ui.label("工作区文件").classes("text-lg font-semibold")
        for slot in self.workspace.slots.values():
            result = self.workspace.freshness(slot.key)
            with ui.card().classes("w-full p-3 gap-1"):
                ui.label(slot.label).classes("font-semibold")
                ui.label(str(slot.path)).classes("text-xs text-slate-500 break-all")
                if result.missing:
                    ui.label("尚未生成；可继续打开工作台处理其他文件。").classes("text-sm text-amber-700")
                elif result.stale_sources:
                    ui.label("直接来源已变化：" + "、".join(result.stale_sources)).classes(
                        "text-sm text-red-700"
                    )
                else:
                    ui.label("来源摘要一致。").classes("text-sm text-emerald-700")

    def _build_story_form(self, slot_key: str, record: StoryEventReviewRecord) -> None:
        """构建 Story Event 完整文档表单。"""

        content = record.effective_document()
        fields: dict[str, object] = {}
        with ui.grid(columns=3).classes("w-full gap-3"):
            fields["timeline_id"] = ui.input("timeline_id", value=content.timeline_id)
            fields["series_id"] = ui.input("series_id", value=content.series_id.value)
            fields["episode"] = ui.number("episode", value=content.episode, precision=0)
            fields["occurred_story_year"] = ui.number(
                "occurred_story_year", value=content.occurred_story_year, precision=0
            )
            fields["time_order"] = ui.number("time_order", value=content.time_order, precision=0)
            fields["importance"] = ui.number("importance", value=content.importance, precision=0)
            fields["visible_from"] = ui.number("visible_from", value=content.visible_from, precision=0)
            fields["visible_to"] = ui.number("visible_to", value=content.visible_to, precision=0)
            fields["canon_branch"] = ui.input("canon_branch", value=content.canon_branch.value)
        fields["title"] = ui.input("标题", value=content.title).classes("w-full")
        fields["summary"] = ui.textarea("摘要", value=content.summary).props("autogrow").classes("w-full")
        fields["participants"] = ui.select(
            {
                character.value: f"{character.common_name}（{character.value}）"
                for character in CharacterId
            },
            label="参与角色",
            value=[item.value for item in content.participants],
            with_input=True,
            multiple=True,
            clearable=True,
        ).props("use-chips options-dense input-debounce=0").classes("w-full")
        fields["tags"] = ui.input("标签（逗号分隔）", value=", ".join(content.tags)).classes("w-full")
        fields["retrieval_text"] = ui.textarea(
            "检索文本", value=content.retrieval_text
        ).props("autogrow").classes("w-full")
        ui.button(
            "应用内容修改",
            on_click=lambda: self._apply_story_form(slot_key, record, fields),
        ).props("unelevated color=primary")

    def _apply_story_form(
        self,
        slot_key: str,
        record: StoryEventReviewRecord,
        fields: dict[str, object],
    ) -> None:
        """校验并应用 Story Event 表单。"""

        try:
            payload = StoryEventPayload(
                timeline_id=_widget_text(fields["timeline_id"]),
                occurred_story_year=_widget_optional_int(fields["occurred_story_year"]),
                series_id=_widget_text(fields["series_id"]),
                episode=_widget_int(fields["episode"]),
                time_order=_widget_int(fields["time_order"]),
                visible_from=_widget_int(fields["visible_from"]),
                visible_to=_widget_int(fields["visible_to"]),
                canon_branch=_widget_text(fields["canon_branch"]),
                title=_widget_text(fields["title"]),
                summary=_widget_text(fields["summary"]),
                participants=_widget_string_list(fields["participants"]),
                importance=_widget_int(fields["importance"]),
                tags=_widget_csv(fields["tags"]),
                retrieval_text=_widget_text(fields["retrieval_text"]),
            )
            self.workspace.apply(
                slot_key,
                ReplaceStoryDocumentCommand(record.candidate_id, payload),
            )
            self._notify_success("已更新 Story Event 草稿并撤销旧审批")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _build_lore_form(self, slot_key: str, record: LoreEntryReviewRecord) -> None:
        """构建 Lore Entry 完整文档表单。"""

        content = record.effective_document()
        fields: dict[str, object] = {}
        with ui.grid(columns=3).classes("w-full gap-3"):
            fields["scope_type"] = ui.input("scope_type", value=content.scope_type.value)
            fields["timeline_id"] = ui.input("timeline_id", value=content.timeline_id)
            fields["canon_branch"] = ui.input("canon_branch", value=content.canon_branch.value)
            fields["visible_from"] = ui.number("visible_from", value=content.visible_from, precision=0)
            fields["visible_to"] = ui.number("visible_to", value=content.visible_to, precision=0)
        fields["series_ids"] = ui.input(
            "series_ids（逗号分隔；空表示 null）",
            value="" if content.series_ids is None else ", ".join(item.value for item in content.series_ids),
        ).classes("w-full")
        fields["applicable_story_years"] = ui.input(
            "适用学年（逗号分隔；空表示 null）",
            value=(
                ""
                if content.applicable_story_years is None
                else ", ".join(str(item) for item in content.applicable_story_years)
            ),
        ).classes("w-full")
        fields["title"] = ui.input("标题", value=content.title).classes("w-full")
        fields["content"] = ui.textarea("内容", value=content.content).props("autogrow").classes("w-full")
        fields["retrieval_text"] = ui.textarea(
            "检索文本", value=content.retrieval_text
        ).props("autogrow").classes("w-full")
        fields["tags"] = ui.input("标签（逗号分隔）", value=", ".join(content.tags)).classes("w-full")
        ui.button(
            "应用内容修改",
            on_click=lambda: self._apply_lore_form(slot_key, record, fields),
        ).props("unelevated color=primary")

    def _apply_lore_form(
        self,
        slot_key: str,
        record: LoreEntryReviewRecord,
        fields: dict[str, object],
    ) -> None:
        """校验并应用 Lore Entry 表单。"""

        try:
            series_ids = _widget_csv(fields["series_ids"])
            years = [int(item) for item in _widget_csv(fields["applicable_story_years"])]
            payload = LoreEntryPayload(
                scope_type=_widget_text(fields["scope_type"]),
                series_ids=series_ids or None,
                timeline_id=_widget_text(fields["timeline_id"]),
                applicable_story_years=years or None,
                visible_from=_widget_optional_int(fields["visible_from"]),
                visible_to=_widget_optional_int(fields["visible_to"]),
                canon_branch=_widget_text(fields["canon_branch"]),
                title=_widget_text(fields["title"]),
                content=_widget_text(fields["content"]),
                retrieval_text=_widget_text(fields["retrieval_text"]),
                tags=_widget_csv(fields["tags"]),
            )
            self.workspace.apply(slot_key, ReplaceLoreDocumentCommand(record.candidate_id, payload))
            self._notify_success("已更新 Lore Entry 草稿并撤销旧审批")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _build_relation_form(self, slot_key: str, record: RelationTypeReviewRecord) -> None:
        """构建 Relation Type 完整内容和 State 卡片表单。"""

        content = record.effective_content()
        ui.label(
            f"{record.subject_character_id.value} → {record.object_character_id.value}"
        ).classes("text-sm text-slate-600")
        semantic = ui.input("semantic_label", value=content.semantic_label).classes("w-full")
        state_fields: list[dict[str, object]] = []
        for index, state in enumerate(content.states):
            state_fields.append(self._build_relation_state_fields(record, index, state))
        with ui.row().classes("gap-2"):
            ui.button(
                "应用完整 Relation 内容",
                on_click=lambda: self._apply_relation_form(slot_key, record, semantic, state_fields),
            ).props("unelevated color=primary")
            ui.button("合并其他 Type", on_click=lambda: self._open_relation_merge(slot_key, record)).props(
                "outline"
            )
            ui.button(
                "新增 State",
                on_click=lambda: self._add_relation_state(record),
            ).props("outline")

    def _build_relation_state_fields(
        self,
        record: RelationTypeReviewRecord,
        index: int,
        state: RelationStateDraft,
    ) -> dict[str, object]:
        """构建一个 Relation State 的字段集合。"""

        fields: dict[str, object] = {}
        with ui.expansion(f"State {index + 1} · {state.visible_from}–{state.visible_to}").classes("w-full"):
            ui.label(state.relation_state_id).classes("text-xs text-slate-500 break-all")
            fields["relation_state_id"] = state.relation_state_id
            fields["supporting_observation_ids"] = ui.input(
                "supporting_observation_ids",
                value=", ".join(state.supporting_observation_ids),
            ).classes("w-full")
            fields["state_summary"] = ui.textarea(
                "state_summary", value=state.state_summary
            ).props("autogrow").classes("w-full")
            fields["speech_hint"] = ui.textarea("speech_hint", value=state.speech_hint).props(
                "autogrow"
            ).classes("w-full")
            fields["object_character_nickname"] = ui.input(
                "object_character_nickname", value=state.object_character_nickname
            ).classes("w-full")
            with ui.grid(columns=2).classes("w-full gap-3"):
                fields["visible_from"] = ui.number("visible_from", value=state.visible_from, precision=0)
                fields["visible_to"] = ui.number("visible_to", value=state.visible_to, precision=0)
            fields["tags"] = ui.input("tags", value=", ".join(state.tags)).classes("w-full")
            fields["retrieval_text"] = ui.textarea(
                "retrieval_text", value=state.retrieval_text
            ).props("autogrow").classes("w-full")
            with ui.row().classes("gap-2"):
                if index > 0:
                    ui.button(
                        "上移",
                        on_click=lambda: self._move_relation_state(record, index, -1),
                    ).props("flat dense")
                if index + 1 < len(record.effective_content().states):
                    ui.button(
                        "下移",
                        on_click=lambda: self._move_relation_state(record, index, 1),
                    ).props("flat dense")
                    ui.button(
                        "与下一 State 合并",
                        on_click=lambda: self._merge_relation_states(record, index),
                    ).props("flat dense")
                if index > 0:
                    ui.button(
                        "从此 State 拆分新 Type",
                        on_click=lambda: self._split_relation(record, index),
                    ).props("flat")
                ui.button(
                    "删除此 State",
                    on_click=lambda: self._delete_relation_state(record, index),
                ).props("flat color=negative")
        return fields

    def _apply_relation_form(
        self,
        slot_key: str,
        record: RelationTypeReviewRecord,
        semantic_widget: object,
        state_fields: list[dict[str, object]],
    ) -> None:
        """校验并应用 Relation Type 完整内容表单。"""

        try:
            states = [
                RelationStateDraft(
                    relation_state_id=str(fields["relation_state_id"]),
                    supporting_observation_ids=_widget_csv(fields["supporting_observation_ids"]),
                    state_summary=_widget_text(fields["state_summary"]),
                    speech_hint=_widget_text(fields["speech_hint"]),
                    object_character_nickname=_widget_text(fields["object_character_nickname"]),
                    visible_from=_widget_int(fields["visible_from"]),
                    visible_to=_widget_int(fields["visible_to"]),
                    tags=_widget_csv(fields["tags"]),
                    retrieval_text=_widget_text(fields["retrieval_text"]),
                )
                for fields in state_fields
            ]
            content = RelationTypeContentDraft(
                semantic_label=_widget_text(semantic_widget),
                states=states,
            )
            self.workspace.apply(
                slot_key,
                ReplaceRelationContentCommand(record.relation_type_id, content),
            )
            self._notify_success("已更新 Relation 完整内容并撤销旧审批")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _add_relation_state(self, record: RelationTypeReviewRecord) -> None:
        """向 Relation Type 末尾添加一个待人工填写的新 State。"""

        content = record.effective_content().model_copy(deep=True)
        visible_from = 0 if not content.states else content.states[-1].visible_to + 1
        content.states.append(
            RelationStateDraft(
                relation_state_id=f"relation_state:{uuid4()}",
                supporting_observation_ids=[],
                state_summary="待填写",
                speech_hint="",
                object_character_nickname="",
                visible_from=visible_from,
                visible_to=visible_from,
                tags=[],
                retrieval_text="待填写",
            )
        )
        try:
            self.workspace.apply(
                "relation",
                ReplaceRelationContentCommand(record.relation_type_id, content),
            )
            self._notify_success("已新增 Relation State，请继续填写内容和时间窗口")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _move_relation_state(
        self,
        record: RelationTypeReviewRecord,
        index: int,
        offset: int,
    ) -> None:
        """在 Relation Type 内调整一个 State 的显示顺序。"""

        content = record.effective_content().model_copy(deep=True)
        target = index + offset
        if not 0 <= index < len(content.states) or not 0 <= target < len(content.states):
            return
        content.states[index], content.states[target] = content.states[target], content.states[index]
        try:
            self.workspace.apply(
                "relation",
                ReplaceRelationContentCommand(record.relation_type_id, content),
            )
            self._notify_success("已调整 Relation State 顺序")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _merge_relation_states(self, record: RelationTypeReviewRecord, index: int) -> None:
        """把相邻两个 Relation State 合并并保留前一个 State 身份。"""

        content = record.effective_content().model_copy(deep=True)
        if not 0 <= index < len(content.states) - 1:
            return
        primary = content.states[index]
        merged = content.states[index + 1]
        primary.supporting_observation_ids = list(
            dict.fromkeys([*primary.supporting_observation_ids, *merged.supporting_observation_ids])
        )
        primary.visible_from = min(primary.visible_from, merged.visible_from)
        primary.visible_to = max(primary.visible_to, merged.visible_to)
        primary.tags = list(dict.fromkeys([*primary.tags, *merged.tags]))
        content.states.pop(index + 1)
        try:
            self.workspace.apply(
                "relation",
                ReplaceRelationContentCommand(record.relation_type_id, content),
            )
            self._notify_success("已合并相邻 Relation State；前一个 State 继承旧身份")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _delete_relation_state(self, record: RelationTypeReviewRecord, index: int) -> None:
        """从 Relation Type 草稿删除一个 State。"""

        content = record.effective_content().model_copy(deep=True)
        if not 0 <= index < len(content.states):
            return
        content.states.pop(index)
        try:
            self.workspace.apply(
                "relation",
                ReplaceRelationContentCommand(record.relation_type_id, content),
            )
            self._notify_success("已删除 Relation State")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _split_relation(self, record: RelationTypeReviewRecord, index: int) -> None:
        """从指定 State 开始拆出一个新 Relation Type。"""

        try:
            self.workspace.apply(
                "relation",
                SplitRelationTypeCommand(
                    item_id=record.relation_type_id,
                    split_index=index,
                    new_type_id=f"relation_type:{uuid4()}",
                    new_semantic_label=record.effective_content().semantic_label,
                ),
            )
            self._notify_success("已拆分 Relation Type；原 Type 继承旧身份")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _open_relation_merge(self, slot_key: str, record: RelationTypeReviewRecord) -> None:
        """打开同向 Relation Type 合并对话框。"""

        artifact = self.workspace.slots[slot_key].artifact
        if not isinstance(artifact, Stage3RelationReviewArtifact):
            return
        options = {
            item.relation_type_id: item.effective_content().semantic_label
            for item in artifact.relation_types
            if item.relation_type_id != record.relation_type_id
            and item.subject_character_id == record.subject_character_id
            and item.object_character_id == record.object_character_id
        }
        if not options:
            ui.notify("没有可合并的同向 Relation Type", color="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full gap-4"):
            ui.label("合并 Relation Type").classes("text-xl font-semibold")
            target = ui.select(options, label="被合并 Type").classes("w-full")
            semantic = ui.input(
                "合并后的 semantic_label", value=record.effective_content().semantic_label
            ).classes("w-full")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认合并",
                    on_click=lambda: self._merge_relation_dialog(
                        dialog, slot_key, record, target, semantic
                    ),
                ).props("unelevated color=primary")
        dialog.open()

    def _merge_relation_dialog(
        self,
        dialog: object,
        slot_key: str,
        record: RelationTypeReviewRecord,
        target_widget: object,
        semantic_widget: object,
    ) -> None:
        """提交 Relation Type 合并对话框。"""

        try:
            merged_id = _widget_text(target_widget)
            self.workspace.apply(
                slot_key,
                MergeRelationTypesCommand(
                    primary_type_id=record.relation_type_id,
                    merged_type_id=merged_id,
                    semantic_label=_widget_text(semantic_widget),
                ),
            )
            getattr(dialog, "close")()
            self._notify_success("已合并 Relation Type；被合并候选标记为重复拒绝")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _build_thought_form(self, slot_key: str, record: ThoughtThreadReviewRecord) -> None:
        """构建 Thought Thread 完整内容和 State 卡片表单。"""

        content = record.effective_content()
        ui.label(record.character_id.value).classes("text-sm text-slate-600")
        with ui.grid(columns=2).classes("w-full gap-3"):
            subject = ui.input("canonical_subject", value=content.canonical_subject)
            aspect = ui.input("thought_aspect", value=content.thought_aspect)
        state_fields: list[dict[str, object]] = []
        for index, state in enumerate(content.states):
            state_fields.append(self._build_thought_state_fields(record, index, state))
        with ui.row().classes("gap-2"):
            ui.button(
                "应用完整 Thought 内容",
                on_click=lambda: self._apply_thought_form(
                    slot_key, record, subject, aspect, state_fields
                ),
            ).props("unelevated color=primary")
            ui.button("合并其他 Thread", on_click=lambda: self._open_thought_merge(slot_key, record)).props(
                "outline"
            )
            ui.button(
                "新增 State",
                on_click=lambda: self._add_thought_state(record),
            ).props("outline")

    def _build_thought_state_fields(
        self,
        record: ThoughtThreadReviewRecord,
        index: int,
        state: ThoughtStateDraft,
    ) -> dict[str, object]:
        """构建一个 Thought State 的字段集合。"""

        fields: dict[str, object] = {}
        with ui.expansion(f"State {index + 1} · {state.visible_from}–{state.visible_to}").classes("w-full"):
            ui.label(state.thought_state_id).classes("text-xs text-slate-500 break-all")
            fields["thought_state_id"] = state.thought_state_id
            fields["transition"] = state.transition
            fields["supporting_update_ids"] = ui.input(
                "supporting_update_ids", value=", ".join(state.supporting_update_ids)
            ).classes("w-full")
            fields["thought_text"] = ui.textarea(
                "thought_text", value=state.thought_text
            ).props("autogrow").classes("w-full")
            fields["epistemic_status"] = ui.select(
                ["knows", "believes", "suspects", "uncertain", "rejects"],
                value=state.epistemic_status,
                label="epistemic_status",
            ).classes("w-full")
            with ui.grid(columns=2).classes("w-full gap-3"):
                fields["visible_from"] = ui.number("visible_from", value=state.visible_from, precision=0)
                fields["visible_to"] = ui.number("visible_to", value=state.visible_to, precision=0)
            fields["story_event_candidate_ids"] = ui.input(
                "story_event_candidate_ids", value=", ".join(state.story_event_candidate_ids)
            ).classes("w-full")
            fields["event_fact_ids"] = ui.input(
                "event_fact_ids", value=", ".join(state.event_fact_ids)
            ).classes("w-full")
            fields["tags"] = ui.input("tags", value=", ".join(state.tags)).classes("w-full")
            fields["retrieval_text"] = ui.textarea(
                "retrieval_text", value=state.retrieval_text
            ).props("autogrow").classes("w-full")
            with ui.row().classes("gap-2"):
                if index > 0:
                    ui.button(
                        "上移",
                        on_click=lambda: self._move_thought_state(record, index, -1),
                    ).props("flat dense")
                if index + 1 < len(record.effective_content().states):
                    ui.button(
                        "下移",
                        on_click=lambda: self._move_thought_state(record, index, 1),
                    ).props("flat dense")
                    ui.button(
                        "与下一 State 合并",
                        on_click=lambda: self._merge_thought_states(record, index),
                    ).props("flat dense")
                if index > 0:
                    ui.button(
                        "从此 State 拆分新 Thread",
                        on_click=lambda: self._split_thought(record, index),
                    ).props("flat")
                ui.button(
                    "删除此 State",
                    on_click=lambda: self._delete_thought_state(record, index),
                ).props("flat color=negative")
        return fields

    def _apply_thought_form(
        self,
        slot_key: str,
        record: ThoughtThreadReviewRecord,
        subject_widget: object,
        aspect_widget: object,
        state_fields: list[dict[str, object]],
    ) -> None:
        """校验并应用 Thought Thread 完整内容表单。"""

        try:
            states = [
                ThoughtStateDraft(
                    thought_state_id=str(fields["thought_state_id"]),
                    transition=str(fields["transition"]),
                    supporting_update_ids=_widget_csv(fields["supporting_update_ids"]),
                    thought_text=_widget_text(fields["thought_text"]),
                    epistemic_status=_widget_text(fields["epistemic_status"]),
                    visible_from=_widget_int(fields["visible_from"]),
                    visible_to=_widget_int(fields["visible_to"]),
                    story_event_candidate_ids=_widget_csv(fields["story_event_candidate_ids"]),
                    event_fact_ids=_widget_csv(fields["event_fact_ids"]),
                    tags=_widget_csv(fields["tags"]),
                    retrieval_text=_widget_text(fields["retrieval_text"]),
                )
                for fields in state_fields
            ]
            content = ThoughtThreadContentDraft(
                canonical_subject=_widget_text(subject_widget),
                thought_aspect=_widget_text(aspect_widget),
                states=states,
            )
            self.workspace.apply(
                slot_key,
                ReplaceThoughtContentCommand(record.thought_thread_id, content),
            )
            self._notify_success("已更新 Thought 完整内容并撤销旧审批")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _add_thought_state(self, record: ThoughtThreadReviewRecord) -> None:
        """向 Thought Thread 末尾添加一个待人工填写的新 State。"""

        content = record.effective_content().model_copy(deep=True)
        visible_from = 0 if not content.states else content.states[-1].visible_to + 1
        content.states.append(
            ThoughtStateDraft(
                thought_state_id=f"thought_state:{uuid4()}",
                transition="revised" if content.states else "acquired",
                supporting_update_ids=[],
                thought_text="待填写",
                epistemic_status="uncertain",
                visible_from=visible_from,
                visible_to=visible_from,
                story_event_candidate_ids=[],
                event_fact_ids=[],
                tags=[],
                retrieval_text="待填写",
            )
        )
        try:
            self.workspace.apply(
                "thought",
                ReplaceThoughtContentCommand(record.thought_thread_id, content),
            )
            self._notify_success("已新增 Thought State，请继续填写内容和时间窗口")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _move_thought_state(
        self,
        record: ThoughtThreadReviewRecord,
        index: int,
        offset: int,
    ) -> None:
        """在 Thought Thread 内调整一个 State 的显示顺序。"""

        content = record.effective_content().model_copy(deep=True)
        target = index + offset
        if not 0 <= index < len(content.states) or not 0 <= target < len(content.states):
            return
        content.states[index], content.states[target] = content.states[target], content.states[index]
        try:
            self.workspace.apply(
                "thought",
                ReplaceThoughtContentCommand(record.thought_thread_id, content),
            )
            self._notify_success("已调整 Thought State 顺序")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _merge_thought_states(self, record: ThoughtThreadReviewRecord, index: int) -> None:
        """把相邻两个 Thought State 合并并保留前一个 State 身份。"""

        content = record.effective_content().model_copy(deep=True)
        if not 0 <= index < len(content.states) - 1:
            return
        primary = content.states[index]
        merged = content.states[index + 1]
        primary.supporting_update_ids = list(
            dict.fromkeys([*primary.supporting_update_ids, *merged.supporting_update_ids])
        )
        primary.visible_from = min(primary.visible_from, merged.visible_from)
        primary.visible_to = max(primary.visible_to, merged.visible_to)
        primary.story_event_candidate_ids = list(
            dict.fromkeys(
                [*primary.story_event_candidate_ids, *merged.story_event_candidate_ids]
            )
        )
        primary.event_fact_ids = list(
            dict.fromkeys([*primary.event_fact_ids, *merged.event_fact_ids])
        )
        primary.tags = list(dict.fromkeys([*primary.tags, *merged.tags]))
        content.states.pop(index + 1)
        try:
            self.workspace.apply(
                "thought",
                ReplaceThoughtContentCommand(record.thought_thread_id, content),
            )
            self._notify_success("已合并相邻 Thought State；前一个 State 继承旧身份")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _delete_thought_state(self, record: ThoughtThreadReviewRecord, index: int) -> None:
        """从 Thought Thread 草稿删除一个 State。"""

        content = record.effective_content().model_copy(deep=True)
        if not 0 <= index < len(content.states):
            return
        content.states.pop(index)
        try:
            self.workspace.apply(
                "thought",
                ReplaceThoughtContentCommand(record.thought_thread_id, content),
            )
            self._notify_success("已删除 Thought State")
        except (TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _split_thought(self, record: ThoughtThreadReviewRecord, index: int) -> None:
        """从指定 State 开始拆出一个新 Thought Thread。"""

        content = record.effective_content()
        try:
            self.workspace.apply(
                "thought",
                SplitThoughtThreadCommand(
                    item_id=record.thought_thread_id,
                    split_index=index,
                    new_thread_id=f"thought_thread:{uuid4()}",
                    new_canonical_subject=content.canonical_subject,
                    new_thought_aspect=content.thought_aspect,
                ),
            )
            self._notify_success("已拆分 Thought Thread；原 Thread 继承旧身份")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _open_thought_merge(self, slot_key: str, record: ThoughtThreadReviewRecord) -> None:
        """打开同角色 Thought Thread 合并对话框。"""

        artifact = self.workspace.slots[slot_key].artifact
        if not isinstance(artifact, Stage3ThoughtReviewArtifact):
            return
        options = {
            item.thought_thread_id: (
                f"{item.effective_content().canonical_subject} / {item.effective_content().thought_aspect}"
            )
            for item in artifact.threads
            if item.thought_thread_id != record.thought_thread_id
            and item.character_id == record.character_id
        }
        if not options:
            ui.notify("没有可合并的同角色 Thought Thread", color="warning")
            return
        content = record.effective_content()
        with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full gap-4"):
            ui.label("合并 Thought Thread").classes("text-xl font-semibold")
            target = ui.select(options, label="被合并 Thread").classes("w-full")
            subject = ui.input("合并后的 canonical_subject", value=content.canonical_subject)
            aspect = ui.input("合并后的 thought_aspect", value=content.thought_aspect)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认合并",
                    on_click=lambda: self._merge_thought_dialog(
                        dialog, slot_key, record, target, subject, aspect
                    ),
                ).props("unelevated color=primary")
        dialog.open()

    def _merge_thought_dialog(
        self,
        dialog: object,
        slot_key: str,
        record: ThoughtThreadReviewRecord,
        target_widget: object,
        subject_widget: object,
        aspect_widget: object,
    ) -> None:
        """提交 Thought Thread 合并对话框。"""

        try:
            self.workspace.apply(
                slot_key,
                MergeThoughtThreadsCommand(
                    primary_thread_id=record.thought_thread_id,
                    merged_thread_id=_widget_text(target_widget),
                    canonical_subject=_widget_text(subject_widget),
                    thought_aspect=_widget_text(aspect_widget),
                ),
            )
            getattr(dialog, "close")()
            self._notify_success("已合并 Thought Thread；被合并候选标记为重复拒绝")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _build_lore_decision_form(self, slot_key: str, record: LoreDedupDecisionRecord) -> None:
        """构建 Lore 重复组决定与合并文档表单。"""

        status_label = LORE_DECISION_STATUS_LABELS.get(record.status, record.status)
        ui.label(f"候选数量：{len(record.candidate_ids)} · 状态：{status_label}")
        if record.status == "automatic":
            ui.label(
                _lore_dedup_action_description(record.action, len(record.candidate_ids))
            ).classes("text-sm text-emerald-700")
            self._build_lore_candidate_previews(
                record,
                record.action,
                record.primary_candidate_id,
            )
            return
        action = ui.select(
            LORE_DEDUP_ACTION_OPTIONS,
            value=record.action or "keep_separate",
            label="这组候选如何处理",
        ).classes("w-full")
        action_description = ui.label().classes("text-sm text-slate-600")
        primary = ui.select(
            record.candidate_ids,
            value=record.primary_candidate_id or record.candidate_ids[0],
            label="最终保留的候选",
        ).classes("w-full")
        drop_warning = ui.label(
            f"警告：确认后，本组全部 {len(record.candidate_ids)} 条候选都不会发布。"
        ).classes("text-sm font-semibold text-red-700")
        candidate_badges = self._build_lore_candidate_previews(
            record,
            action.value,
            primary.value,
        )

        def refresh_preview(_: object | None = None) -> None:
            """根据当前去重动作刷新说明、警告和候选状态标签。"""

            action_value = _widget_text(action)
            primary_id = _widget_text(primary)
            action_description.text = _lore_dedup_action_description(
                action_value,
                len(record.candidate_ids),
            )
            primary.set_visibility(action_value == "merge")
            drop_warning.set_visibility(action_value == "drop")
            for candidate_id, badge in candidate_badges.items():
                badge_text, badge_color = _lore_candidate_badge(
                    action_value,
                    primary_id,
                    candidate_id,
                )
                badge.text = badge_text
                badge.props(f"color={badge_color}")

        action.on_value_change(refresh_preview)
        primary.on_value_change(refresh_preview)
        refresh_preview()
        notes = (
            ui.textarea("审核备注", value=record.review_notes or "")
            .props("autogrow")
            .classes("w-full")
        )
        with ui.row().classes("gap-2"):
            ui.button(
                "确认去重决定",
                on_click=lambda: self._apply_lore_decision(
                    slot_key, record, action, primary, notes
                ),
            ).props("unelevated color=primary")
            if record.status == "completed":
                ui.button(
                    "恢复为待处理",
                    on_click=lambda: self._clear_lore_decision(slot_key, record),
                ).props("outline")

    def _build_lore_candidate_previews(
        self,
        record: LoreDedupDecisionRecord,
        action: object,
        primary_candidate_id: object,
    ) -> dict[str, ui.badge]:
        """构建 Lore 候选内容预览并返回其状态标签。"""

        badges: dict[str, ui.badge] = {}
        for candidate_id in record.candidate_ids:
            candidate = self._find_lore_candidate(candidate_id)
            expansion_title = (
                candidate_id
                if candidate is None
                else f"{candidate.effective_document().title} · {candidate_id}"
            )
            with ui.expansion(expansion_title).classes("w-full"):
                badge_text, badge_color = _lore_candidate_badge(
                    action,
                    primary_candidate_id,
                    candidate_id,
                )
                badges[candidate_id] = ui.badge(badge_text).props(f"color={badge_color}")
                if candidate is None:
                    ui.label("构建配置中找不到候选文档").classes("text-red-700")
                else:
                    ui.label(candidate.effective_document().content).classes(
                        "text-sm whitespace-pre-wrap"
                    )
        return badges

    def _apply_lore_decision(
        self,
        slot_key: str,
        record: LoreDedupDecisionRecord,
        action_widget: object,
        primary_widget: object,
        notes_widget: object,
    ) -> None:
        """应用 Lore 重复组决定，merge 默认使用主候选有效文档。"""

        try:
            action = _widget_text(action_widget)
            primary_id = _widget_text(primary_widget) if action == "merge" else None
            primary = None if primary_id is None else self._find_lore_candidate(primary_id)
            reviewed = None
            if action == "merge" and primary is not None:
                reviewed = primary.effective_document().model_copy(deep=True)
            self.workspace.apply(
                slot_key,
                CompleteLoreDecisionCommand(
                    group_id=record.group_id,
                    action=action,
                    primary_candidate_id=primary_id,
                    reviewed_document=reviewed,
                    review_notes=_widget_text(notes_widget) or None,
                ),
            )
            self._notify_success("已完成 Lore 去重决定")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _clear_lore_decision(self, slot_key: str, record: LoreDedupDecisionRecord) -> None:
        """清除一个人工 Lore 决定。"""

        try:
            self.workspace.apply(slot_key, ClearLoreDecisionCommand(record.group_id))
            self._notify_success("已恢复为 pending")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _build_review_actions(self, slot_key: str, record: ReviewFields) -> None:
        """构建普通审核单位的备注、身份和最终处置操作。"""

        ui.separator()
        ui.label("人工审核").classes("text-lg font-semibold")
        if record.disposition in {"publish", "reject", "exclude"}:
            with ui.row().classes("items-center gap-2"):
                color = (
                    "positive"
                    if record.disposition == "publish"
                    else "negative" if record.disposition == "reject" else "warning"
                )
                ui.badge(disposition_badge_label(record.disposition)).props(f"color={color}")
                ui.label(disposition_description(record.disposition)).classes("text-sm text-slate-600")
        notes = ui.textarea("review_notes", value=record.review_notes or "").props("autogrow").classes("w-full")
        with ui.row().classes("gap-2 flex-wrap"):
            ui.button(
                "保存备注到草稿",
                on_click=lambda: self._update_notes(slot_key, record, notes),
            ).props("outline")
            ui.button(
                "审核通过（纳入世界书）",
                on_click=lambda: self._complete_item(slot_key, record, "publish"),
            ).props("unelevated color=positive")
            ui.button(
                "不纳入世界书",
                on_click=lambda: self._open_nonpublication_dialog(slot_key, record),
            ).props("outline color=negative")
            ui.button(
                "标记待跟进",
                on_click=lambda: self._mark_followup(slot_key, record, notes),
            ).props("flat")
        if isinstance(
            record,
            (StoryEventReviewRecord, LoreEntryReviewRecord, RelationTypeReviewRecord, ThoughtThreadReviewRecord),
        ):
            ui.button(
                "恢复机器版本",
                on_click=lambda: self._restore_generated(slot_key, record),
            ).props("flat color=secondary")
            suggestions = getattr(record, "identity_suggestions", [])
            if suggestions:
                ui.label("身份继承待确认").classes("font-semibold text-amber-700")
                for suggestion in suggestions:
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(
                            f"{suggestion.previous_id} · {suggestion.confidence:.2f} · {suggestion.reason}"
                        ).classes("text-sm")
                        ui.button(
                            "继承此身份",
                            on_click=lambda previous=suggestion.previous_id: self._resolve_identity(
                                slot_key, record, "inherit", previous
                            ),
                        ).props("flat dense")
                ui.button(
                    "保留为新身份",
                    on_click=lambda: self._resolve_identity(slot_key, record, "new", None),
                ).props("outline")

    def _build_risk_summary(self, record: ReviewFields) -> None:
        """在审核详情顶部解释程序给出的风险等级及其原因。"""

        if record.risk_level == "low":
            return
        thought_artifact = self.workspace.slots["thought"].artifact
        thought_updates = (
            thought_artifact.updates
            if isinstance(thought_artifact, Stage3ThoughtReviewArtifact)
            else []
        )
        relation_artifact = self.workspace.slots["relation"].artifact
        relation_observations = (
            relation_artifact.observations
            if isinstance(relation_artifact, Stage3RelationReviewArtifact)
            else []
        )
        reasons = _review_risk_explanations(
            record,
            thought_updates,
            relation_observations,
        )
        color = "negative" if record.risk_level == "high" else "warning"
        label = "高风险 · 需要重点复核" if record.risk_level == "high" else "中风险 · 建议复核"
        with ui.card().classes("w-full gap-2 border border-amber-200 bg-amber-50 p-3"):
            ui.badge(label).props(f"color={color}")
            ui.label("程序判定原因").classes("font-semibold")
            for reason in reasons:
                ui.label(f"• {reason}").classes("text-sm whitespace-pre-wrap")
            ui.label("风险标签不会自动决定发布或排除，最终处置仍由人工审核。").classes(
                "text-xs text-slate-600"
            )

    def _update_notes(self, slot_key: str, record: ReviewFields, notes_widget: object) -> None:
        """更新普通审核备注草稿。"""

        try:
            self.workspace.apply(
                slot_key,
                UpdateItemNotesCommand(_review_item_id(record), _widget_text(notes_widget) or None),
            )
            self._notify_success("已更新审核备注草稿")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _complete_item(self, slot_key: str, record: ReviewFields, disposition: str) -> None:
        """完成普通审核单位的最终处置。"""

        try:
            self.workspace.apply(
                slot_key,
                CompleteItemReviewCommand(
                    item_id=_review_item_id(record),
                    disposition=disposition,
                ),
            )
            self._notify_success("已完成审核处置草稿")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _open_nonpublication_dialog(
        self,
        slot_key: str,
        record: ReviewFields,
    ) -> None:
        """打开同时解释 Reject 与 Exclude 的统一不收录对话框。"""

        item_kind = _review_item_kind(record)
        options = review_reason_options(item_kind)
        option_labels = {option.key: option.select_label for option in options}
        with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-full gap-4"):
            ui.label("不纳入世界书").classes("text-xl font-semibold")
            with ui.row().classes("w-full gap-3 items-stretch"):
                with ui.card().classes("flex-1 p-3 gap-1 bg-red-50"):
                    ui.badge("Reject · 候选无效").props("color=negative")
                    ui.label("候选本身不成立，例如证据不足、抽取错误或重复。").classes(
                        "text-sm text-slate-700"
                    )
                with ui.card().classes("flex-1 p-3 gap-1 bg-amber-50"):
                    ui.badge("Exclude · 有效但不收录").props("color=warning")
                    ui.label(
                        "内容可能有效，但当前不进入正式包；保留区别以便统计、复查或未来利用。"
                    ).classes("text-sm text-slate-700")
            reason = ui.select(
                option_labels,
                value=options[0].key,
                label="不纳入原因",
            ).props("options-dense").classes("w-full")
            ui.label("选择“其他”原因时必须填写补充说明。").classes("text-xs text-slate-500")
            note = ui.textarea("原因补充说明").props("autogrow").classes("w-full")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认不纳入",
                    on_click=lambda: self._submit_nonpublication(
                        dialog,
                        slot_key,
                        record,
                        item_kind,
                        reason,
                        note,
                    ),
                ).props("unelevated color=primary")
        dialog.open()

    def _submit_nonpublication(
        self,
        dialog: object,
        slot_key: str,
        record: ReviewFields,
        item_kind: ReviewItemKind,
        reason_widget: object,
        note_widget: object,
    ) -> None:
        """根据中文原因映射提交 Reject 或 Exclude 最终处置。"""

        try:
            reason = review_reason_by_key(_widget_text(reason_widget), item_kind)
            self.workspace.apply(
                slot_key,
                CompleteItemReviewCommand(
                    item_id=_review_item_id(record),
                    disposition=reason.disposition,
                    reason_code=reason.reason_code,
                    reason_note=_widget_text(note_widget) or None,
                ),
            )
            getattr(dialog, "close")()
            self._notify_success("已完成审核处置草稿")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _mark_followup(self, slot_key: str, record: ReviewFields, notes_widget: object) -> None:
        """把普通审核单位标记为待跟进。"""

        try:
            self.workspace.apply(
                slot_key,
                MarkItemFollowupCommand(
                    _review_item_id(record),
                    _widget_text(notes_widget) or None,
                ),
            )
            self._notify_success("已标记为待跟进")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _restore_generated(self, slot_key: str, record: ReviewFields) -> None:
        """恢复当前审核项的机器版本。"""

        try:
            self.workspace.apply(
                slot_key,
                RestoreGeneratedContentCommand(_review_item_id(record)),
            )
            self._notify_success("已恢复机器版本并撤销旧审批")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _resolve_identity(
        self,
        slot_key: str,
        record: ReviewFields,
        choice: str,
        previous_id: str | None,
    ) -> None:
        """确认当前候选的开发侧身份。"""

        try:
            self.workspace.apply(
                slot_key,
                ResolveIdentityCommand(
                    item_id=_review_item_id(record),
                    choice=choice,
                    previous_id=previous_id,
                ),
            )
            self._notify_success("已完成身份确认并撤销旧审批")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def refresh_evidence(self) -> None:
        """刷新机器基准、上一版人工结果和局部证据。"""

        if self.evidence_column is None:
            return
        self.evidence_column.clear()
        with self.evidence_column:
            located = self._locate_current()
            if located is None:
                ui.label("选择审核项后在这里查看机器基准和证据。").classes("text-slate-500")
                return
            _slot, record = located
            generated = _generated_payload(record)
            if generated is not None:
                with ui.expansion("机器基准", icon="smart_toy").classes("w-full"):
                    ui.code(json.dumps(generated, ensure_ascii=False, indent=2)).classes("mono-wrap")
            previous = _previous_payload(record)
            if previous is not None:
                with ui.expansion("上一版人工结果", icon="history").classes("w-full"):
                    ui.code(json.dumps(previous, ensure_ascii=False, indent=2)).classes("mono-wrap")
            for line in self._evidence_lines(record):
                ui.label(line).classes("text-sm whitespace-pre-wrap")
            self._build_assignment_controls(record)

    def _evidence_lines(self, record: BaseModel) -> list[str]:
        """返回当前审核项的证据摘要文本。"""

        if isinstance(record, (StoryEventReviewRecord, LoreEntryReviewRecord)):
            return [
                f"scene: {record.source_scene_id} · local: {record.source_local_id}",
                "utterances: " + ", ".join(record.evidence_u_ids),
                "screen text: " + ", ".join(record.evidence_s_ids),
                *self.workspace.evidence_context(
                    {record.source_scene_id},
                    set(record.evidence_u_ids),
                    set(record.evidence_s_ids),
                ),
            ]
        relation = self.workspace.slots["relation"].artifact
        if isinstance(record, RelationTypeReviewRecord) and isinstance(relation, Stage3RelationReviewArtifact):
            ids = {
                item
                for state in record.effective_content().states
                for item in state.supporting_observation_ids
            }
            observations = [
                item
                for item in relation.observations
                if item.observation_id in ids
            ]
            return [
                f"{item.observation_id} · {item.time_order} · {item.observation_text}"
                for item in observations
            ] + list(
                self.workspace.evidence_context(
                    {item.scene_id for item in observations},
                    {
                        utterance_id
                        for item in observations
                        for utterance_id in item.evidence_u_ids
                    },
                    set(),
                )
            )
        thought = self.workspace.slots["thought"].artifact
        if isinstance(record, ThoughtThreadReviewRecord) and isinstance(thought, Stage3ThoughtReviewArtifact):
            ids = {
                item
                for state in record.effective_content().states
                for item in state.supporting_update_ids
            }
            updates = [item for item in thought.updates if item.update_id in ids]
            return [
                f"{item.update_id} · {item.evidence_time} · {item.thought_text}"
                for item in updates
            ] + list(
                self.workspace.evidence_context(
                    {item.source_scene_id for item in updates},
                    {
                        utterance_id
                        for item in updates
                        for utterance_id in item.evidence_u_ids
                    },
                    set(),
                )
            )
        if isinstance(record, UnmergedRelationObservationDecision) and isinstance(
            relation, Stage3RelationReviewArtifact
        ):
            observation = next(
                (item for item in relation.observations if item.observation_id == record.observation_id),
                None,
            )
            if observation is None:
                return []
            return [
                observation.observation_text,
                *self.workspace.evidence_context(
                    {observation.scene_id},
                    set(observation.evidence_u_ids),
                    set(),
                ),
            ]
        if isinstance(record, UnassignedThoughtUpdateDecision) and isinstance(
            thought, Stage3ThoughtReviewArtifact
        ):
            update = next((item for item in thought.updates if item.update_id == record.update_id), None)
            if update is None:
                return []
            return [
                update.thought_text,
                *self.workspace.evidence_context(
                    {update.source_scene_id},
                    set(update.evidence_u_ids),
                    set(),
                ),
            ]
        return []

    def _build_assignment_controls(self, record: BaseModel) -> None:
        """为未归属 Observation/Update 构建归线操作。"""

        if isinstance(record, UnmergedRelationObservationDecision):
            artifact = self.workspace.slots["relation"].artifact
            if not isinstance(artifact, Stage3RelationReviewArtifact):
                return
            observation = next(
                (item for item in artifact.observations if item.observation_id == record.observation_id),
                None,
            )
            if observation is None:
                return
            targets = {
                item.relation_type_id: item.effective_content().semantic_label
                for item in artifact.relation_types
                if item.subject_character_id == observation.subject_character_id
                and item.object_character_id == observation.object_character_id
                and item.effective_content().states
            }
            if targets:
                target = ui.select(targets, label="归入 Relation Type").classes("w-full")
                state_index = ui.number("目标 State 序号（从 1 开始）", value=1, precision=0)
                ui.button(
                    "归入目标 State",
                    on_click=lambda: self._assign_observation(record, target, state_index),
                ).props("outline")
        elif isinstance(record, UnassignedThoughtUpdateDecision):
            artifact = self.workspace.slots["thought"].artifact
            if not isinstance(artifact, Stage3ThoughtReviewArtifact):
                return
            update = next((item for item in artifact.updates if item.update_id == record.update_id), None)
            if update is None:
                return
            targets = {
                item.thought_thread_id: (
                    f"{item.effective_content().canonical_subject} / {item.effective_content().thought_aspect}"
                )
                for item in artifact.threads
                if item.character_id == update.character_id and item.effective_content().states
            }
            if targets:
                target = ui.select(targets, label="归入 Thought Thread").classes("w-full")
                state_index = ui.number("目标 State 序号（从 1 开始）", value=1, precision=0)
                ui.button(
                    "归入目标 State",
                    on_click=lambda: self._assign_update(record, target, state_index),
                ).props("outline")

    def _assign_observation(
        self,
        record: UnmergedRelationObservationDecision,
        target_widget: object,
        state_widget: object,
    ) -> None:
        """把未归属 Observation 移入目标 Relation State。"""

        try:
            self.workspace.apply(
                "relation",
                AssignRelationObservationCommand(
                    observation_id=record.observation_id,
                    target_type_id=_widget_text(target_widget),
                    target_state_index=_widget_int(state_widget) - 1,
                ),
            )
            self.current_item_id = None
            self._notify_success("已把 Observation 归入目标 Relation State")
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            self._notify_error(exc)

    def _assign_update(
        self,
        record: UnassignedThoughtUpdateDecision,
        target_widget: object,
        state_widget: object,
    ) -> None:
        """把未归属 Update 移入目标 Thought State。"""

        try:
            self.workspace.apply(
                "thought",
                AssignThoughtUpdateCommand(
                    update_id=record.update_id,
                    target_thread_id=_widget_text(target_widget),
                    target_state_index=_widget_int(state_widget) - 1,
                ),
            )
            self.current_item_id = None
            self._notify_success("已把 Update 归入目标 Thought State")
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            self._notify_error(exc)

    def _locate_current(self) -> tuple[ArtifactSlot, BaseModel] | None:
        """在当前审核域中定位选中的 Pydantic 记录。"""

        if self.current_item_id is None:
            return None
        for slot in self.workspace.slots.values():
            artifact = slot.artifact
            if isinstance(artifact, Stage3DocumentReviewArtifact):
                records: list[BaseModel] = [*artifact.story_events, *artifact.lore_entries]
            elif isinstance(artifact, Stage3RelationReviewArtifact):
                records = [*artifact.relation_types, *artifact.unmerged_observations]
            elif isinstance(artifact, Stage3ThoughtReviewArtifact):
                records = [*artifact.threads, *artifact.unassigned_updates]
            elif isinstance(artifact, Stage3LoreDecisionsArtifact):
                records = list(artifact.decisions)
            else:
                continue
            for record in records:
                if _model_item_id(record) == self.current_item_id:
                    return slot, record
        return None

    def _find_lore_candidate(self, candidate_id: str) -> LoreEntryReviewRecord | None:
        """在全部逐集 Review 中查找 Lore 候选。"""

        for slot in self.workspace.slots.values():
            if isinstance(slot.artifact, Stage3DocumentReviewArtifact):
                for record in slot.artifact.lore_entries:
                    if record.candidate_id == candidate_id:
                        return record
        return None

    def batch_complete(self) -> None:
        """按文件批量审核通过显式选择且无阻断问题的项目。"""

        grouped: dict[str, list[str]] = {}
        for slot_key, item_id in sorted(self.selected_items):
            grouped.setdefault(slot_key, []).append(item_id)
        if not grouped:
            ui.notify("请先显式勾选审核项", color="warning")
            return
        try:
            for slot_key, item_ids in grouped.items():
                if slot_key == "lore_decisions":
                    raise ValueError("Lore 去重组不能使用普通批量审核通过")
                self.workspace.apply(
                    slot_key,
                    BatchCompleteReviewCommand(tuple(item_ids)),
                )
            self.selected_items.clear()
            self._notify_success("已批量审核通过显式选择的项目")
        except (TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def undo(self) -> None:
        """撤销当前文件的上一条会话命令。"""

        if self.workspace.undo():
            self._notify_success("已撤销")
        else:
            ui.notify("没有可撤销操作", color="warning")

    def redo(self) -> None:
        """重做当前文件刚撤销的会话命令。"""

        if self.workspace.redo():
            self._notify_success("已重做")
        else:
            ui.notify("没有可重做操作", color="warning")

    def save_current(self) -> None:
        """保存当前审核文件。"""

        try:
            path = self.workspace.save_current()
            self._notify_success(f"已保存 {path.name}")
        except (OSError, TypeError, ValueError) as exc:
            self._notify_error(exc)

    def save_all(self) -> None:
        """预校验后保存全部脏审核文件。"""

        try:
            result = self.workspace.save_all()
            if result.failed:
                ui.notify(
                    "部分文件保存失败：" + "、".join(result.failed),
                    color="negative",
                )
                self.refresh()
                return
            self._notify_success(f"已保存 {len(result.saved_keys)} 个审核文件")
        except (OSError, TypeError, ValueError) as exc:
            self._notify_error(exc)

    def open_regeneration_dialog(self) -> None:
        """打开当前审核文件的显式重生成对话框。"""

        slot = self.workspace.current_slot()
        if self.workspace.dirty_keys():
            ui.notify("重生成前请先保存全部草稿", color="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("w-[820px] max-w-full gap-4"):
            ui.label(f"重生成 · {slot.label}").classes("text-xl font-semibold")
            freshness = self.workspace.freshness(slot.key)
            if freshness.missing:
                ui.label("当前产物尚未生成。")
            elif freshness.stale_sources:
                ui.label("已变化来源：" + "、".join(freshness.stale_sources)).classes(
                    "text-amber-700"
                )
            else:
                ui.label("当前来源没有变化；仍可显式重新生成。")
            allow_all = ui.checkbox("允许本轮全部已发布身份消失（执行前请人工核对）")
            if slot.key.startswith("document:"):
                episode = int(slot.key.split(":", 1)[1])
                episode_paths = next(
                    value for value in self.workspace.resolved.episodes if value[0].episode == episode
                )
                ui.code(
                    "PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag "
                    f"--input {episode_paths[1]} --annotation {episode_paths[2]} "
                    f"--output {episode_paths[4]}"
                ).classes("mono-wrap")
                ui.label("预计影响：覆盖本集 Review，并按机器基准迁移或重置旧审核。")
                ui.button(
                    "重新生成本集 Review",
                    on_click=lambda: self._regenerate_document(
                        dialog,
                        episode,
                        _widget_bool(allow_all),
                    ),
                ).props("unelevated color=warning")
            elif slot.key == "lore_decisions":
                ui.code(
                    "PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage3-lore-decisions "
                    "--input <逐集 Review，可重复> "
                    f"--output {self.workspace.resolved.lore_decisions}"
                ).classes("mono-wrap")
                ui.label("预计影响：重新扫描全部已发布 Lore 候选并迁移相同重复组决定。")
                ui.button(
                    "重新扫描 Lore 重复组",
                    on_click=lambda: self._regenerate_lore(dialog),
                ).props("unelevated color=warning")
            elif slot.key in {"relation", "thought"}:
                self._build_prompt_regeneration_controls(dialog, slot.key, allow_all)
            with ui.row().classes("w-full justify-end"):
                ui.button("关闭", on_click=dialog.close).props("flat")
        dialog.open()

    def _build_prompt_regeneration_controls(
        self,
        dialog: object,
        slot_key: str,
        allow_all_widget: object,
    ) -> None:
        """构建 Relation/Thought 的 render、等待与 assemble 三步控件。"""

        default_dir = (
            self.workspace.resolved.path.parent
            / "prompt_packages"
            / f"{slot_key}_{uuid4().hex[:8]}"
        )
        ui.label("第 1 步：生成新的 Prompt Package").classes("font-semibold")
        output_dir = ui.input("Prompt Package 输出目录", value=str(default_dir)).classes("w-full")
        render_command = (
            "render-stage3-relation-prompts"
            if slot_key == "relation"
            else "render-stage3-thought-prompts"
        )
        ui.code(
            f"PYTHONPATH=GPT_SoVITS python -m rag.pipeline {render_command} "
            "<build spec 中的多集输入参数> "
            f"--output-dir {default_dir}"
        ).classes("mono-wrap")
        ui.button(
            "生成 Prompt Package",
            on_click=lambda: self._render_prompt_package(
                slot_key,
                _widget_text(output_dir),
            ),
        ).props("outline color=warning")
        ui.separator()
        ui.label("第 2 步：填写 manifest 声明的 responses 文件").classes("font-semibold")
        ui.label("工作台不会假装自动完成人工或 Codex 响应；填完后再执行下一步。")
        ui.separator()
        ui.label("第 3 步：校验 responses 并组装全量 Review").classes("font-semibold")
        manifest = ui.input(
            "manifest.json 路径",
            value=str(default_dir / "manifest.json"),
        ).classes("w-full")
        model_label = ui.input("模型来源标签", value="codex-workspace").classes("w-full")
        assemble_command = (
            "assemble-stage3-relations"
            if slot_key == "relation"
            else "assemble-stage3-thought-responses"
        )
        output_path = (
            self.workspace.resolved.relation_review
            if slot_key == "relation"
            else self.workspace.resolved.thought_review
        )
        ui.code(
            f"PYTHONPATH=GPT_SoVITS python -m rag.pipeline {assemble_command} "
            f"--manifest <上方路径> --output {output_path} --model-label <上方标签>"
        ).classes("mono-wrap")
        ui.label("预计影响：覆盖全量 Review，并展示审核迁移、重置、新增和消失数量。")
        ui.button(
            "校验并组装 Review",
            on_click=lambda: self._assemble_prompt_package(
                dialog,
                slot_key,
                _widget_text(manifest),
                _widget_text(model_label),
                _widget_bool(allow_all_widget),
            ),
        ).props("unelevated color=warning")

    async def _regenerate_document(
        self,
        dialog: object,
        episode: int,
        allow_all_removed: bool,
    ) -> None:
        """在 I/O worker 中重生成一集 Review 并重新加载槽位。"""

        try:
            result = await run.io_bound(
                self.regenerator.regenerate_document,
                episode,
                None,
                allow_all_removed,
            )
            self._finish_regeneration(dialog, result)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    async def _regenerate_lore(self, dialog: object) -> None:
        """在 I/O worker 中重新扫描 Lore decisions 并重新加载槽位。"""

        try:
            result = await run.io_bound(self.regenerator.rebuild_lore_decisions)
            self._finish_regeneration(dialog, result)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    async def _render_prompt_package(self, slot_key: str, output_dir: str) -> None:
        """在 I/O worker 中渲染 Relation 或 Thought Prompt Package。"""

        try:
            method = (
                self.regenerator.render_relation_prompts
                if slot_key == "relation"
                else self.regenerator.render_thought_prompts
            )
            result = await run.io_bound(method, output_dir)
            ui.notify(
                f"{result.summary()}；manifest: {result.output_path}",
                color="positive",
                position="top",
            )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    async def _assemble_prompt_package(
        self,
        dialog: object,
        slot_key: str,
        manifest_path: str,
        model_label: str,
        allow_all_removed: bool,
    ) -> None:
        """在 I/O worker 中组装 Relation 或 Thought Review。"""

        try:
            method = (
                self.regenerator.assemble_relation_prompts
                if slot_key == "relation"
                else self.regenerator.assemble_thought_prompts
            )
            result = await run.io_bound(
                method,
                manifest_path,
                model_label,
                False,
                False,
                None,
                allow_all_removed,
            )
            self._finish_regeneration(dialog, result)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._notify_error(exc)

    def _finish_regeneration(
        self,
        dialog: object,
        result: RegenerationResult,
    ) -> None:
        """重新加载已覆盖的 artifact，并展示一次迁移摘要。"""

        self.workspace.reload(result.slot_key, discard_dirty=True)
        self.workspace.select(result.slot_key)
        self.current_item_id = None
        getattr(dialog, "close")()
        self._notify_success(f"重生成完成：{result.summary()}")

    def run_audit(self) -> None:
        """运行纯构建审计并展示结构化问题。"""

        try:
            report = self.workspace.run_audit()
            self.last_audit_messages = [
                " · ".join(
                    part
                    for part in (
                        item.code,
                        item.message,
                        None if item.entry_id is None else f"entry={item.entry_id}",
                        None if item.path is None else f"path={item.path}",
                    )
                    if part is not None
                )
                for item in report.issues
            ] or ["审计成功：当前世界书构建输入已就绪。"]
            self.refresh()
            ui.notify(
                "审计成功" if report.succeeded else "审计未通过，请查看问题列表",
                color="positive" if report.succeeded else "warning",
            )
        except (OSError, TypeError, ValueError) as exc:
            self._notify_error(exc)

    def _notify_success(self, message: str) -> None:
        """显示成功提示并刷新工作台。"""

        ui.notify(message, color="positive", position="top")
        self.refresh()

    def _notify_error(self, error: BaseException) -> None:
        """显示失败提示并保持当前草稿。"""

        ui.notify(f"{type(error).__name__}: {error}", color="negative", position="top")
        self.refresh_header()


def _ordinary_list_item(
    slot_key: str,
    item_id: str,
    kind: str,
    title: str,
    record: ReviewFields,
    human_edited: bool,
    identity_pending: bool,
) -> ReviewListItem:
    """构造普通审核单位的统一队列项。"""

    return ReviewListItem(
        slot_key=slot_key,
        item_id=item_id,
        kind=kind,
        title=title or item_id,
        review_status=record.review_status,
        disposition=record.disposition,
        risk_level=record.risk_level,
        identity_pending=identity_pending,
        human_edited=human_edited,
    )


def _review_item_id(record: ReviewFields) -> str:
    """返回普通审核单位的开发侧稳定 ID。"""

    for field_name in (
        "candidate_id",
        "relation_type_id",
        "observation_id",
        "thought_thread_id",
        "update_id",
    ):
        value = getattr(record, field_name, None)
        if value is not None:
            return str(value)
    raise ValueError("审核项没有可识别的稳定 ID")


def _review_item_kind(record: ReviewFields) -> ReviewItemKind:
    """把具体审核记录映射为原因目录使用的内容类型。"""

    if isinstance(record, StoryEventReviewRecord):
        return "story"
    if isinstance(record, LoreEntryReviewRecord):
        return "lore"
    if isinstance(record, (RelationTypeReviewRecord, UnmergedRelationObservationDecision)):
        return "relation"
    if isinstance(record, (ThoughtThreadReviewRecord, UnassignedThoughtUpdateDecision)):
        return "thought"
    raise TypeError(f"未知审核记录类型: {type(record).__name__}")


def _model_item_id(record: BaseModel) -> str:
    """返回普通审核项或 Lore 组的稳定 ID。"""

    group_id = getattr(record, "group_id", None)
    if group_id is not None:
        return str(group_id)
    if isinstance(record, ReviewFields):
        return _review_item_id(record)
    raise ValueError("记录没有可识别的稳定 ID")


def _generated_payload(record: BaseModel) -> object | None:
    """返回当前记录的机器基准 JSON 投影。"""

    if isinstance(record, (StoryEventReviewRecord, LoreEntryReviewRecord)):
        return record.generated_document.model_dump(mode="json")
    if isinstance(record, RelationTypeReviewRecord):
        return record.generated_content.model_dump(mode="json")
    if isinstance(record, ThoughtThreadReviewRecord):
        return record.generated_content.model_dump(mode="json")
    return None


def _previous_payload(record: BaseModel) -> object | None:
    """返回当前记录上一版人工内容的 JSON 投影。"""

    if isinstance(record, (StoryEventReviewRecord, LoreEntryReviewRecord)):
        return (
            None
            if record.previous_reviewed_document is None
            else record.previous_reviewed_document.model_dump(mode="json")
        )
    if isinstance(record, RelationTypeReviewRecord):
        return (
            None
            if record.previous_reviewed_content is None
            else record.previous_reviewed_content.model_dump(mode="json")
        )
    if isinstance(record, ThoughtThreadReviewRecord):
        return (
            None
            if record.previous_reviewed_content is None
            else record.previous_reviewed_content.model_dump(mode="json")
        )
    return None


def _event_value(event: object) -> str:
    """把 NiceGUI 事件值规范化为字符串。"""

    return str(getattr(event, "value", "") or "")


def _event_checked(event: object) -> bool:
    """把 NiceGUI checkbox 事件值规范化为布尔值。"""

    return bool(getattr(event, "value", False))


def _review_risk_explanations(
    record: ReviewFields,
    thought_updates: list[ThoughtUpdateEvidence],
    relation_observations: list[RelationObservationReviewRecord] | None = None,
) -> list[str]:
    """汇总已有风险原因及可由 Relation/Thought 证据还原的细节。"""

    reasons = [reason.strip() for reason in record.risk_reasons if reason.strip()]
    if isinstance(record, RelationTypeReviewRecord):
        covered_ids = set(record.covered_observation_ids)
        reasons.extend(
            (
                f"来源 Observation {observation.observation_id} 的歧义："
                f"{observation.ambiguity_notes.strip()}"
            )
            for observation in relation_observations or []
            if observation.observation_id in covered_ids
            and observation.ambiguity_notes.strip()
        )
    elif isinstance(record, ThoughtThreadReviewRecord):
        covered_ids = set(record.covered_update_ids)
        inferred_updates = [
            update
            for update in thought_updates
            if update.update_id in covered_ids and update.evidence_strength == "inferred"
        ]
        reasons.extend(
            (
                "该 Thread 使用了推断性 Thought Update（并非角色明确表达），"
                f"需要确认长期观点是否成立：{update.update_id} · {update.thought_text}"
            )
            for update in inferred_updates
        )
    elif isinstance(record, UnassignedThoughtUpdateDecision):
        if record.kind == "unresolved":
            reasons.append(
                "模型未能把该 Update 归入任何 Thought Thread，需要人工判断应当归线还是不纳入世界书。"
            )
        elif record.kind == "excluded":
            reasons.append(
                "模型认为该 Update 不构成长期 Thought Thread，只建议排除，仍需人工确认。"
            )
    if not reasons:
        reasons.append("该条目被自动标记为较高风险，但当前产物没有记录更具体的原因。")
    return list(dict.fromkeys(reasons))


def _lore_dedup_action_description(action: object, candidate_count: int) -> str:
    """返回 Lore 去重动作的中文结果说明。"""

    action_value = str(action or "")
    if action_value == "keep_separate":
        return f"本组 {candidate_count} 条候选都会作为独立 Lore 条目发布。"
    if action_value == "merge":
        return (
            "最终只发布下面选中的候选，其余候选不发布。"
            "当前功能不会自动综合多条候选的文本。"
        )
    if action_value == "drop":
        return (
            f"本组全部 {candidate_count} 条候选都不会发布。"
            "这不是“删除重复项并保留一条”。"
        )
    if action_value == "auto_merge_identical":
        return "候选内容完全一致，程序已自动选择其中一条保留；该决定不允许人工覆盖。"
    return "请选择这组候选的处理方式。"


def _lore_candidate_badge(
    action: object,
    primary_candidate_id: object,
    candidate_id: str,
) -> tuple[str, str]:
    """返回候选在当前 Lore 去重动作下的状态标签与颜色。"""

    action_value = str(action or "")
    primary_id = str(primary_candidate_id or "")
    if action_value == "keep_separate":
        return "分别保留", "positive"
    if action_value == "drop":
        return "整组丢弃", "negative"
    if action_value == "merge":
        return (
            ("最终保留", "positive")
            if candidate_id == primary_id
            else ("合并后移除", "warning")
        )
    if action_value == "auto_merge_identical":
        return (
            ("自动保留", "positive")
            if candidate_id == primary_id
            else ("自动移除", "warning")
        )
    return "待决定", "grey"


def _widget_value(widget: object) -> object:
    """读取 NiceGUI 表单控件当前值。"""

    return getattr(widget, "value", None)


def _widget_text(widget: object) -> str:
    """读取并清理表单文本值。"""

    value = _widget_value(widget)
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip()


def _widget_int(widget: object) -> int:
    """读取必填整数表单值。"""

    value = _widget_value(widget)
    if value is None or str(value).strip() == "":
        raise ValueError("必填整数不可为空")
    return int(value)


def _widget_optional_int(widget: object) -> int | None:
    """读取可空整数表单值。"""

    value = _widget_value(widget)
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _widget_bool(widget: object) -> bool:
    """读取 checkbox 控件布尔值。"""

    return bool(_widget_value(widget))


def _widget_csv(widget: object) -> list[str]:
    """把逗号分隔表单值规范化为去重字符串列表。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in _widget_text(widget).replace("，", ",").split(","):
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _widget_string_list(widget: object) -> list[str]:
    """读取多选控件的字符串列表，并兼容旧逗号分隔输入。"""

    value = _widget_value(widget)
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = str(item).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result
    return _widget_csv(widget)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析审核工作台启动参数。"""

    parser = argparse.ArgumentParser(description="启动统一 Stage 3 审核工作台")
    parser.add_argument("--build-spec", default=str(DEFAULT_BUILD_SPEC), help="worldbook_build.json 路径")
    parser.add_argument("--host", default="127.0.0.1", help="NiceGUI 绑定 host")
    parser.add_argument("--port", type=int, default=8188, help="NiceGUI 端口")
    parser.add_argument("--native", action="store_true", help="使用 NiceGUI native 模式")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """启动统一 Stage 3 审核工作台。"""

    args = parse_args(argv)

    def build_root() -> None:
        """为每个浏览器会话创建独立的审核工作区页面。"""

        workbench = Stage3ReviewWorkbench(Path(args.build_spec))
        workbench.build_ui()

    ui.run(
        root=build_root,
        host=args.host,
        port=args.port,
        native=args.native,
        reload=False,
        title="Stage 3 审核工作台",
        favicon="🧭",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
