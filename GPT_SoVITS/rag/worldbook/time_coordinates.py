"""世界书剧情时间坐标的集中编码与展示模型。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.models import SeriesId


OPEN_ENDED_TIME = 999999
EPISODE_SLOT_COUNT = 50

_SERIES_BASES: dict[SeriesId, int] = {
    SeriesId.BANG_DREAM_1: 1000,
    SeriesId.BANG_DREAM_2: 2000,
    SeriesId.BANG_DREAM_3: 3000,
    SeriesId.ITS_MYGO: 4000,
    SeriesId.AVE_MUJICA: 5000,
}


@dataclass(frozen=True, slots=True)
class StoryTimeCoordinate:
    """表示可由内部整数稳定还原的集数与集内时间点。"""

    episode: int
    episode_offset: int

    def display_text(self) -> str:
        """返回不声称精确场景号的用户可读文本。"""

        return f"第 {self.episode} 集 · 时间点 {self.episode_offset}"


def encode_story_time(series_id: SeriesId | str, coordinate: StoryTimeCoordinate) -> int:
    """把集数与集内时间点编码为剧情时间轴整数。"""

    normalized_series = SeriesId(series_id)
    if coordinate.episode < 1:
        raise ValueError("集数必须大于等于 1")
    if not 0 <= coordinate.episode_offset < EPISODE_SLOT_COUNT:
        raise ValueError(f"集内时间点必须位于 0 到 {EPISODE_SLOT_COUNT - 1} 之间")
    return _SERIES_BASES[normalized_series] + (coordinate.episode - 1) * EPISODE_SLOT_COUNT + coordinate.episode_offset


def decode_story_time(series_id: SeriesId | str, value: int) -> StoryTimeCoordinate:
    """把剧情时间轴整数解码为集数与集内时间点。"""

    normalized_series = SeriesId(series_id)
    if value == OPEN_ENDED_TIME:
        raise ValueError("开放式时间上界不能解码为具体时间点")
    offset = value - _SERIES_BASES[normalized_series]
    if offset < 0:
        raise ValueError(f"时间坐标 {value} 早于系列 {normalized_series.value} 的起点")
    return StoryTimeCoordinate(
        episode=offset // EPISODE_SLOT_COUNT + 1,
        episode_offset=offset % EPISODE_SLOT_COUNT,
    )


def is_open_ended_time(value: int | None) -> bool:
    """判断可空时间上界是否表示持续有效。"""

    return value is None or value == OPEN_ENDED_TIME
