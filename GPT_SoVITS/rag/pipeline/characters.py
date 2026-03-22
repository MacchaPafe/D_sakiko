"""第一阶段候选角色构造。"""

from __future__ import annotations

import re
from typing import Iterable

from ui_constants import char_info_json

from .schemas import CandidateCharacter


ITS_MYGO_PRIMARY_CHARACTER_IDS = {
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
    "rinko": ["燐子", "凛凛子", "凛凛子姐", "白金燐子", "白金燐子姐"],
    "lock": ["六花", "LOCK", "Lock", "朝日六花", "罗克"],
    "ako": ["亚子", "宇田川亚子", "宇田川あこ"],
    "chuchu": ["知由", "珠手知由", "CHU2", "Chu2", "chu2"],
}


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

    return catalog


def default_episode_prior_candidates() -> set[str]:
    """返回 ItsMyGO 第一阶段的默认高优先级候选角色集合。"""

    return {
        candidate.display_name
        for candidate in build_character_catalog()
        if candidate.character_id in ITS_MYGO_PRIMARY_CHARACTER_IDS
    }


def find_alias_hits(texts: Iterable[str], aliases: Iterable[str]) -> list[str]:
    """返回在文本集合中命中的别名。"""

    text_blob = "\n".join(texts)
    hits: list[str] = []
    for alias in aliases:
        if alias and alias in text_blob:
            hits.append(alias)
    return hits
