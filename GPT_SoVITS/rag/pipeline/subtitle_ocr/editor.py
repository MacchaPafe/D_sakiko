"""提供视频字幕 OCR review JSON 的独立 NiceGUI 复核工作台。"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

import cv2
from nicegui.element import Element
from nicegui import ui
import numpy as np
from numpy.typing import NDArray

from .models import RelativeRegion, SubtitleReviewEvent
from .profiles import region_pixels
from .publisher import default_ass_path, publish_review_ass
from .workspace import OCRReviewWorkspace


MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_FILES = 500


def milliseconds_text(milliseconds: int) -> str:
    """把毫秒格式化为便于审核的时间文本。"""

    total_seconds, remainder_ms = divmod(milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{remainder_ms:03d}"


def _resize_image(image: NDArray[np.uint8], maximum_edge: int = 1280) -> NDArray[np.uint8]:
    """限制编辑器缓存图的最长边。"""

    height, width = image.shape[:2]
    scale = min(1.0, maximum_edge / max(height, width))
    if scale >= 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _crop_region(image: NDArray[np.uint8], region: RelativeRegion) -> NDArray[np.uint8]:
    """按相对矩形裁剪编辑器证据图。"""

    height, width = image.shape[:2]
    left, top, right, bottom = region_pixels(region, width, height)
    return image[top:bottom, left:right]


def _image_data_url(path: Path) -> str:
    """把当前需要展示的 JPEG 转换为浏览器可用的数据 URL。"""

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class OCRSubtitleReviewEditor:
    """提供事件队列、字幕编辑、证据查看和正式发布操作。"""

    def __init__(self, review_path: Path) -> None:
        """加载单集审核工作区并初始化界面状态。"""

        self.workspace = OCRReviewWorkspace.load(review_path)
        self.current_event_id = (
            self.workspace.artifact.events[0].event_id
            if self.workspace.artifact.events
            else None
        )
        self.selected_event_ids: set[str] = set()
        self.event_cards: dict[str, Element] = {}
        self.status_filter = "all"
        self.search_text = ""
        self.cache_root = review_path.parent / (
            f"{review_path.stem.removesuffix('.review')}.review-assets/cache"
        )

    def current_event(self) -> SubtitleReviewEvent | None:
        """返回当前选中的字幕事件。"""

        if self.current_event_id is None:
            return None
        try:
            return self.workspace.event(self.current_event_id)
        except KeyError:
            return None

    def filtered_events(self) -> list[SubtitleReviewEvent]:
        """按状态与搜索文本返回左侧队列事件。"""

        search = self.search_text.strip().lower()
        return [
            event
            for event in self.workspace.artifact.events
            if (self.status_filter == "all" or event.status == self.status_filter)
            and (not search or search in event.text.lower() or search in event.event_id.lower())
        ]

    def available_review_paths(self) -> list[Path]:
        """返回当前目录中可切换的单集 review JSON。"""

        paths = sorted(self.workspace.path.parent.glob("*.review.json"))
        return paths or [self.workspace.path]

    def switch_review(self, path_text: str) -> None:
        """在没有未保存草稿时切换到同目录另一集审核文件。"""

        target = Path(path_text).resolve()
        if target == self.workspace.path:
            return
        if self.workspace.dirty:
            ui.notify("当前集存在未保存草稿，请先保存再切换", type="warning")
            self.header_panel.refresh()
            return
        try:
            self.workspace = OCRReviewWorkspace.load(target)
        except (OSError, ValueError) as exc:
            ui.notify(str(exc), type="negative")
            return
        self.current_event_id = (
            self.workspace.artifact.events[0].event_id
            if self.workspace.artifact.events
            else None
        )
        self.selected_event_ids.clear()
        self.cache_root = target.parent / (
            f"{target.stem.removesuffix('.review')}.review-assets/cache"
        )
        self._refresh_all()

    def select_event(self, event_id: str) -> None:
        """切换当前事件并刷新详情和证据。"""

        previous_event_id = self.current_event_id
        self.current_event_id = event_id
        previous_card = self.event_cards.get(previous_event_id or "")
        if previous_card is not None:
            previous_card.classes(remove="bg-blue-50")
        current_card = self.event_cards.get(event_id)
        if current_card is not None:
            current_card.classes(add="bg-blue-50")
        self.detail_panel.refresh()
        self.evidence_panel.refresh()

    def toggle_selected(self, event_id: str, selected: bool) -> None:
        """切换一个事件的批量选择状态。"""

        if selected:
            self.selected_event_ids.add(event_id)
        else:
            self.selected_event_ids.discard(event_id)

    def _refresh_all(self) -> None:
        """刷新工作台全部动态区域。"""

        self.header_panel.refresh()
        self.queue_panel.refresh()
        self.detail_panel.refresh()
        self.evidence_panel.refresh()

    def save(self) -> None:
        """显式保存当前审核草稿。"""

        try:
            path = self.workspace.save()
        except (OSError, RuntimeError, ValueError) as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(f"已保存: {path.name}", type="positive")
        self._refresh_all()

    def undo(self) -> None:
        """撤销上一编辑操作。"""

        if self.workspace.undo():
            self._refresh_all()

    def redo(self) -> None:
        """重做刚撤销的编辑操作。"""

        if self.workspace.redo():
            self._refresh_all()

    def accept_current(self) -> None:
        """把当前事件标记为人工确认。"""

        event = self.current_event()
        if event is None:
            return
        self.workspace.set_status(event.event_id, "accepted")
        self._refresh_all()

    def delete_current(self) -> None:
        """软删除当前事件。"""

        event = self.current_event()
        if event is None:
            return
        self.workspace.delete_events([event.event_id], "人工删除")
        self._refresh_all()

    def restore_current(self) -> None:
        """恢复当前软删除事件。"""

        event = self.current_event()
        if event is None:
            return
        self.workspace.restore_events([event.event_id])
        self._refresh_all()

    def batch_delete_selected(self) -> None:
        """软删除用户显式勾选的全部事件。"""

        changed = self.workspace.delete_events(
            sorted(self.selected_event_ids),
            "批量删除（例如 OP/ED）",
        )
        self.selected_event_ids.clear()
        ui.notify(f"已软删除 {changed} 条字幕", type="positive")
        self._refresh_all()

    def batch_restore_selected(self) -> None:
        """恢复用户显式勾选的全部软删除事件。"""

        changed = self.workspace.restore_events(sorted(self.selected_event_ids))
        self.selected_event_ids.clear()
        ui.notify(f"已恢复 {changed} 条字幕", type="positive")
        self._refresh_all()

    def delete_time_range(self, start_ms: int, end_ms: int) -> None:
        """按用户明确输入的时间范围批量软删除字幕。"""

        try:
            changed = self.workspace.delete_time_range(
                start_ms,
                end_ms,
                "批量删除时间范围（例如 OP/ED）",
            )
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(f"时间范围内已软删除 {changed} 条字幕", type="positive")
        self._refresh_all()

    def update_current(self, text: str, start_ms: int, end_ms: int) -> None:
        """保存当前事件表单中的正文和时间。"""

        event = self.current_event()
        if event is None:
            return
        try:
            self.workspace.update_event(
                event.event_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify("事件已更新到内存草稿", type="positive")
        self._refresh_all()

    def split_current(self, split_ms: int, second_text: str) -> None:
        """按表单参数拆分当前事件。"""

        event = self.current_event()
        if event is None:
            return
        try:
            _, second_id = self.workspace.split_event(event.event_id, split_ms, second_text)
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
            return
        self.current_event_id = second_id
        self._refresh_all()

    def merge_with_next(self) -> None:
        """把当前事件与时间顺序中的下一条活动字幕合并。"""

        event = self.current_event()
        if event is None:
            return
        active = [
            item
            for item in self.workspace.artifact.events
            if item.status != "deleted" and item.start_ms >= event.start_ms
        ]
        active.sort(key=lambda item: (item.start_ms, item.end_ms))
        try:
            index = next(i for i, item in enumerate(active) if item.event_id == event.event_id)
        except StopIteration:
            return
        if index + 1 >= len(active):
            ui.notify("当前事件之后没有可合并字幕", type="warning")
            return
        next_event = active[index + 1]
        self.workspace.merge_events(
            event.event_id,
            next_event.event_id,
            f"{event.text}\n{next_event.text}",
        )
        self._refresh_all()

    def add_after_current(self) -> None:
        """在当前事件后新增一条待填写的人工字幕。"""

        event = self.current_event()
        start_ms = event.end_ms if event is not None else 0
        added = self.workspace.add_event(start_ms, start_ms + 1000, "请填写字幕")
        self.current_event_id = added.event_id
        self._refresh_all()

    def publish(self) -> None:
        """保存并发布唯一的正式 ASS。"""

        try:
            self.workspace.save()
            ass_path = publish_review_ass(self.workspace.path)
            self.workspace = OCRReviewWorkspace.load(self.workspace.path)
        except (OSError, RuntimeError, ValueError) as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(f"正式 ASS 已发布: {ass_path.name}", type="positive")
        self._refresh_all()

    async def request_publish(self) -> None:
        """在覆盖现有正式 ASS 前要求用户显式确认。"""

        target = default_ass_path(self.workspace.path, self.workspace.artifact)
        if not target.exists():
            self.publish()
            return
        with ui.dialog() as dialog, ui.card():
            ui.label("正式 ASS 已存在")
            ui.label(f"继续会先创建时间戳备份，再替换：{target}")
            with ui.row().classes("justify-end w-full"):
                ui.button("取消", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("确认重新发布", on_click=lambda: dialog.submit(True)).props(
                    "color=warning"
                )
        confirmed = await dialog
        if confirmed:
            self.publish()

    def _cache_current_evidence(
        self,
        timestamp_ms: int,
    ) -> tuple[Path | None, Path | None]:
        """用一次视频解码缓存当前完整帧及其字幕区域。"""

        full_target = self.cache_root / f"frame_{timestamp_ms:010d}_full.jpg"
        crop_target = self.cache_root / f"frame_{timestamp_ms:010d}_crop.jpg"
        if full_target.exists() and crop_target.exists():
            full_target.touch()
            crop_target.touch()
            return full_target, crop_target

        frame = cv2.imread(str(full_target)) if full_target.exists() else None
        if frame is None:
            frame = self._read_video_frame(timestamp_ms)
        if frame is None:
            return (
                full_target if full_target.exists() else None,
                crop_target if crop_target.exists() else None,
            )

        self.cache_root.mkdir(parents=True, exist_ok=True)
        if not full_target.exists():
            self._write_cached_image(full_target, frame)
        if not crop_target.exists():
            crop = _crop_region(frame, self.workspace.artifact.profile.subtitle_band)
            self._write_cached_image(crop_target, crop)
        self._trim_cache()
        return full_target, crop_target

    def _read_video_frame(self, timestamp_ms: int) -> NDArray[np.uint8] | None:
        """打开视频并读取指定时间点的一帧。"""

        capture = cv2.VideoCapture(self.workspace.artifact.video.path)
        if not capture.isOpened():
            return None
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(max(0, timestamp_ms)))
            succeeded, frame = capture.read()
        finally:
            capture.release()
        if not succeeded:
            return None
        return frame

    @staticmethod
    def _write_cached_image(target: Path, image: NDArray[np.uint8]) -> None:
        """把缩放后的证据图写入 JPEG 缓存。"""

        cv2.imwrite(
            str(target),
            _resize_image(image),
            [int(cv2.IMWRITE_JPEG_QUALITY), 80],
        )

    def _trim_cache(self) -> None:
        """按文件数量和总字节限制淘汰最久未使用截图。"""

        files = sorted(
            self.cache_root.glob("*.jpg"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        total_bytes = sum(path.stat().st_size for path in files)
        while len(files) > MAX_CACHE_FILES or total_bytes > MAX_CACHE_BYTES:
            oldest = files.pop(0)
            total_bytes -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    @ui.refreshable
    def header_panel(self) -> None:
        """渲染顶部保存、发布与整体状态。"""

        artifact = self.workspace.artifact
        pending = artifact.pending_count()
        publication_text = "未发布" if artifact.publication is None else (
            "发布结果已过期" if artifact.publication_is_stale() else "ASS 已是最新"
        )
        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("字幕 OCR 复核工作台").classes("text-2xl font-bold")
                ui.label(str(self.workspace.path)).classes("text-xs text-slate-500")
                review_paths = self.available_review_paths()
                if len(review_paths) > 1:
                    ui.select(
                        {str(path): path.name for path in review_paths},
                        value=str(self.workspace.path),
                        label="切换剧集",
                        on_change=lambda event: self.switch_review(str(event.value)),
                    ).classes("w-96")
            with ui.row().classes("items-center gap-2"):
                ui.badge(f"待复核 {pending}", color="negative" if pending else "positive")
                ui.badge(publication_text, color="warning" if artifact.publication_is_stale() else "positive")
                if self.workspace.dirty:
                    ui.badge("未保存", color="warning")
                ui.button("撤销", on_click=self.undo).props("outline")
                ui.button("重做", on_click=self.redo).props("outline")
                ui.button("保存", on_click=self.save).props("outline color=primary")
                ui.button("发布 ASS", on_click=self.request_publish).props(
                    "unelevated color=positive"
                )

    @ui.refreshable
    def queue_panel(self) -> None:
        """渲染可筛选和批量选择的字幕事件队列。"""

        self.event_cards.clear()
        ui.select(
            {
                "all": "全部",
                "pending": "待复核",
                "auto_accepted": "自动通过",
                "accepted": "人工通过",
                "deleted": "已删除",
            },
            value=self.status_filter,
            label="状态",
            on_change=lambda event: self._change_status_filter(str(event.value)),
        ).classes("w-full")
        ui.input(
            "搜索正文或 ID",
            value=self.search_text,
            on_change=lambda event: self._change_search(str(event.value)),
        ).classes("w-full")
        with ui.row().classes("w-full gap-2"):
            ui.button("删除所选", on_click=self.batch_delete_selected).props("outline color=negative")
            ui.button("恢复所选", on_click=self.batch_restore_selected).props("outline")
        with ui.expansion("按时间范围删除 OP/ED").classes("w-full"):
            range_start = ui.number("开始 ms", value=0, step=100).classes("w-full")
            range_end = ui.number("结束 ms", value=90000, step=100).classes("w-full")
            ui.button(
                "确认软删除该范围",
                on_click=lambda: self.delete_time_range(
                    int(range_start.value), int(range_end.value)
                ),
            ).props("outline color=negative")
        for event in self.filtered_events():
            active_class = " bg-blue-50" if event.event_id == self.current_event_id else ""
            card = ui.card().classes(f"w-full p-2 cursor-pointer{active_class}")
            self.event_cards[event.event_id] = card
            with card:
                with ui.row().classes("w-full items-start no-wrap"):
                    ui.checkbox(
                        value=event.event_id in self.selected_event_ids,
                        on_change=lambda change, event_id=event.event_id: self.toggle_selected(
                            event_id, bool(change.value)
                        ),
                    )
                    with ui.column().classes("gap-0 flex-grow").on(
                        "click", lambda _, event_id=event.event_id: self.select_event(event_id)
                    ):
                        ui.label(event.text.replace("\n", " / ")).classes("text-sm")
                        ui.label(
                            f"{milliseconds_text(event.start_ms)}–{milliseconds_text(event.end_ms)}"
                        ).classes("text-xs text-slate-500")
                        ui.badge(event.status)

    def _change_status_filter(self, value: str) -> None:
        """更新队列状态过滤器。"""

        self.status_filter = value
        self.queue_panel.refresh()

    def _change_search(self, value: str) -> None:
        """更新队列搜索文本。"""

        self.search_text = value
        self.queue_panel.refresh()

    @ui.refreshable
    def detail_panel(self) -> None:
        """渲染当前事件正文、时间和结构操作。"""

        event = self.current_event()
        if event is None:
            ui.label("没有字幕事件")
            return
        ui.label(event.event_id).classes("text-xs text-slate-500")
        text_input = ui.textarea("字幕正文", value=event.text).classes("w-full")
        with ui.row().classes("w-full"):
            start_input = ui.number("开始 ms", value=event.start_ms, step=100).classes("w-1/2")
            end_input = ui.number("结束 ms", value=event.end_ms, step=100).classes("w-1/2")
        ui.label("复核原因：" + ("、".join(event.reasons) if event.reasons else "无"))
        ui.label(f"OCR 置信度：{event.confidence:.4f}")
        with ui.row().classes("w-full gap-2"):
            ui.button(
                "应用修改",
                on_click=lambda: self.update_current(
                    str(text_input.value), int(start_input.value), int(end_input.value)
                ),
            ).props("color=primary")
            ui.button("确认通过", on_click=self.accept_current).props("outline color=positive")
            if event.status == "deleted":
                ui.button("恢复", on_click=self.restore_current).props("outline")
            else:
                ui.button("删除", on_click=self.delete_current).props("outline color=negative")
        ui.separator()
        split_input = ui.number(
            "拆分时间 ms",
            value=(event.start_ms + event.end_ms) // 2,
            step=100,
        ).classes("w-full")
        second_text_input = ui.textarea("拆分后第二条正文", value=event.text).classes("w-full")
        with ui.row().classes("w-full gap-2"):
            ui.button(
                "拆分",
                on_click=lambda: self.split_current(
                    int(split_input.value), str(second_text_input.value)
                ),
            ).props("outline")
            ui.button("与下一条合并", on_click=self.merge_with_next).props("outline")
            ui.button("在后方新增", on_click=self.add_after_current).props("outline")

    @ui.refreshable
    def evidence_panel(self) -> None:
        """渲染当前字幕的完整帧、字幕区域和 OCR 候选证据。"""

        event = self.current_event()
        if event is None:
            ui.label("没有可显示证据")
            return
        ui.label("静态帧证据").classes("text-lg font-semibold")
        timestamp = event.representative_timestamp_ms
        frame_path, crop_path = self._cache_current_evidence(timestamp)
        ui.label(f"当前帧 · {milliseconds_text(timestamp)}").classes("text-xs text-slate-500")
        if frame_path is not None:
            ui.image(_image_data_url(frame_path)).classes(
                "w-full max-w-3xl shrink-0 rounded"
            ).props("fit=contain")
        ui.label("字幕区域").classes("text-xs text-slate-500")
        if crop_path is not None:
            ui.image(_image_data_url(crop_path)).classes(
                "w-full max-w-3xl shrink-0 rounded"
            ).props("fit=contain")
        ui.separator()
        ui.label("事件候选").classes("text-lg font-semibold")
        for candidate in event.candidates:
            with ui.card().classes("w-full p-2"):
                ui.label(candidate.text.replace("\n", " / "))
                ui.label(
                    f"出现 {candidate.occurrences} 次 · 置信度 {candidate.mean_confidence:.4f} · "
                    f"共识 {candidate.consensus_score:.4f}"
                ).classes("text-xs text-slate-500")

    def build_ui(self) -> None:
        """构建字幕复核工作台三栏页面。"""

        ui.page_title("字幕 OCR 复核工作台")
        ui.add_css(
            "body { background: #f4f7fb; color: #172033; } "
            ".ocr-shell { width: min(1900px, calc(100vw - 24px)); margin: 0 auto; }"
        )
        with ui.header().classes("ocr-shell bg-white rounded-xl mt-2 px-4 py-3"):
            self.header_panel()
        with ui.row().classes("ocr-shell w-full items-start no-wrap gap-3 mt-3"):
            with ui.column().classes("w-1/4 bg-white rounded-xl p-3 max-h-[86vh] overflow-auto"):
                self.queue_panel()
            with ui.column().classes("w-1/3 bg-white rounded-xl p-4 max-h-[86vh] overflow-auto"):
                self.detail_panel()
            with ui.column().classes("flex-grow bg-white rounded-xl p-4 max-h-[86vh] overflow-auto"):
                self.evidence_panel()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析字幕 OCR 复核工作台启动参数。"""

    parser = argparse.ArgumentParser(description="启动字幕 OCR 复核工作台")
    parser.add_argument("--input", type=Path, required=True, help="review JSON 路径")
    parser.add_argument("--host", default="127.0.0.1", help="NiceGUI 绑定 host")
    parser.add_argument("--port", type=int, default=8190, help="NiceGUI 端口")
    parser.add_argument("--native", action="store_true", help="使用 NiceGUI native 模式")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """启动字幕 OCR 复核工作台。"""

    args = parse_args(argv)

    def build_root() -> None:
        """为浏览器会话创建独立字幕审核页面。"""

        OCRSubtitleReviewEditor(args.input).build_ui()

    ui.run(
        root=build_root,
        host=args.host,
        port=args.port,
        native=args.native,
        reload=False,
        title="字幕 OCR 复核工作台",
        favicon="🎞️",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
