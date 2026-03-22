"""字幕读取、清洗和双语对齐。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import pysubs2

from .schemas import RawSubtitleLine, ScreenTextUnit, UtteranceUnit


DIALOGUE_JP_STYLES = {"Dial_JP", "Dial_JP2"}
DIALOGUE_ZH_STYLES = {"Dial_CH", "Dial_CH2"}
SCREEN_STYLES = {"Screen", "Cmt_CH"}
IGNORED_STYLES = {"Title", "Staff", "ED_JP", "ED_CH"}
RELEVANT_STYLES = DIALOGUE_JP_STYLES | DIALOGUE_ZH_STYLES | SCREEN_STYLES

ASS_TAG_RE = re.compile(r"\{[^{}]*\}")
MULTISPACE_RE = re.compile(r"\s+")
EPISODE_RE = re.compile(r"\[(\d{2,3})\]")


def extract_episode_number(path: str | Path) -> int:
    """从字幕文件名中提取集数。"""

    filename = Path(path).name
    matches = EPISODE_RE.findall(filename)
    if not matches:
        raise ValueError(f"无法从文件名中提取集数: {filename}")
    return int(matches[-1])


def ms_to_timestamp(milliseconds: int) -> str:
    """将毫秒转换为 h:mm:ss.xx 格式。"""

    total_seconds, ms = divmod(milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    centiseconds = ms // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def clean_ass_text(text: str) -> str:
    """清理 ASS 控制标签与多余空白。"""

    cleaned = ASS_TAG_RE.sub("", text)
    cleaned = cleaned.replace("\\N", " ").replace("\\n", " ").replace("\uFEFF", "")
    cleaned = MULTISPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def _collect_dialogue_line_numbers(path: Path) -> list[int]:
    line_numbers: list[int] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if raw_line.startswith("Dialogue:"):
            line_numbers.append(line_no)
    return line_numbers


def load_relevant_subtitle_lines(path: str | Path) -> list[RawSubtitleLine]:
    """读取字幕中与第一阶段相关的行。"""

    source_path = Path(path)
    dialogue_line_numbers = _collect_dialogue_line_numbers(source_path)
    subs = pysubs2.load(str(source_path))

    relevant_lines: list[RawSubtitleLine] = []
    dialogue_index = 0
    for event in subs.events:
        if event.type != "Dialogue":
            continue

        line_no = dialogue_line_numbers[dialogue_index]
        dialogue_index += 1

        style = event.style or ""
        if style in IGNORED_STYLES or style not in RELEVANT_STYLES:
            continue

        relevant_lines.append(
            RawSubtitleLine(
                source_path=str(source_path),
                line_no=line_no,
                layer=int(event.layer),
                start_ms=int(event.start),
                end_ms=int(event.end),
                style=style,
                raw_text=event.text,
                clean_text=clean_ass_text(event.text),
            )
        )

    return relevant_lines


def build_screen_text_units(lines: Iterable[RawSubtitleLine], episode: int) -> list[ScreenTextUnit]:
    """从字幕中提取并去重 screen/commentary 文本。"""

    dedup: dict[tuple[int, int, str, str], ScreenTextUnit] = {}
    counter = 1
    for line in lines:
        if line.style not in SCREEN_STYLES or not line.clean_text:
            continue

        kind = "commentary_note" if line.style == "Cmt_CH" else "screen_text"
        key = (line.start_ms, line.end_ms, kind, line.clean_text)
        if key in dedup:
            existing = dedup[key]
            merged_line_numbers = sorted(set(existing.source_line_nos + [line.line_no]))
            dedup[key] = existing.model_copy(update={"source_line_nos": merged_line_numbers})
            continue

        dedup[key] = ScreenTextUnit(
            s_id=f"ep{episode:02d}_s{counter:04d}",
            episode=episode,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            kind=kind,
            text=line.clean_text,
            source_line_nos=[line.line_no],
        )
        counter += 1

    return sorted(dedup.values(), key=lambda item: (item.start_ms, item.end_ms, item.s_id))


def _style_variant(style: str) -> int:
    return 2 if style.endswith("2") else 1


def _is_time_compatible(left: RawSubtitleLine, right: RawSubtitleLine, tolerance_ms: int = 50) -> bool:
    return abs(left.start_ms - right.start_ms) <= tolerance_ms and abs(left.end_ms - right.end_ms) <= tolerance_ms


def build_utterance_units(lines: Iterable[RawSubtitleLine], episode: int) -> list[UtteranceUnit]:
    """对齐双语对白，生成 utterance 单元。"""

    jp_lines = [line for line in lines if line.style in DIALOGUE_JP_STYLES and line.clean_text]
    zh_lines = [line for line in lines if line.style in DIALOGUE_ZH_STYLES and line.clean_text]
    used_zh_indexes: set[int] = set()

    utterances: list[UtteranceUnit] = []
    counter = 1

    for jp_line in jp_lines:
        matched_index = None
        for index, zh_line in enumerate(zh_lines):
            if index in used_zh_indexes:
                continue
            if _style_variant(jp_line.style) != _style_variant(zh_line.style):
                continue
            if not _is_time_compatible(jp_line, zh_line):
                continue
            matched_index = index
            break

        zh_text = ""
        source_line_nos = [jp_line.line_no]
        notes: list[str] = []
        if matched_index is not None:
            zh_line = zh_lines[matched_index]
            used_zh_indexes.add(matched_index)
            zh_text = zh_line.clean_text
            source_line_nos.append(zh_line.line_no)
        else:
            notes.append("missing_zh_pair")

        utterances.append(
            UtteranceUnit(
                u_id=f"ep{episode:02d}_u{counter:04d}",
                episode=episode,
                start_ms=jp_line.start_ms,
                end_ms=jp_line.end_ms,
                jp_text=jp_line.clean_text,
                zh_text=zh_text,
                source_line_nos=sorted(source_line_nos),
                notes=notes,
            )
        )
        counter += 1

    unmatched_zh_lines = [zh for index, zh in enumerate(zh_lines) if index not in used_zh_indexes]
    for zh_line in unmatched_zh_lines:
        utterances.append(
            UtteranceUnit(
                u_id=f"ep{episode:02d}_u{counter:04d}",
                episode=episode,
                start_ms=zh_line.start_ms,
                end_ms=zh_line.end_ms,
                jp_text="",
                zh_text=zh_line.clean_text,
                source_line_nos=[zh_line.line_no],
                notes=["missing_jp_pair"],
            )
        )
        counter += 1

    return sorted(utterances, key=lambda item: (item.start_ms, item.end_ms, item.u_id))
