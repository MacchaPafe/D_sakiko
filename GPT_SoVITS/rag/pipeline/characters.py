"""第一阶段候选角色构造。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rag.models import SeriesId
from ui_constants import char_info_json

from .schemas import CandidateCharacter


@dataclass(frozen=True)
class SeriesAnnotationProfile:
    """定义一个动画系列在字幕标注阶段使用的稳定配置。"""

    anime_title: str
    primary_character_ids: frozenset[str]


MYGO_MUJICA_PRIMARY_CHARACTER_IDS = frozenset(
    {
        "tomori",
        "anon",
        "rana",
        "soyo",
        "taki",
        "sakiko",
        "mutsumi",
        "uika",
        "umiri",
        "nyamu",
    }
)

SERIES_ANNOTATION_PROFILES: dict[SeriesId, SeriesAnnotationProfile] = {
    SeriesId.ITS_MYGO: SeriesAnnotationProfile(
        anime_title="It's MyGO!!!!!",
        primary_character_ids=MYGO_MUJICA_PRIMARY_CHARACTER_IDS,
    ),
    SeriesId.AVE_MUJICA: SeriesAnnotationProfile(
        anime_title="BanG Dream! Ave Mujica",
        primary_character_ids=MYGO_MUJICA_PRIMARY_CHARACTER_IDS,
    ),
    SeriesId.YUME_MITA: SeriesAnnotationProfile(
        anime_title="TV动画「BanG Dream! YUME∞MITA」",
        primary_character_ids=frozenset({"arale", "nonoka", "ritsu", "miyako", "yuno"}),
    ),
}


MANUAL_ALIAS_OVERRIDES = {
    "tomori": ["灯", "小灯", "高松灯", "高松燈"],
    "anon": ["爱音", "千早", "千早爱音", "千早同学", "爱音同学"],
    "rana": ["乐奈", "要乐奈"],
    "soyo": ["素世", "小素世", "长崎素世", "長崎そよ", "长崎爽世"],
    "taki": ["立希", "椎名立希"],
    "sakiko": ["祥子", "小祥", "丰川祥子", "豊川祥子", "祥ちゃん"],
    "mutsumi": ["睦", "小睦", "若叶睦", "若葉睦"],
    "uika": ["初华", "三角初华", "三角初華"],
    "umiri": ["海铃", "八幡海铃", "八幡海鈴"],
    "nyamu": ["喵梦", "祐天寺喵梦", "祐天寺にゃむ"],
    "kasumi": ["香澄", "户山香澄", "戸山香澄"],
    "rinko": ["燐子", "白金燐子", "白金燐子姐"],
    "lock": ["六花", "LOCK", "Lock", "朝日六花", "罗克"],
    "ako": ["亚子", "宇田川亚子", "宇田川あこ"],
    "chuchu": ["知由", "珠手知由", "CHU2", "Chu2", "chu2"],
    "arale": ["阿拉蕾", "仲町阿拉蕾", "仲町あられ", "Nakamachi Arale"],
    "nonoka": ["野乃花", "宫永野乃花", "宮永ののか", "Miyanaga Nonoka"],
    "ritsu": ["律", "峰月律", "Minetsuki Ritsu"],
    "miyako": ["都子", "藤都子", "Fuji Miyako"],
    "yuno": ["由乃", "千石由乃", "千石ユノ", "Sengoku Yuno"],
}


ANNOTATION_ONLY_CHARACTERS: tuple[CandidateCharacter, ...] = (
    CandidateCharacter(
        display_name="凛凛子",
        character_id="ririko",
        aliases=[
            "ririko",
            "RIRIKO",
            "凛凛子",
            "凛凛子姐",
            "凛々子",
            "凛々子姐",
        ],
        notes="仅供字幕标注与 RAG 使用的配角",
        score=0,
    ),
)


def _parse_full_name(full_name: str) -> tuple[str, str | None, str | None]:
    chinese_name = full_name.split("（", 1)[0].strip()
    japanese_name = None
    group_name = None
    match = re.search(r"（(.+?)）", full_name)
    if match:
        payload = match.group(1)
        if "-" in payload:
            japanese_name, group_name = payload.split("-", 1)
        else:
            japanese_name = payload
    return chinese_name, japanese_name, group_name


def build_character_catalog() -> list[CandidateCharacter]:
    """根据现有角色配置构建候选角色目录。"""

    catalog: list[CandidateCharacter] = []
    for display_name, info in char_info_json.items():
        romaji = info["romaji"]
        chinese_name, japanese_name, group_name = _parse_full_name(info["full_name"])

        aliases = {
            display_name,
            chinese_name,
            romaji,
            romaji.lower(),
            romaji.upper(),
        }
        if japanese_name:
            aliases.add(japanese_name)
        aliases.update(MANUAL_ALIAS_OVERRIDES.get(romaji, []))

        notes = None
        if group_name:
            notes = f"所属乐队/组合：{group_name}"

        catalog.append(
            CandidateCharacter(
                display_name=display_name,
                character_id=romaji,
                aliases=sorted(alias for alias in aliases if alias),
                notes=notes,
                score=0,
            )
        )

    catalog.extend(ANNOTATION_ONLY_CHARACTERS)
    return catalog


def get_series_annotation_profile(series_id: SeriesId | str) -> SeriesAnnotationProfile:
    """返回指定动画系列的字幕标注配置。"""

    normalized_series_id = SeriesId(series_id)
    try:
        profile = SERIES_ANNOTATION_PROFILES[normalized_series_id]
    except KeyError as error:
        raise ValueError(f"系列尚未配置字幕标注候选策略: {normalized_series_id.value}") from error

    catalog_ids = {candidate.character_id for candidate in build_character_catalog()}
    missing_ids = sorted(profile.primary_character_ids - catalog_ids)
    if missing_ids:
        raise ValueError(
            f"系列 {normalized_series_id.value} 的候选角色未出现在角色目录中: {', '.join(missing_ids)}"
        )
    return profile


def resolve_anime_title(series_id: SeriesId | str, override: str | None = None) -> str:
    """返回显式标题，未提供时使用系列标注配置中的标题。"""

    if override is not None and override.strip():
        return override.strip()
    return get_series_annotation_profile(series_id).anime_title


def default_episode_prior_candidates(series_id: SeriesId | str) -> set[str]:
    """返回指定动画系列第一阶段的默认高优先级候选角色集合。"""

    profile = get_series_annotation_profile(series_id)
    return {
        candidate.display_name
        for candidate in build_character_catalog()
        if candidate.character_id in profile.primary_character_ids
    }


def find_alias_hits(texts: Iterable[str], aliases: Iterable[str]) -> list[str]:
    """返回在文本集合中命中的别名。"""

    text_blob = "\n".join(texts)
    hits: list[str] = []
    for alias in aliases:
        if alias and alias in text_blob:
            hits.append(alias)
    return hits
