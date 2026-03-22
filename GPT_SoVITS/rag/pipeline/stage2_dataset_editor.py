"""NiceGUI 驱动的 Stage2 数据集编辑器。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
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

from rag.pipeline.schemas import Stage2InputArtifact

DEFAULT_INPUT_PATH = PIPELINE_ROOT / "data" / "annotations_stage2" / "ep01_stage2_input.json"


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


class Stage2DatasetEditor:
    """Stage2 utterance 级别标注编辑器。"""

    def __init__(self, input_path: Path) -> None:
        self.annotations_dir = DEFAULT_INPUT_PATH.parent.resolve()
        self.available_file_options: dict[str, str] = {}
        self.input_path = input_path.resolve()
        self.backup_path = self.input_path.with_suffix(f"{self.input_path.suffix}.bak")
        self.data: dict[str, Any] = {}
        self.scenes: list[dict[str, Any]] = []
        self.flat_index_map: list[tuple[int, int]] = []

        self.current_scene_index = 0
        self.current_utterance_index = 0
        self.dirty = False
        self.last_saved_at: str | None = None
        self._syncing_form = False
        self.last_split_snapshot: dict[str, Any] | None = None

        self.scene_column: ui.column | None = None
        self.utterance_column: ui.column | None = None
        self.scene_meta_container: ui.column | None = None
        self.context_container: ui.column | None = None

        self.progress_label: ui.label | None = None
        self.status_badge: ui.badge | None = None
        self.footer_hint: ui.label | None = None
        self.save_button: ui.button | None = None
        self.file_path_label: ui.label | None = None
        self.file_select: ui.select | None = None
        self.load_file_button: ui.button | None = None

        self.speaker_select: ui.select | None = None
        self.addressee_select: ui.select | None = None
        self.mentioned_select: ui.select | None = None
        self.emotion_select: ui.select | None = None
        self.split_before_button: ui.button | None = None
        self.split_after_button: ui.button | None = None
        self.undo_split_button: ui.button | None = None

        self.utterance_meta_label: ui.label | None = None
        self.zh_label: ui.label | None = None
        self.jp_label: ui.label | None = None

        self.character_pool: list[str] = []
        self.emotion_pool: list[str] = []
        self.refresh_available_file_options()
        self._load_dataset(self.input_path)

    @staticmethod
    def _load_file(path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        Stage2InputArtifact.model_validate(raw)
        return raw

    def refresh_available_file_options(self) -> None:
        json_paths = sorted(self.annotations_dir.glob("*.json"))
        if self.input_path.exists() and self.input_path.parent == self.annotations_dir:
            if self.input_path not in json_paths:
                json_paths.append(self.input_path)
                json_paths.sort()
        self.available_file_options = {
            str(path.resolve()): path.relative_to(self.annotations_dir).as_posix()
            for path in json_paths
        }

    def _load_dataset(self, path: Path) -> None:
        self.input_path = path.resolve()
        self.backup_path = self.input_path.with_suffix(f"{self.input_path.suffix}.bak")
        self.data = self._load_file(self.input_path)
        self.scenes = self.data["scenes"]
        self.rebuild_flat_index_map()
        if not self.flat_index_map:
            raise ValueError("输入文件中没有可编辑的 utterances。")

        self.current_scene_index = 0
        self.current_utterance_index = 0
        self.dirty = False
        self.last_saved_at = None
        self.last_split_snapshot = None
        self.character_pool = self._build_character_pool()
        self.emotion_pool = self._build_emotion_pool()

    def _sync_file_selector(self) -> None:
        if self.file_select is not None:
            self.refresh_available_file_options()
            self.file_select.options = self.available_file_options
            self.file_select.set_value(str(self.input_path))
            self.file_select.update()
        if self.file_path_label is not None:
            self.file_path_label.text = str(self.input_path)

    def _apply_loaded_dataset(self, path: Path) -> None:
        self._load_dataset(path)
        self._sync_file_selector()
        self.refresh_ui()
        ui.notify(f"已加载 {self.input_path.name}", color="positive", position="top")

    def load_selected_file(self) -> None:
        if self.file_select is None or not self.file_select.value:
            ui.notify("请先选择一个 JSON 文件。", color="warning", position="top")
            return

        target_path = Path(str(self.file_select.value)).resolve()
        if target_path == self.input_path:
            ui.notify("当前已经打开这个文件。", color="warning", position="top")
            return
        if not target_path.exists():
            ui.notify("所选文件不存在。", color="negative", position="top")
            self.refresh_available_file_options()
            self._sync_file_selector()
            return

        def confirm_load() -> None:
            self._apply_loaded_dataset(target_path)

        if not self.dirty:
            confirm_load()
            return

        with ui.dialog() as dialog, ui.card().classes("w-[540px] max-w-full gap-4 p-5"):
            ui.label("切换数据文件").classes("text-xl font-bold text-slate-800")
            ui.label(
                "当前有未保存修改，切换文件会丢失这些尚未写回磁盘的内容。"
            ).classes("text-sm text-slate-600")
            ui.label(
                f"目标文件：{target_path.relative_to(self.annotations_dir).as_posix()}"
            ).classes("text-sm text-slate-500")
            with ui.row().classes("w-full justify-end gap-2 pt-2"):
                ui.button("取消", on_click=lambda: (dialog.close(), self._sync_file_selector())).props("flat")
                ui.button(
                    "仍然切换",
                    on_click=lambda: (dialog.close(), confirm_load()),
                ).props("unelevated color=warning")
        dialog.open()

    def _build_character_pool(self) -> list[str]:
        speakers = [
            utterance.get("speaker_name", "")
            for scene in self.scenes
            for utterance in scene["utterances"]
        ]
        addressees = [
            name
            for scene in self.scenes
            for utterance in scene["utterances"]
            for name in utterance.get("addressee_candidates", [])
        ]
        mentions = [
            name
            for scene in self.scenes
            for utterance in scene["utterances"]
            for name in utterance.get("mentioned_characters", [])
        ]
        present = [
            name
            for scene in self.scenes
            for name in scene.get("present_characters", [])
        ]
        return ordered_union(dedupe_texts(present), dedupe_texts(speakers), dedupe_texts(addressees), dedupe_texts(mentions))

    def _build_emotion_pool(self) -> list[str]:
        emotions = [
            utterance.get("emotion_hint", "")
            for scene in self.scenes
            for utterance in scene["utterances"]
        ]
        return dedupe_texts(emotions)

    def current_scene(self) -> dict[str, Any]:
        return self.scenes[self.current_scene_index]

    def current_utterance(self) -> dict[str, Any]:
        return self.current_scene()["utterances"][self.current_utterance_index]

    def rebuild_flat_index_map(self) -> None:
        self.flat_index_map = [
            (scene_index, utterance_index)
            for scene_index, scene in enumerate(self.scenes)
            for utterance_index, _ in enumerate(scene["utterances"])
        ]

    def current_global_index(self) -> int:
        return self.flat_index_map.index((self.current_scene_index, self.current_utterance_index))

    def total_utterances(self) -> int:
        return len(self.flat_index_map)

    def set_current(self, scene_index: int, utterance_index: int) -> None:
        self.current_scene_index = max(0, min(scene_index, len(self.scenes) - 1))
        utterances = self.scenes[self.current_scene_index]["utterances"]
        self.current_utterance_index = max(0, min(utterance_index, len(utterances) - 1))
        self.refresh_ui()

    def go_prev(self) -> None:
        current = self.current_global_index()
        self.set_current(*self.flat_index_map[max(0, current - 1)])

    def go_next(self) -> None:
        current = self.current_global_index()
        self.set_current(*self.flat_index_map[min(len(self.flat_index_map) - 1, current + 1)])

    def jump_to_scene(self, scene_index: int) -> None:
        current_utt = self.current_utterance_index if scene_index == self.current_scene_index else 0
        self.set_current(scene_index, current_utt)

    def _split_index(self, before_current: bool) -> int | None:
        utterance_count = len(self.current_scene()["utterances"])
        split_index = self.current_utterance_index if before_current else self.current_utterance_index + 1
        if split_index <= 0 or split_index >= utterance_count:
            return None
        return split_index

    def mark_dirty(self) -> None:
        self.dirty = True
        self.refresh_status()

    @staticmethod
    def _ms_to_text(milliseconds: int) -> str:
        total_seconds, remainder_ms = divmod(milliseconds, 1000)
        hours, remainder_seconds = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder_seconds, 60)
        centiseconds = remainder_ms // 10
        return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

    @staticmethod
    def _describe_character_set(scene: dict[str, Any]) -> str:
        names = scene.get("present_characters", [])
        return "、".join(names) if names else "无"

    @staticmethod
    def _scene_span(scene: dict[str, Any]) -> str:
        return f"{scene['scene_start_text']} - {scene['scene_end_text']}"

    def _derive_present_characters(self, scene: dict[str, Any]) -> list[str]:
        speakers = [
            utterance.get("speaker_name", "")
            for utterance in scene.get("utterances", [])
        ]
        addressees = [
            candidate
            for utterance in scene.get("utterances", [])
            for candidate in utterance.get("addressee_candidates", [])
        ]
        return ordered_union(dedupe_texts(speakers), dedupe_texts(addressees))

    def _recompute_scene_fields(self, scene: dict[str, Any]) -> None:
        time_sources: list[tuple[int, int]] = [
            (utterance["start_ms"], utterance["end_ms"])
            for utterance in scene.get("utterances", [])
        ]
        time_sources.extend(
            (screen_text["start_ms"], screen_text["end_ms"])
            for screen_text in scene.get("screen_texts", [])
        )

        if not time_sources:
            raise ValueError("场景中没有可用于计算时间范围的元素。")

        scene["start_ms"] = min(start_ms for start_ms, _ in time_sources)
        scene["end_ms"] = max(end_ms for _, end_ms in time_sources)
        scene["scene_start_text"] = self._ms_to_text(scene["start_ms"])
        scene["scene_end_text"] = self._ms_to_text(scene["end_ms"])
        scene["scene_summary_hint"] = None
        scene["present_characters"] = self._derive_present_characters(scene)

    def _scene_id_format(self) -> tuple[str, int]:
        for scene in self.scenes:
            match = re.match(r"^(.*_s)(\d+)$", str(scene.get("scene_id", "")))
            if match:
                return match.group(1), len(match.group(2))
        episode = self.current_scene().get("episode", 1)
        return f"ep{int(episode):02d}_s", 3

    def _renumber_scene_ids(self) -> None:
        prefix, width = self._scene_id_format()
        for index, scene in enumerate(self.scenes, start=1):
            scene["scene_id"] = f"{prefix}{index:0{width}d}"

    def _capture_split_snapshot(self) -> None:
        self.last_split_snapshot = {
            "data": copy.deepcopy(self.data),
            "scene_index": self.current_scene_index,
            "utterance_index": self.current_utterance_index,
            "dirty": self.dirty,
            "last_saved_at": self.last_saved_at,
        }

    def undo_last_split(self) -> None:
        if self.last_split_snapshot is None:
            ui.notify("当前没有可撤销的切分。", color="warning", position="top")
            return

        snapshot = self.last_split_snapshot
        self.data = copy.deepcopy(snapshot["data"])
        self.scenes = self.data["scenes"]
        self.rebuild_flat_index_map()
        self.current_scene_index = min(snapshot["scene_index"], len(self.scenes) - 1)
        self.current_utterance_index = min(
            snapshot["utterance_index"],
            len(self.current_scene()["utterances"]) - 1,
        )
        self.dirty = snapshot["dirty"]
        self.last_saved_at = snapshot["last_saved_at"]
        self.last_split_snapshot = None
        self.character_pool = self._build_character_pool()
        self.emotion_pool = self._build_emotion_pool()
        self.refresh_ui()
        ui.notify("已撤销上一次切分。", color="positive", position="top")

    def _build_split_preview(self, split_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
        scene = self.current_scene()
        left_scene = copy.deepcopy(scene)
        right_scene = copy.deepcopy(scene)

        left_scene["utterances"] = copy.deepcopy(scene["utterances"][:split_index])
        right_scene["utterances"] = copy.deepcopy(scene["utterances"][split_index:])
        if not left_scene["utterances"] or not right_scene["utterances"]:
            raise ValueError("切分后有一侧场景为空，无法执行。")

        boundary_ms = (
            left_scene["utterances"][-1]["end_ms"] + right_scene["utterances"][0]["start_ms"]
        ) // 2

        left_screen_texts: list[dict[str, Any]] = []
        right_screen_texts: list[dict[str, Any]] = []
        for screen_text in scene.get("screen_texts", []):
            center_ms = (screen_text["start_ms"] + screen_text["end_ms"]) // 2
            if center_ms <= boundary_ms:
                left_screen_texts.append(copy.deepcopy(screen_text))
            else:
                right_screen_texts.append(copy.deepcopy(screen_text))

        left_scene["screen_texts"] = left_screen_texts
        right_scene["screen_texts"] = right_screen_texts
        self._recompute_scene_fields(left_scene)
        self._recompute_scene_fields(right_scene)
        return left_scene, right_scene

    def _apply_split(self, before_current: bool) -> None:
        split_index = self._split_index(before_current)
        if split_index is None:
            ui.notify("这个位置不能再切分了。", color="warning", position="top")
            return

        left_scene, right_scene = self._build_split_preview(split_index)
        self._capture_split_snapshot()
        scene_insert_index = self.current_scene_index
        self.scenes[scene_insert_index:scene_insert_index + 1] = [left_scene, right_scene]
        self._renumber_scene_ids()
        self.rebuild_flat_index_map()
        self.current_scene_index = min(scene_insert_index + 1, len(self.scenes) - 1)
        self.current_utterance_index = 0
        self.character_pool = self._build_character_pool()
        self.emotion_pool = self._build_emotion_pool()
        self.mark_dirty()
        self.refresh_ui()
        ui.notify("场景已切分，记得保存 JSON。", color="positive", position="top")

    def open_split_dialog(self, before_current: bool) -> None:
        split_index = self._split_index(before_current)
        if split_index is None:
            ui.notify("这个位置不能再切分了。", color="warning", position="top")
            return

        left_scene, right_scene = self._build_split_preview(split_index)
        current_utterance = self.current_utterance()
        direction_label = "在本句前切分" if before_current else "在本句后切分"

        with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-full gap-4 p-5"):
            ui.label("确认切分场景").classes("text-xl font-bold text-slate-800")
            ui.label(
                f"{direction_label} · 当前定位 {self.current_scene()['scene_id']} / {current_utterance['u_id']}"
            ).classes("text-sm text-slate-500")

            with ui.row().classes("w-full gap-4").style("flex-wrap: wrap;"):
                for title, scene in (("前半场景", left_scene), ("后半场景", right_scene)):
                    with ui.column().classes("readonly-block gap-2").style("flex: 1 1 300px;"):
                        ui.label(title).classes("text-base font-semibold text-slate-800")
                        ui.label(
                            f"{len(scene['utterances'])} 句台词 · {len(scene.get('screen_texts', []))} 条屏幕字"
                        ).classes("text-sm text-slate-500")
                        ui.label(self._scene_span(scene)).classes("text-sm text-slate-700")
                        ui.label(
                            f"在场角色：{self._describe_character_set(scene)}"
                        ).classes("text-sm text-slate-700 whitespace-pre-wrap")
                        ui.label(
                            f"summary 将被清空，保存后可继续补写。"
                        ).classes("text-sm text-slate-500")

            ui.label(
                "撤销说明：支持撤销上一次切分，但会一起回退切分之后尚未保存的结构状态。"
            ).classes("text-sm text-amber-700")

            with ui.row().classes("w-full justify-end gap-2 pt-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认切分",
                    on_click=lambda: (dialog.close(), self._apply_split(before_current)),
                ).props("unelevated color=primary")

        dialog.open()

    def refresh_status(self) -> None:
        current = self.current_global_index() + 1
        scene = self.current_scene()
        progress = (
            f"Scene {self.current_scene_index + 1}/{len(self.scenes)}"
            f" · Utterance {self.current_utterance_index + 1}/{len(scene['utterances'])}"
            f" · Global {current}/{self.total_utterances()}"
        )
        if self.progress_label is not None:
            self.progress_label.text = progress

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
                self.footer_hint.text = "修改仅保存在浏览器会话内，点击右上角保存后才会写回 JSON。"
            else:
                self.footer_hint.text = "当前文件与磁盘内容一致。可以随时切换 utterance 继续标注。"

    def refresh_split_controls(self) -> None:
        before_enabled = self._split_index(before_current=True) is not None
        after_enabled = self._split_index(before_current=False) is not None

        if self.split_before_button is not None:
            if before_enabled:
                self.split_before_button.enable()
            else:
                self.split_before_button.disable()
        if self.split_after_button is not None:
            if after_enabled:
                self.split_after_button.enable()
            else:
                self.split_after_button.disable()
        if self.undo_split_button is not None:
            if self.last_split_snapshot is not None:
                self.undo_split_button.enable()
            else:
                self.undo_split_button.disable()

    def save(self) -> None:
        Stage2InputArtifact.model_validate(self.data)
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
        self.last_saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.character_pool = self._build_character_pool()
        self.emotion_pool = self._build_emotion_pool()
        self.refresh_ui()
        ui.notify(f"已保存到 {self.input_path.name}", color="positive", position="top")

    def _speaker_options(self) -> list[str]:
        scene = self.current_scene()
        utterance = self.current_utterance()
        return ordered_union(
            dedupe_texts(scene.get("present_characters", [])),
            dedupe_texts([utterance.get("speaker_name", "")]),
            self.character_pool,
        )

    def _list_options(self, field_name: str) -> list[str]:
        scene = self.current_scene()
        utterance = self.current_utterance()
        return ordered_union(
            dedupe_texts(scene.get("present_characters", [])),
            dedupe_texts(utterance.get(field_name, [])),
            self.character_pool,
        )

    def _emotion_options(self) -> list[str]:
        utterance = self.current_utterance()
        return ordered_union(
            dedupe_texts([utterance.get("emotion_hint", "")]),
            self.emotion_pool,
        )

    def _update_string_field(self, field_name: str, value: Any) -> None:
        if self._syncing_form:
            return
        text = str(value or "").strip() or None
        utterance = self.current_utterance()
        if utterance.get(field_name) == text:
            return
        utterance[field_name] = text
        self.character_pool = self._build_character_pool()
        self.emotion_pool = self._build_emotion_pool()
        self.mark_dirty()

    def _update_list_field(self, field_name: str, value: Any) -> None:
        if self._syncing_form:
            return
        if value is None:
            normalized: list[str] = []
        elif isinstance(value, (list, tuple)):
            normalized = dedupe_texts(list(value))
        else:
            normalized = dedupe_texts([value])

        utterance = self.current_utterance()
        if utterance.get(field_name, []) == normalized:
            return
        utterance[field_name] = normalized
        self.character_pool = self._build_character_pool()
        self.mark_dirty()

    def refresh_scene_list(self) -> None:
        if self.scene_column is None:
            return
        self.scene_column.clear()
        with self.scene_column:
            for scene_index, scene in enumerate(self.scenes):
                is_active = scene_index == self.current_scene_index
                card = ui.card().classes("scene-card w-full gap-1")
                if is_active:
                    card.classes(add="active")
                with card:
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(scene["scene_id"]).classes("text-sm font-semibold")
                        ui.badge(f"{len(scene['utterances'])} 句").classes("bg-white/70 text-slate-700")
                    ui.label(
                        f"{scene['scene_start_text']} - {scene['scene_end_text']}"
                    ).classes("text-xs opacity-80")
                    preview = "、".join(scene.get("present_characters", [])[:4]) or "暂无角色"
                    ui.label(preview).classes("text-xs opacity-80")
                card.on(
                    "click",
                    lambda _=None, target_scene=scene_index: self.jump_to_scene(target_scene),
                )

    def refresh_utterance_list(self) -> None:
        if self.utterance_column is None:
            return
        self.utterance_column.clear()
        scene = self.current_scene()
        with self.utterance_column:
            for utterance_index, utterance in enumerate(scene["utterances"]):
                is_active = utterance_index == self.current_utterance_index
                card = ui.card().classes("utterance-card w-full gap-2")
                if is_active:
                    card.classes(add="active")
                with card:
                    with ui.row().classes("w-full items-center justify-between gap-2"):
                        ui.label(f"{utterance_index + 1:02d} · {utterance['u_id']}").classes(
                            "text-sm font-semibold"
                        )
                        speaker_name = utterance.get("speaker_name") or "未标注"
                        ui.badge(speaker_name).classes("bg-slate-100 text-slate-700")
                    ui.label(
                        f"{utterance['start_text']} - {utterance['end_text']}"
                    ).classes("text-xs opacity-70")
                    ui.label(utterance.get("zh_text", "") or " ").classes("utterance-preview")
                card.on(
                    "click",
                    lambda _=None, target_utt=utterance_index: self.set_current(
                        self.current_scene_index,
                        target_utt,
                    ),
                )

    def refresh_scene_meta(self) -> None:
        if self.scene_meta_container is None:
            return
        self.scene_meta_container.clear()
        scene = self.current_scene()
        with self.scene_meta_container:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(scene["scene_id"]).classes("text-lg font-bold text-slate-800")
                ui.label(
                    f"{scene['scene_start_text']} - {scene['scene_end_text']}"
                ).classes("text-sm text-slate-500")
            if scene.get("scene_summary_hint"):
                ui.label(scene["scene_summary_hint"]).classes("text-sm leading-6 text-slate-600")
            with ui.row().classes("w-full flex-wrap gap-2 pt-2"):
                for name in scene.get("present_characters", []):
                    ui.badge(name).classes("context-badge")
                if not scene.get("present_characters"):
                    ui.badge("暂无角色表").classes("context-badge")

    def refresh_context(self) -> None:
        if self.context_container is None:
            return
        self.context_container.clear()

        scene = self.current_scene()
        utterances = scene["utterances"]
        current = self.current_utterance_index
        context_items = [
            ("上一句", utterances[current - 1]) if current > 0 else None,
            ("当前句", utterances[current]),
            ("下一句", utterances[current + 1]) if current < len(utterances) - 1 else None,
        ]

        with self.context_container:
            for item in context_items:
                if item is None:
                    continue
                title, utterance = item
                block = ui.column().classes("readonly-block w-full gap-1")
                if title == "当前句":
                    block.classes(add="current-context")
                with block:
                    with ui.row().classes("w-full items-center justify-between gap-2"):
                        ui.label(title).classes("text-sm font-semibold text-slate-700")
                        ui.label(utterance["u_id"]).classes("text-xs text-slate-500")
                    ui.label(
                        utterance.get("zh_text", "") or " "
                    ).classes("text-base font-semibold text-slate-800 whitespace-pre-wrap")
                    ui.label(
                        utterance.get("jp_text", "") or " "
                    ).classes("text-sm text-slate-500 whitespace-pre-wrap")

    def refresh_form(self) -> None:
        utterance = self.current_utterance()
        scene = self.current_scene()
        self._syncing_form = True
        try:
            if self.utterance_meta_label is not None:
                self.utterance_meta_label.text = (
                    f"{utterance['u_id']} · {utterance['start_text']} - {utterance['end_text']} · "
                    f"{scene['scene_id']}"
                )
            if self.zh_label is not None:
                self.zh_label.text = utterance.get("zh_text", "")
            if self.jp_label is not None:
                self.jp_label.text = utterance.get("jp_text", "")

            if self.speaker_select is not None:
                self.speaker_select.options = self._speaker_options()
                self.speaker_select.set_value(utterance.get("speaker_name"))
                self.speaker_select.update()

            if self.addressee_select is not None:
                self.addressee_select.options = self._list_options("addressee_candidates")
                self.addressee_select.set_value(utterance.get("addressee_candidates", []))
                self.addressee_select.update()

            if self.mentioned_select is not None:
                self.mentioned_select.options = self._list_options("mentioned_characters")
                self.mentioned_select.set_value(utterance.get("mentioned_characters", []))
                self.mentioned_select.update()

            if self.emotion_select is not None:
                self.emotion_select.options = self._emotion_options()
                self.emotion_select.set_value(utterance.get("emotion_hint"))
                self.emotion_select.update()
        finally:
            self._syncing_form = False

    def refresh_ui(self) -> None:
        self.refresh_status()
        self.refresh_split_controls()
        self.refresh_scene_list()
        self.refresh_utterance_list()
        self.refresh_scene_meta()
        self.refresh_form()
        self.refresh_context()

    def _inject_theme(self) -> None:
        ui.add_head_html(
            """
            <style>
              body {
                background:
                  radial-gradient(circle at top left, rgba(251, 191, 36, 0.18), transparent 32%),
                  radial-gradient(circle at top right, rgba(14, 165, 233, 0.20), transparent 26%),
                  linear-gradient(180deg, #fff9f2 0%, #f5f7fb 46%, #eef4fb 100%);
                font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
                color: #233142;
              }
              .app-shell {
                max-width: 1680px;
                margin: 0 auto;
                width: 100%;
              }
              .glass-card {
                background: rgba(255, 255, 255, 0.78);
                backdrop-filter: blur(18px);
                border: 1px solid rgba(255, 255, 255, 0.68);
                border-radius: 24px;
                box-shadow: 0 24px 60px rgba(50, 70, 93, 0.12);
              }
              .scene-card,
              .utterance-card {
                cursor: pointer;
                border-radius: 18px;
                border: 1px solid transparent;
                background: rgba(248, 250, 252, 0.78);
                transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
              }
              .scene-card:hover,
              .utterance-card:hover {
                transform: translateY(-1px);
                box-shadow: 0 12px 24px rgba(15, 23, 42, 0.08);
              }
              .scene-card.active {
                background: linear-gradient(135deg, #1d3557 0%, #457b9d 100%);
                color: white;
              }
              .scene-card.active .q-badge {
                background: rgba(255, 255, 255, 0.20) !important;
                color: white !important;
              }
              .utterance-card.active {
                border-color: rgba(231, 111, 81, 0.48);
                background: linear-gradient(135deg, rgba(255, 236, 229, 0.92), rgba(255, 246, 241, 0.98));
                box-shadow: 0 16px 28px rgba(231, 111, 81, 0.14);
              }
              .utterance-preview {
                font-size: 0.95rem;
                line-height: 1.55;
                color: #334155;
                white-space: pre-wrap;
              }
              .readonly-block {
                background: rgba(255, 255, 255, 0.92);
                border-radius: 18px;
                border: 1px solid rgba(226, 232, 240, 0.95);
                padding: 14px 16px;
              }
              .current-context {
                border-color: rgba(59, 130, 246, 0.26);
                background: linear-gradient(135deg, rgba(239, 246, 255, 0.95), rgba(255, 255, 255, 0.98));
              }
              .context-badge {
                background: rgba(15, 118, 110, 0.10) !important;
                color: #0f766e !important;
                border: 1px solid rgba(15, 118, 110, 0.12);
              }
              .text-panel {
                background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.95));
                border-radius: 22px;
                border: 1px solid rgba(226, 232, 240, 0.95);
                padding: 18px 20px;
              }
              .text-panel-main {
                font-size: 1.28rem;
                line-height: 1.7;
                color: #0f172a;
                font-weight: 700;
                white-space: pre-wrap;
              }
              .text-panel-sub {
                font-size: 0.98rem;
                line-height: 1.65;
                color: #475569;
                white-space: pre-wrap;
              }
              .editor-label {
                font-size: 0.92rem;
                color: #64748b;
                letter-spacing: 0.02em;
              }
            </style>
            """
        )

    def build_ui(self) -> None:
        self._inject_theme()
        ui.page_title("Stage2 数据集编辑器")

        with ui.header().classes("glass-card app-shell items-center justify-between px-5 py-4 mt-4"):
            with ui.row().classes("items-center gap-4"):
                with ui.column().classes("gap-0"):
                    ui.label("Stage2 数据集编辑器").classes("text-2xl font-bold text-slate-800")
                    self.file_path_label = ui.label(str(self.input_path)).classes("text-sm text-slate-500")
                self.progress_label = ui.label().classes("text-sm text-slate-600")
            with ui.row().classes("items-center gap-2"):
                self.file_select = ui.select(
                    options=self.available_file_options,
                    value=str(self.input_path),
                    label="选择 JSON 文件",
                ).classes("min-w-[280px]")
                self.file_select.props("dense standout options-dense")
                self.load_file_button = ui.button("加载", on_click=self.load_selected_file).props("outline")
                self.status_badge = ui.badge().classes("px-3 py-2 text-sm font-medium")
                ui.button("上一条", on_click=self.go_prev).props("outline id=prev-utterance-button").classes("rounded-full")
                ui.button("下一条", on_click=self.go_next).props("outline id=next-utterance-button").classes("rounded-full")
                self.save_button = ui.button("保存", on_click=self.save).props("unelevated id=save-stage2-button")
                self.save_button.classes("rounded-full bg-slate-900 text-white px-5")

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

                if (['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(event.key)) {
                  event.preventDefault();
                  const buttonId = ['ArrowLeft', 'ArrowUp'].includes(event.key)
                    ? 'prev-utterance-button'
                    : 'next-utterance-button';
                  const navButton = document.getElementById(buttonId);
                  if (navButton) navButton.click();
                  return;
                }

                if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
                  event.preventDefault();
                  const saveButton = document.getElementById('save-stage2-button');
                  if (saveButton) saveButton.click();
                }
              });
            </script>
            """
        )

        with ui.row().classes("app-shell w-full items-start gap-5 px-2 py-6").style("flex-wrap: wrap;"):
            with ui.column().classes("gap-5").style("flex: 0 0 360px; max-width: 360px; min-width: 300px;"):
                with ui.card().classes("glass-card w-full gap-3 p-4"):
                    ui.label("场景导航").classes("text-lg font-semibold text-slate-800")
                    ui.label("按 scene 切换，再在当前 scene 内选择 utterance。").classes("editor-label")
                    with ui.scroll_area().classes("w-full").style("height: 280px;"):
                        self.scene_column = ui.column().classes("w-full gap-2")

                with ui.card().classes("glass-card w-full gap-3 p-4"):
                    ui.label("当前 Scene 的 Utterances").classes("text-lg font-semibold text-slate-800")
                    ui.label("左侧列表保持紧凑，只展示当前 scene，右侧负责深度编辑。").classes("editor-label")
                    with ui.scroll_area().classes("w-full").style("height: 540px;"):
                        self.utterance_column = ui.column().classes("w-full gap-2")

            with ui.column().classes("gap-5").style("flex: 1 1 760px; min-width: 360px;"):
                with ui.card().classes("glass-card w-full gap-4 p-5"):
                    self.scene_meta_container = ui.column().classes("w-full gap-3")

                with ui.card().classes("glass-card w-full gap-5 p-5"):
                    ui.label("Utterance 标注").classes("text-lg font-semibold text-slate-800")
                    self.utterance_meta_label = ui.label().classes("text-sm text-slate-500")

                    with ui.row().classes("w-full items-center gap-3").style("flex-wrap: wrap;"):
                        ui.label("结构编辑").classes("editor-label")
                        self.split_before_button = ui.button(
                            "在本句前切分",
                            on_click=lambda: self.open_split_dialog(before_current=True),
                        ).props("outline")
                        self.split_after_button = ui.button(
                            "在本句后切分",
                            on_click=lambda: self.open_split_dialog(before_current=False),
                        ).props("outline")
                        self.undo_split_button = ui.button(
                            "撤销上一次切分",
                            on_click=self.undo_last_split,
                        ).props("flat color=warning")

                    with ui.row().classes("w-full gap-4").style("flex-wrap: wrap;"):
                        self.speaker_select = ui.select(
                            options=[],
                            label="speaker_name",
                            with_input=True,
                            on_change=lambda e: self._update_string_field("speaker_name", e.value),
                        ).classes("w-full").style("flex: 1 1 280px;")
                        self.speaker_select.props(
                            "clearable standout dense use-input fill-input hide-selected "
                            "input-debounce=0 new-value-mode=add-unique"
                        )

                        self.emotion_select = ui.select(
                            options=[],
                            label="emotion_hint",
                            with_input=True,
                            on_change=lambda e: self._update_string_field("emotion_hint", e.value),
                        ).classes("w-full").style("flex: 1 1 280px;")
                        self.emotion_select.props(
                            "clearable standout dense use-input fill-input hide-selected "
                            "input-debounce=0 new-value-mode=add-unique"
                        )

                        self.addressee_select = ui.select(
                            options=[],
                            value=[],
                            label="addressee_candidates",
                            multiple=True,
                            with_input=True,
                            on_change=lambda e: self._update_list_field("addressee_candidates", e.value),
                        ).classes("w-full").style("flex: 1 1 280px;")
                        self.addressee_select.props(
                            "use-chips clearable standout dense use-input input-debounce=0 "
                            "new-value-mode=add-unique"
                        )

                        self.mentioned_select = ui.select(
                            options=[],
                            value=[],
                            label="mentioned_characters",
                            multiple=True,
                            with_input=True,
                            on_change=lambda e: self._update_list_field("mentioned_characters", e.value),
                        ).classes("w-full").style("flex: 1 1 280px;")
                        self.mentioned_select.props(
                            "use-chips clearable standout dense use-input input-debounce=0 "
                            "new-value-mode=add-unique"
                        )

                    with ui.column().classes("w-full gap-3"):
                        ui.label("双语文本参考").classes("editor-label")
                        with ui.row().classes("w-full gap-4").style("flex-wrap: wrap;"):
                            with ui.column().classes("text-panel gap-2").style("flex: 1 1 320px;"):
                                ui.label("中文").classes("text-sm font-semibold text-slate-500")
                                self.zh_label = ui.label().classes("text-panel-main")
                            with ui.column().classes("text-panel gap-2").style("flex: 1 1 320px;"):
                                ui.label("日文").classes("text-sm font-semibold text-slate-500")
                                self.jp_label = ui.label().classes("text-panel-sub")

                with ui.card().classes("glass-card w-full gap-4 p-5"):
                    ui.label("临近语境").classes("text-lg font-semibold text-slate-800")
                    self.context_container = ui.column().classes("w-full gap-3")

        with ui.footer().classes("app-shell glass-card items-center justify-between px-5 py-3 mb-4"):
            self.footer_hint = ui.label().classes("text-sm text-slate-600")
            ui.label("提示：支持方向键切换、场景切分，Cmd/Ctrl + S 快速保存").classes("text-sm text-slate-500")

        self.refresh_ui()
        self._sync_file_selector()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Stage2 数据集 NiceGUI 编辑器")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="待编辑的 stage2_input JSON 路径",
    )
    parser.add_argument("--host", default="127.0.0.1", help="NiceGUI 绑定 host")
    parser.add_argument("--port", type=int, default=8186, help="NiceGUI 端口")
    parser.add_argument(
        "--native",
        action="store_true",
        help="使用 NiceGUI native 模式启动桌面窗口",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    editor = Stage2DatasetEditor(Path(args.input))
    editor.build_ui()
    ui.run(
        host=args.host,
        port=args.port,
        native=args.native,
        reload=False,
        title="Stage2 数据集编辑器",
        favicon="📝",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
