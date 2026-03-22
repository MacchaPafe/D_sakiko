"""NiceGUI 驱动的 Stage3 RAG 数据集编辑器。"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_PACKAGE_ROOT = PIPELINE_ROOT.parents[1]
if str(PROJECT_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PACKAGE_ROOT))

try:
    from nicegui import ui
except ImportError as exc:  # pragma: no cover - 运行期依赖提示
    raise SystemExit(
        "缺少 nicegui 依赖。请先安装项目依赖后再运行：`uv sync` 或 `pip install nicegui`。"
    ) from exc

from rag.pipeline.schemas import Stage3NormalizedImportArtifact

DEFAULT_INPUT_PATH = PIPELINE_ROOT / "data" / "annotations_stage3" / "ep01_rag_ready.json"


def dedupe_texts(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """去重并清理文本列表，同时保留原顺序。"""

    if not values:
        return []

    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def ordered_union(*groups: list[str]) -> list[str]:
    """按传入顺序合并列表并去重。"""

    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


@dataclass(frozen=True)
class SectionSpec:
    key: str
    label: str
    description: str
    accent_class: str
    empty_hint: str


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="story_events",
        label="Story Events",
        description="剧情事件，适合检查标题、摘要、参与角色与检索文案是否准确。",
        accent_class="accent-story",
        empty_hint="当前没有剧情事件记录。",
    ),
    SectionSpec(
        key="character_relations",
        label="Character Relations",
        description="角色关系，重点关注主客体之间的关系标签、状态描述与说话风格。",
        accent_class="accent-relation",
        empty_hint="当前没有角色关系记录。",
    ),
    SectionSpec(
        key="lore_entries",
        label="Lore Entries",
        description="世界观/设定条目，适合统一术语标题、内容说明和检索文本。",
        accent_class="accent-lore",
        empty_hint="当前没有设定条目记录。",
    ),
)

SECTION_ORDER = [spec.key for spec in SECTION_SPECS]
SECTION_MAP = {spec.key: spec for spec in SECTION_SPECS}


class Stage3DatasetEditor:
    """Stage3 结构化 RAG 数据编辑器。"""

    def __init__(self, input_path: Path) -> None:
        self.input_path = input_path.resolve()
        self.backup_path = self.input_path.with_suffix(f"{self.input_path.suffix}.bak")
        self.data = self._load_file(self.input_path)

        self.current_section = self._pick_initial_section()
        self.current_indices = {key: 0 for key in SECTION_ORDER}
        self.dirty = False
        self.last_saved_at: str | None = None
        self._syncing_form = False
        self.last_deleted_snapshot: dict[str, Any] | None = None

        self.character_pool = self._build_character_pool()
        self.tag_pool = self._build_tag_pool()

        self.section_column: ui.column | None = None
        self.record_column: ui.column | None = None
        self.record_editor_column: ui.column | None = None
        self.metadata_column: ui.column | None = None

        self.progress_label: ui.label | None = None
        self.status_badge: ui.badge | None = None
        self.footer_hint: ui.label | None = None
        self.file_path_label: ui.label | None = None
        self.delete_button: ui.button | None = None
        self.undo_delete_button: ui.button | None = None

    @staticmethod
    def _load_file(path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        Stage3NormalizedImportArtifact.model_validate(raw)
        return raw

    def _pick_initial_section(self) -> str:
        for section in SECTION_ORDER:
            if self.data.get(section):
                return section
        return SECTION_ORDER[0]

    def records(self, section: str | None = None) -> list[dict[str, Any]]:
        return self.data.get(section or self.current_section, [])

    def current_index(self, section: str | None = None) -> int:
        key = section or self.current_section
        records = self.records(key)
        if not records:
            return 0
        return max(0, min(self.current_indices.get(key, 0), len(records) - 1))

    def current_record(self) -> dict[str, Any] | None:
        records = self.records()
        if not records:
            return None
        return records[self.current_index()]

    def current_document(self) -> dict[str, Any] | None:
        record = self.current_record()
        if record is None:
            return None
        return record["document"]

    def total_records(self) -> int:
        return len(self.records())

    def _build_character_pool(self) -> list[str]:
        story_participants = [
            participant
            for record in self.data.get("story_events", [])
            for participant in record.get("document", {}).get("participants", [])
        ]
        relation_ids = [
            character_id
            for record in self.data.get("character_relations", [])
            for character_id in (
                record.get("document", {}).get("subject_character_id", ""),
                record.get("document", {}).get("object_character_id", ""),
            )
        ]
        return ordered_union(dedupe_texts(story_participants), dedupe_texts(relation_ids))

    def _build_tag_pool(self) -> list[str]:
        all_tags = [
            tag
            for section in SECTION_ORDER
            for record in self.data.get(section, [])
            for tag in record.get("document", {}).get("tags", [])
        ]
        return dedupe_texts(all_tags)

    def _section_position_text(self) -> str:
        total = self.total_records()
        if total == 0:
            return f"{SECTION_MAP[self.current_section].label} · 0 / 0"
        return f"{SECTION_MAP[self.current_section].label} · {self.current_index() + 1} / {total}"

    def set_section(self, section: str) -> None:
        if section not in SECTION_MAP:
            return
        self.current_section = section
        self.current_indices[section] = self.current_index(section)
        self.refresh_ui()

    def set_current_index(self, index: int) -> None:
        records = self.records()
        if not records:
            self.current_indices[self.current_section] = 0
            self.refresh_ui()
            return
        self.current_indices[self.current_section] = max(0, min(index, len(records) - 1))
        self.refresh_ui()

    def go_prev_record(self) -> None:
        if not self.records():
            return
        self.set_current_index(self.current_index() - 1)

    def go_next_record(self) -> None:
        if not self.records():
            return
        self.set_current_index(self.current_index() + 1)

    def go_prev_section(self) -> None:
        current = SECTION_ORDER.index(self.current_section)
        self.set_section(SECTION_ORDER[max(0, current - 1)])

    def go_next_section(self) -> None:
        current = SECTION_ORDER.index(self.current_section)
        self.set_section(SECTION_ORDER[min(len(SECTION_ORDER) - 1, current + 1)])

    def mark_dirty(self) -> None:
        self.dirty = True
        self.character_pool = self._build_character_pool()
        self.tag_pool = self._build_tag_pool()
        self.refresh_status()

    def _capture_delete_snapshot(self, record: dict[str, Any], index: int) -> None:
        self.last_deleted_snapshot = {
            "section": self.current_section,
            "index": index,
            "record": copy.deepcopy(record),
        }

    def delete_current_record(self) -> None:
        records = self.records()
        record = self.current_record()
        if not records or record is None:
            ui.notify("当前没有可删除的记录。", color="warning", position="top")
            return

        index = self.current_index()
        self._capture_delete_snapshot(record, index)
        records.pop(index)

        if records:
            self.current_indices[self.current_section] = min(index, len(records) - 1)
        else:
            self.current_indices[self.current_section] = 0

        self.mark_dirty()
        self.refresh_ui()
        ui.notify("已删除当前记录，可点击“撤销删除”恢复。", color="warning", position="top")

    def undo_delete(self) -> None:
        if self.last_deleted_snapshot is None:
            ui.notify("当前没有可撤销的删除。", color="warning", position="top")
            return

        snapshot = self.last_deleted_snapshot
        section = str(snapshot["section"])
        target_records = self.records(section)
        restore_index = max(0, min(int(snapshot["index"]), len(target_records)))
        target_records.insert(restore_index, copy.deepcopy(snapshot["record"]))

        self.current_section = section
        self.current_indices[section] = restore_index
        self.dirty = True
        self.last_deleted_snapshot = None
        self.character_pool = self._build_character_pool()
        self.tag_pool = self._build_tag_pool()
        self.refresh_ui()
        ui.notify("已恢复刚才删除的记录。", color="positive", position="top")

    def open_delete_dialog(self) -> None:
        record = self.current_record()
        if record is None:
            ui.notify("当前没有可删除的记录。", color="warning", position="top")
            return

        title = self._record_title(self.current_section, record)
        section_label = SECTION_MAP[self.current_section].label

        with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full gap-4 p-5"):
            ui.label("确认删除记录").classes("text-xl font-bold text-slate-800")
            ui.label(
                "删除会立刻从当前分栏移除这条记录。若是误删，可在之后点击“撤销删除”恢复上一次删除。"
            ).classes("text-sm text-slate-600")
            with ui.column().classes("readonly-pill-panel gap-2"):
                ui.label(section_label).classes("field-label")
                ui.label(title).classes("text-base font-semibold text-slate-900")
                ui.label(record["point_id"]).classes("text-sm text-slate-500 break-all")

            with ui.row().classes("w-full justify-end gap-2 pt-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认删除",
                    on_click=lambda: (dialog.close(), self.delete_current_record()),
                ).props("unelevated color=negative")

        dialog.open()

    def save(self) -> None:
        Stage3NormalizedImportArtifact.model_validate(self.data)
        if not self.backup_path.exists():
            self.backup_path.write_text(
                self.input_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        self.input_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.dirty = False
        self.last_deleted_snapshot = None
        self.last_saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.character_pool = self._build_character_pool()
        self.tag_pool = self._build_tag_pool()
        self.refresh_ui()
        ui.notify(f"已保存到 {self.input_path.name}", color="positive", position="top")

    def _update_text_field(self, field_name: str, value: Any) -> None:
        if self._syncing_form:
            return
        document = self.current_document()
        if document is None:
            return
        text = str(value or "").strip()
        if document.get(field_name, "") == text:
            return
        document[field_name] = text
        self.mark_dirty()
        self.refresh_record_list()
        self.refresh_metadata_panel()

    def _update_list_field(self, field_name: str, value: Any) -> None:
        if self._syncing_form:
            return
        if value is None:
            normalized: list[str] = []
        elif isinstance(value, (list, tuple)):
            normalized = dedupe_texts([str(item) for item in value])
        else:
            normalized = dedupe_texts([str(value)])

        document = self.current_document()
        if document is None:
            return
        if document.get(field_name, []) == normalized:
            return
        document[field_name] = normalized
        self.mark_dirty()
        self.refresh_record_list()
        self.refresh_metadata_panel()
        self.refresh_editor()

    def _record_title(self, section: str, record: dict[str, Any]) -> str:
        document = record["document"]
        if section == "story_events":
            return document.get("title", "") or record["point_id"]
        if section == "character_relations":
            subject = document.get("subject_character_id", "?")
            relation = document.get("relation_label", "未命名关系")
            obj = document.get("object_character_id", "?")
            return f"{subject} -> {obj} · {relation}"
        return document.get("title", "") or record["point_id"]

    def _record_preview(self, section: str, record: dict[str, Any]) -> str:
        document = record["document"]
        if section == "story_events":
            participants = "、".join(document.get("participants", [])[:4]) or "暂无 participants"
            return f"{participants} · {document.get('summary', '')[:80]}"
        if section == "character_relations":
            nickname = document.get("object_character_nickname", "")
            suffix = f" · 称呼 {nickname}" if nickname else ""
            return f"{document.get('state_summary', '')[:78]}{suffix}"
        return document.get("content", "")[:90]

    def _record_badges(self, section: str, record: dict[str, Any]) -> list[str]:
        document = record["document"]
        if section == "story_events":
            base = [f"scene {record.get('source_scene_id', '-')}", f"importance {document.get('importance', '-')}" ]
            base.extend(document.get("tags", [])[:2])
            return base
        if section == "character_relations":
            base = [
                f"scene {record.get('source_scene_id', '-')}",
                f"{document.get('subject_character_id', '-')}",
                f"{document.get('object_character_id', '-')}",
            ]
            base.extend(document.get("tags", [])[:1])
            return base
        base = [f"scene {record.get('source_scene_id', '-')}", f"scope {document.get('scope_type', '-')}"]
        base.extend(document.get("tags", [])[:2])
        return base

    def _character_options(self, current_values: list[str]) -> list[str]:
        return ordered_union(dedupe_texts(current_values), self.character_pool)

    def _tag_options(self, current_values: list[str]) -> list[str]:
        return ordered_union(dedupe_texts(current_values), self.tag_pool)

    @staticmethod
    def _readonly_badge_row(values: list[str], empty_text: str = "无") -> None:
        with ui.row().classes("w-full flex-wrap gap-2"):
            if values:
                for value in values:
                    ui.badge(value).classes("meta-badge")
            else:
                ui.badge(empty_text).classes("meta-badge")

    def refresh_status(self) -> None:
        if self.progress_label is not None:
            self.progress_label.text = self._section_position_text()

        if self.status_badge is not None:
            if self.dirty:
                self.status_badge.text = "有未保存修改"
                self.status_badge.classes(
                    remove="bg-emerald-100 text-emerald-900",
                    add="bg-amber-100 text-amber-900",
                )
            else:
                suffix = f" · {self.last_saved_at}" if self.last_saved_at else ""
                self.status_badge.text = f"已保存{suffix}"
                self.status_badge.classes(
                    remove="bg-amber-100 text-amber-900",
                    add="bg-emerald-100 text-emerald-900",
                )

        if self.footer_hint is not None:
            if self.dirty:
                undo_hint = " 支持撤销上一次删除。" if self.last_deleted_snapshot is not None else ""
                self.footer_hint.text = f"当前修改还在会话里，记得保存后再结束。{undo_hint}"
            else:
                self.footer_hint.text = "当前文件与磁盘内容一致，可以继续切换记录。"

    def refresh_delete_controls(self) -> None:
        has_record = self.current_record() is not None
        can_undo = self.last_deleted_snapshot is not None

        if self.delete_button is not None:
            if has_record:
                self.delete_button.enable()
            else:
                self.delete_button.disable()

        if self.undo_delete_button is not None:
            if can_undo:
                self.undo_delete_button.enable()
            else:
                self.undo_delete_button.disable()

    def refresh_section_list(self) -> None:
        if self.section_column is None:
            return
        self.section_column.clear()
        with self.section_column:
            for spec in SECTION_SPECS:
                count = len(self.records(spec.key))
                is_active = spec.key == self.current_section
                card = ui.card().classes(f"section-card w-full gap-2 {spec.accent_class}")
                if is_active:
                    card.classes(add="active")
                with card:
                    with ui.row().classes("w-full items-center justify-between gap-3"):
                        with ui.column().classes("gap-0"):
                            ui.label(spec.label).classes("text-base font-semibold")
                            ui.label(spec.description).classes("text-xs opacity-80 leading-5")
                        ui.badge(str(count)).classes("count-badge")
                card.on("click", lambda _=None, target=spec.key: self.set_section(target))

    def refresh_record_list(self) -> None:
        if self.record_column is None:
            return
        self.record_column.clear()
        records = self.records()
        spec = SECTION_MAP[self.current_section]
        with self.record_column:
            if not records:
                with ui.column().classes("placeholder-panel w-full items-start gap-2"):
                    ui.label(spec.empty_hint).classes("text-base font-semibold text-slate-700")
                    ui.label("可以先切换到其他分栏查看已有内容。").classes("text-sm text-slate-500")
                return

            for index, record in enumerate(records):
                is_active = index == self.current_index()
                card = ui.card().classes("record-card w-full gap-3")
                if is_active:
                    card.classes(add="active")
                with card:
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        with ui.column().classes("gap-1"):
                            ui.label(
                                f"{index + 1:02d} · {self._record_title(self.current_section, record)}"
                            ).classes("text-sm font-semibold leading-6")
                            ui.label(record["point_id"]).classes("text-xs text-slate-500 break-all")
                        ui.badge(f"{record.get('confidence', 0):.2f}").classes("confidence-badge")
                    ui.label(
                        self._record_preview(self.current_section, record) or " "
                    ).classes("record-preview")
                    with ui.row().classes("w-full flex-wrap gap-2"):
                        for badge_text in self._record_badges(self.current_section, record):
                            ui.badge(badge_text).classes("mini-badge")
                card.on("click", lambda _=None, target=index: self.set_current_index(target))

    def refresh_metadata_panel(self) -> None:
        if self.metadata_column is None:
            return
        self.metadata_column.clear()

        metadata = self.data["metadata"]
        issues = self.data.get("issues", [])
        with self.metadata_column:
            with ui.row().classes("w-full items-center justify-between gap-3"):
                with ui.column().classes("gap-0"):
                    ui.label("数据集概览").classes("text-lg font-semibold text-slate-800")
                    ui.label(str(self.input_path)).classes("text-sm text-slate-500 break-all")
                ui.badge(f"issues {len(issues)}").classes("meta-badge")

            with ui.row().classes("w-full flex-wrap gap-2 pt-1"):
                for text in (
                    metadata.get("anime_title", ""),
                    f"series {metadata.get('series_id', '-')}",
                    f"season {metadata.get('season_id', '-')}",
                    f"episode {metadata.get('episode', '-')}",
                    f"branch {metadata.get('canon_branch', '-')}",
                ):
                    ui.badge(text).classes("meta-badge")

            with ui.grid(columns=2).classes("w-full gap-3 pt-2"):
                for key, value in (
                    ("story_events", len(self.data.get("story_events", []))),
                    ("character_relations", len(self.data.get("character_relations", []))),
                    ("lore_entries", len(self.data.get("lore_entries", []))),
                    ("source_stage2_model", metadata.get("source_stage2_model", "")),
                ):
                    with ui.column().classes("stat-card gap-1"):
                        ui.label(str(key)).classes("text-xs uppercase tracking-wide text-slate-500")
                        ui.label(str(value)).classes("text-base font-semibold text-slate-800")

    def _build_readonly_record_summary(self, record: dict[str, Any]) -> None:
        document = record["document"]

        with ui.row().classes("w-full items-start justify-between gap-4").style("flex-wrap: wrap;"):
            with ui.column().classes("gap-1").style("flex: 1 1 480px;"):
                ui.label("point_id").classes("field-label")
                ui.label(record["point_id"]).classes("readonly-code")
            with ui.column().classes("gap-2 items-start"):
                ui.badge(f"confidence {record.get('confidence', 0):.2f}").classes("meta-badge")
                ui.badge(f"scene {record.get('source_scene_id', '-')}").classes("meta-badge")

        with ui.row().classes("w-full flex-wrap gap-2 pt-1"):
            ui.badge(f"source_local_id {record.get('source_local_id', '-')}").classes("meta-badge")
            if self.current_section == "story_events":
                ui.badge(f"time_order {document.get('time_order', '-')}").classes("meta-badge")
                ui.badge(f"importance {document.get('importance', '-')}").classes("meta-badge")
            elif self.current_section == "character_relations":
                ui.badge(f"subject {document.get('subject_character_id', '-')}").classes("meta-badge")
                ui.badge(f"object {document.get('object_character_id', '-')}").classes("meta-badge")
            else:
                ui.badge(f"scope {document.get('scope_type', '-')}").classes("meta-badge")
                visible_from = document.get("visible_from")
                visible_to = document.get("visible_to")
                ui.badge(f"visible {visible_from} -> {visible_to}").classes("meta-badge")

        with ui.expansion("来源与证据", icon="fact_check").classes("w-full source-expansion"):
            with ui.column().classes("w-full gap-4 pt-2"):
                with ui.column().classes("gap-2"):
                    ui.label("evidence_u_ids").classes("field-label")
                    self._readonly_badge_row(record.get("evidence_u_ids", []), empty_text="无 utterance 证据")
                if "evidence_s_ids" in record:
                    with ui.column().classes("gap-2"):
                        ui.label("evidence_s_ids").classes("field-label")
                        self._readonly_badge_row(record.get("evidence_s_ids", []), empty_text="无 screen-text 证据")

    def _build_text_input(
        self,
        label: str,
        value: str,
        field_name: str,
        *,
        textarea: bool = False,
        placeholder: str = "",
    ) -> None:
        component = ui.textarea if textarea else ui.input
        element = component(
            label=label,
            value=value,
            placeholder=placeholder,
            on_change=lambda e, target=field_name: self._update_text_field(target, e.value),
        ).classes("w-full")
        props = "outlined dense"
        if textarea:
            props += " autogrow"
        element.props(props)

    def _build_list_input(
        self,
        label: str,
        values: list[str],
        options: list[str],
        field_name: str,
        *,
        hint: str,
    ) -> None:
        with ui.column().classes("w-full gap-2"):
            ui.label(label).classes("field-label")
            select = ui.select(
                options=options,
                value=values,
                multiple=True,
                on_change=lambda e, target=field_name: self._update_list_field(target, e.value),
            ).classes("w-full")
            select.props(
                "use-chips use-input clearable standout dense options-dense "
                "input-debounce=0 new-value-mode=add-unique"
            )
            ui.label(hint).classes("text-xs text-slate-500")

    def _build_story_event_form(self, document: dict[str, Any]) -> None:
        with ui.column().classes("w-full gap-4"):
            self._build_text_input(
                "title",
                document.get("title", ""),
                "title",
                placeholder="为事件写一个简洁标题",
            )
            self._build_text_input(
                "summary",
                document.get("summary", ""),
                "summary",
                textarea=True,
                placeholder="概括这个剧情事件发生了什么",
            )
            self._build_list_input(
                "participants",
                document.get("participants", []),
                self._character_options(document.get("participants", [])),
                "participants",
                hint="使用 chips 展示参与角色；可直接输入新角色 ID，删除旧 chip 后重输即可修改。",
            )
            self._build_list_input(
                "tags",
                document.get("tags", []),
                self._tag_options(document.get("tags", [])),
                "tags",
                hint="标签适合保持短词组风格，回车即可添加。",
            )
            self._build_text_input(
                "retrieval_text",
                document.get("retrieval_text", ""),
                "retrieval_text",
                textarea=True,
                placeholder="面向检索的自然语言描述",
            )

    def _build_character_relation_form(self, document: dict[str, Any]) -> None:
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full gap-4").style("flex-wrap: wrap;"):
                with ui.column().classes("readonly-pill-panel gap-2").style("flex: 1 1 240px;"):
                    ui.label("subject_character_id").classes("field-label")
                    ui.label(document.get("subject_character_id", "")).classes("readonly-plain")
                with ui.column().classes("readonly-pill-panel gap-2").style("flex: 1 1 240px;"):
                    ui.label("object_character_id").classes("field-label")
                    ui.label(document.get("object_character_id", "")).classes("readonly-plain")

            self._build_text_input(
                "relation_label",
                document.get("relation_label", ""),
                "relation_label",
                placeholder="例如：关心挽留 / 指责愤怒 / 初识观察",
            )
            self._build_text_input(
                "state_summary",
                document.get("state_summary", ""),
                "state_summary",
                textarea=True,
                placeholder="总结当前关系状态与情绪走向",
            )
            self._build_text_input(
                "speech_hint",
                document.get("speech_hint", ""),
                "speech_hint",
                textarea=True,
                placeholder="总结主语对宾语说话时的语气和风格",
            )
            self._build_text_input(
                "object_character_nickname",
                document.get("object_character_nickname", ""),
                "object_character_nickname",
                placeholder="例如：小祥；没有可留空",
            )
            self._build_list_input(
                "tags",
                document.get("tags", []),
                self._tag_options(document.get("tags", [])),
                "tags",
                hint="标签保持短小，便于之后统一检索和筛选。",
            )
            self._build_text_input(
                "retrieval_text",
                document.get("retrieval_text", ""),
                "retrieval_text",
                textarea=True,
                placeholder="面向检索的关系描述",
            )

    def _build_lore_form(self, document: dict[str, Any]) -> None:
        with ui.column().classes("w-full gap-4"):
            self._build_text_input(
                "title",
                document.get("title", ""),
                "title",
                placeholder="为设定条目补一个统一标题",
            )
            self._build_text_input(
                "content",
                document.get("content", ""),
                "content",
                textarea=True,
                placeholder="解释术语、地点、组织或规则的含义",
            )
            self._build_list_input(
                "tags",
                document.get("tags", []),
                self._tag_options(document.get("tags", [])),
                "tags",
                hint="适合写类别词，如学校、地点、乐队、术语等。",
            )
            self._build_text_input(
                "retrieval_text",
                document.get("retrieval_text", ""),
                "retrieval_text",
                textarea=True,
                placeholder="面向检索的设定说明",
            )

    def refresh_editor(self) -> None:
        if self.record_editor_column is None:
            return

        self.record_editor_column.clear()
        record = self.current_record()
        spec = SECTION_MAP[self.current_section]

        with self.record_editor_column:
            if record is None:
                with ui.column().classes("placeholder-panel w-full items-start gap-2"):
                    ui.label(spec.empty_hint).classes("text-base font-semibold text-slate-700")
                    ui.label("当前分栏暂无可编辑数据。").classes("text-sm text-slate-500")
                return

            document = record["document"]
            self._syncing_form = True
            try:
                with ui.column().classes("w-full gap-5"):
                    with ui.column().classes("editor-panel gap-4"):
                        with ui.row().classes("w-full items-start justify-between gap-4").style("flex-wrap: wrap;"):
                            with ui.column().classes("gap-1").style("flex: 1 1 420px;"):
                                ui.label(spec.label).classes("section-eyebrow")
                                ui.label(self._record_title(self.current_section, record)).classes(
                                    "text-2xl font-semibold text-slate-900 leading-8"
                                )
                            ui.label(
                                f"{self.current_index() + 1} / {self.total_records()}"
                            ).classes("text-sm text-slate-500")

                        self._build_readonly_record_summary(record)

                    with ui.column().classes("editor-panel gap-4"):
                        ui.label("可编辑字段").classes("text-lg font-semibold text-slate-800")
                        if self.current_section == "story_events":
                            self._build_story_event_form(document)
                        elif self.current_section == "character_relations":
                            self._build_character_relation_form(document)
                        else:
                            self._build_lore_form(document)
            finally:
                self._syncing_form = False

    def refresh_ui(self) -> None:
        self.refresh_status()
        self.refresh_delete_controls()
        self.refresh_section_list()
        self.refresh_record_list()
        self.refresh_metadata_panel()
        self.refresh_editor()

    def _inject_theme(self) -> None:
        ui.add_head_html(
            """
            <style>
              body {
                background:
                  radial-gradient(circle at top left, rgba(250, 204, 21, 0.18), transparent 26%),
                  radial-gradient(circle at 90% 10%, rgba(14, 165, 233, 0.18), transparent 24%),
                  linear-gradient(180deg, #fffaf4 0%, #f6f8fc 42%, #eef3f7 100%);
                font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
                color: #233142;
              }
              .app-shell {
                max-width: 1720px;
                margin: 0 auto;
                width: 100%;
              }
              .glass-card {
                background: rgba(255, 255, 255, 0.78);
                backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 255, 255, 0.75);
                border-radius: 26px;
                box-shadow: 0 26px 60px rgba(50, 70, 93, 0.12);
              }
              .section-card,
              .record-card {
                cursor: pointer;
                border-radius: 20px;
                border: 1px solid rgba(226, 232, 240, 0.82);
                background: rgba(249, 250, 251, 0.82);
                transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
              }
              .section-card:hover,
              .record-card:hover {
                transform: translateY(-1px);
                box-shadow: 0 16px 30px rgba(15, 23, 42, 0.08);
              }
              .section-card.active,
              .record-card.active {
                box-shadow: 0 18px 34px rgba(15, 23, 42, 0.12);
              }
              .accent-story.active {
                background: linear-gradient(135deg, rgba(225, 245, 254, 0.96), rgba(255, 255, 255, 0.98));
                border-color: rgba(2, 132, 199, 0.28);
              }
              .accent-relation.active {
                background: linear-gradient(135deg, rgba(255, 241, 242, 0.96), rgba(255, 255, 255, 0.98));
                border-color: rgba(225, 29, 72, 0.24);
              }
              .accent-lore.active {
                background: linear-gradient(135deg, rgba(236, 253, 245, 0.96), rgba(255, 255, 255, 0.98));
                border-color: rgba(5, 150, 105, 0.24);
              }
              .record-card.active {
                background: linear-gradient(135deg, rgba(255, 247, 237, 0.98), rgba(255, 255, 255, 0.98));
                border-color: rgba(234, 88, 12, 0.24);
              }
              .editor-panel {
                background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.96));
                border-radius: 22px;
                border: 1px solid rgba(226, 232, 240, 0.92);
                padding: 20px 22px;
              }
              .placeholder-panel {
                background: rgba(255, 255, 255, 0.84);
                border: 1px dashed rgba(148, 163, 184, 0.45);
                border-radius: 20px;
                padding: 20px;
              }
              .stat-card {
                background: rgba(248, 250, 252, 0.92);
                border-radius: 18px;
                border: 1px solid rgba(226, 232, 240, 0.92);
                padding: 14px 16px;
              }
              .field-label {
                font-size: 0.88rem;
                color: #64748b;
                letter-spacing: 0.02em;
              }
              .section-eyebrow {
                font-size: 0.78rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: #0f766e;
              }
              .readonly-code {
                font-size: 0.94rem;
                line-height: 1.6;
                color: #0f172a;
                word-break: break-all;
                background: rgba(248, 250, 252, 0.95);
                border: 1px solid rgba(226, 232, 240, 0.95);
                border-radius: 16px;
                padding: 12px 14px;
              }
              .readonly-pill-panel {
                background: rgba(248, 250, 252, 0.92);
                border-radius: 18px;
                border: 1px solid rgba(226, 232, 240, 0.92);
                padding: 14px 16px;
              }
              .readonly-plain {
                font-size: 1rem;
                font-weight: 600;
                color: #0f172a;
              }
              .record-preview {
                color: #475569;
                font-size: 0.94rem;
                line-height: 1.6;
                white-space: pre-wrap;
              }
              .count-badge,
              .confidence-badge,
              .mini-badge,
              .meta-badge {
                border: 1px solid rgba(148, 163, 184, 0.18);
              }
              .count-badge {
                background: rgba(255, 255, 255, 0.72) !important;
                color: #334155 !important;
              }
              .confidence-badge {
                background: rgba(15, 23, 42, 0.08) !important;
                color: #0f172a !important;
              }
              .mini-badge {
                background: rgba(15, 118, 110, 0.08) !important;
                color: #0f766e !important;
              }
              .meta-badge {
                background: rgba(59, 130, 246, 0.08) !important;
                color: #1d4ed8 !important;
              }
              .source-expansion {
                background: rgba(248, 250, 252, 0.72);
                border-radius: 18px;
                border: 1px solid rgba(226, 232, 240, 0.88);
                padding: 4px 10px;
              }
            </style>
            """
        )

        ui.add_head_html(
            """
            <script>
              function isEditableTarget(target) {
                if (!target) return false;
                if (target.isContentEditable) return true;
                const tagName = (target.tagName || '').toLowerCase();
                if (['input', 'textarea', 'select'].includes(tagName)) return true;
                return !!target.closest('.q-field__native, .q-field__input, .q-menu, .q-dialog');
              }

              window.addEventListener('keydown', function(event) {
                if (isEditableTarget(event.target)) {
                  return;
                }

                const keyToButton = {
                  ArrowLeft: 'prev-record-button',
                  ArrowRight: 'next-record-button',
                  ArrowUp: 'prev-section-button',
                  ArrowDown: 'next-section-button',
                };
                const buttonId = keyToButton[event.key];
                if (buttonId) {
                  event.preventDefault();
                  const button = document.getElementById(buttonId);
                  if (button) button.click();
                  return;
                }

                if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
                  event.preventDefault();
                  const saveButton = document.getElementById('save-stage3-button');
                  if (saveButton) saveButton.click();
                }
              });
            </script>
            """
        )

    def build_ui(self) -> None:
        self._inject_theme()
        ui.page_title("Stage3 数据集编辑器")

        with ui.header().classes("glass-card app-shell items-center justify-between px-5 py-4 mt-4"):
            with ui.row().classes("items-center gap-4"):
                with ui.column().classes("gap-0"):
                    ui.label("Stage3 数据集编辑器").classes("text-2xl font-bold text-slate-800")
                    self.file_path_label = ui.label(str(self.input_path)).classes("text-sm text-slate-500 break-all")
                self.progress_label = ui.label().classes("text-sm text-slate-600")
            with ui.row().classes("items-center gap-2"):
                self.status_badge = ui.badge().classes("px-3 py-2 text-sm font-medium")
                ui.button("上一栏", on_click=self.go_prev_section).props(
                    "outline id=prev-section-button"
                ).classes("rounded-full")
                ui.button("下一栏", on_click=self.go_next_section).props(
                    "outline id=next-section-button"
                ).classes("rounded-full")
                ui.button("上一条", on_click=self.go_prev_record).props(
                    "outline id=prev-record-button"
                ).classes("rounded-full")
                ui.button("下一条", on_click=self.go_next_record).props(
                    "outline id=next-record-button"
                ).classes("rounded-full")
                self.undo_delete_button = ui.button("撤销删除", on_click=self.undo_delete).props(
                    "outline"
                )
                self.undo_delete_button.classes("rounded-full")
                self.delete_button = ui.button("删除当前", on_click=self.open_delete_dialog).props(
                    "outline color=negative"
                )
                self.delete_button.classes("rounded-full")
                save_button = ui.button("保存", on_click=self.save).props(
                    "unelevated id=save-stage3-button"
                )
                save_button.classes("rounded-full bg-slate-900 text-white px-5")

        with ui.row().classes("app-shell w-full items-start gap-5 px-2 py-6").style("flex-wrap: wrap;"):
            with ui.column().classes("gap-5").style("flex: 0 0 420px; max-width: 420px; min-width: 320px;"):
                with ui.card().classes("glass-card w-full gap-3 p-4"):
                    ui.label("分栏导航").classes("text-lg font-semibold text-slate-800")
                    ui.label("先切换 story_events / character_relations / lore_entries，再在当前分栏中逐条编辑。").classes(
                        "field-label"
                    )
                    with ui.scroll_area().classes("w-full").style("height: 300px;"):
                        self.section_column = ui.column().classes("w-full gap-3")

                with ui.card().classes("glass-card w-full gap-3 p-4"):
                    ui.label("当前分栏记录").classes("text-lg font-semibold text-slate-800")
                    ui.label("列表保持紧凑，重点看标题、point_id、预览和关键标签。").classes("field-label")
                    with ui.scroll_area().classes("w-full").style("height: 760px;"):
                        self.record_column = ui.column().classes("w-full gap-3")

            with ui.column().classes("gap-5").style("flex: 1 1 840px; min-width: 380px;"):
                with ui.card().classes("glass-card w-full gap-4 p-5"):
                    self.metadata_column = ui.column().classes("w-full gap-3")

                with ui.card().classes("glass-card w-full gap-4 p-5"):
                    self.record_editor_column = ui.column().classes("w-full gap-5")

        with ui.footer().classes("app-shell glass-card items-center justify-between px-5 py-3 mb-4"):
            self.footer_hint = ui.label().classes("text-sm text-slate-600")
            ui.label("方向键：上/下切换分栏，左/右切换记录；Cmd/Ctrl + S 保存").classes(
                "text-sm text-slate-500"
            )

        self.refresh_ui()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Stage3 数据集 NiceGUI 编辑器")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="待编辑的 stage3 RAG JSON 路径",
    )
    parser.add_argument("--host", default="127.0.0.1", help="NiceGUI 绑定 host")
    parser.add_argument("--port", type=int, default=8187, help="NiceGUI 端口")
    parser.add_argument(
        "--native",
        action="store_true",
        help="使用 NiceGUI native 模式启动桌面窗口",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    editor = Stage3DatasetEditor(Path(args.input))
    editor.build_ui()
    ui.run(
        host=args.host,
        port=args.port,
        native=args.native,
        reload=False,
        title="Stage3 数据集编辑器",
        favicon="🗂️",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
