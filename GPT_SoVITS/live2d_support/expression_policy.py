from __future__ import annotations

import re
from collections.abc import Container, Iterable
from pathlib import Path


# 在启动一个动作时，如果 motion 文件名中包含左侧的单词，那么同时启动右侧列表中第一个存在的表情（如有）
# 注意右侧表情永远是从左到右依次尝试使用的，不是随机的
MOTION_TOKEN_EXPRESSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("smile", ("exp_smile02", "exp_smile01", "exp_bsmile01")),
    ("kime", ("exp_kime01", "exp_smile01")),
    ("wink", ("exp_smile01", "exp_shy01")),
    ("cry", ("exp_cry01", "exp_sad01")),
    ("sad", ("exp_sad01",)),
    ("angry", ("exp_angry01",)),
    ("denial", ("exp_upset01", "exp_sneer01", "exp_serious02", "exp_angry01")),
    ("question", ("exp_surprised01",)),
    ("surprised", ("exp_surprised01",)),
    ("nervous", ("exp_pale01", "exp_dispair01")),
    ("thinking", ("exp_thinking01", "exp_serious01")),
    ("check", ("exp_thinking01", "exp_serious01")),
    ("serious", ("exp_serious01", "exp_serious02")),
    ("look", ("exp_idle01",)),
    ("idle", ("exp_idle01",)),
    ("bye", ("exp_smile01", "exp_bsmile01", "exp_idle01")),
)

# 如果通过 adapter.start_sematic_expression() 启动了一个语义表情，那么就会尝试使用右侧列表中第一个存在的表情
SEMANTIC_EXPRESSION_CANDIDATES: dict[str, tuple[str, ...]] = {
    "idle": ("idle", "exp_idle01", "exp_idle02", "exp_idle03"),
    "serious": ("serious", "exp_serious01", "exp_serious02", "exp_idle01"),
    "happiness": ("exp_smile02", "exp_smile01", "exp_bsmile01", "exp_kime01", "exp_idle01"),
    "like": ("exp_smile02", "exp_shy01", "exp_bsmile01", "exp_smile01", "exp_kime01", "exp_idle01"),
    "sadness": ("exp_sad01", "exp_sad02", "exp_cry01", "exp_cry02", "exp_pale01", "exp_serious01", "exp_idle01"),
    "anger": ("exp_angry01", "exp_angry02", "exp_hatred01", "exp_upset01", "exp_serious02", "exp_serious01", "exp_idle01"),
    "disgust": ("exp_upset01", "exp_sneer01", "exp_smirk01", "exp_serious02", "exp_angry01", "exp_sad01", "exp_idle01"),
    "surprise": ("exp_surprised01", "exp_surprised02", "exp_amazed01", "exp_pale01", "exp_upset01", "exp_idle01"),
    "fear": ("exp_pale01", "exp_dispair01", "exp_surprised01", "exp_cry01", "exp_sad01", "exp_serious01", "exp_idle01"),
    "text_generating": ("exp_thinking01", "exp_thinking02", "exp_serious01", "exp_idle01"),
}


def normalized_name_tokens(value: str) -> tuple[str, ...]:
    """把动作文件名或动作组名规范化为用于匹配的 token。"""
    stem = Path(value).name
    lowered_stem = stem.lower()
    tokens: list[str] = []
    for raw_token in re.split(r"[^0-9a-zA-Z]+", lowered_stem):
        if not raw_token:
            continue
        normalized_token = re.sub(r"\d+$", "", raw_token)
        if normalized_token:
            tokens.append(normalized_token)
    return tuple(tokens)


def expression_candidates_for_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    """根据规范化 token 返回表情候选列表。"""
    token_set = frozenset(tokens)
    for token, candidates in MOTION_TOKEN_EXPRESSION_RULES:
        if token in token_set:
            return candidates
    return ()


def semantic_expression_key(value: str) -> str:
    """把语义表情名规范化为候选表的 key。"""
    return value.strip().lower()


def semantic_expression_candidates(semantic_name: str) -> tuple[str, ...] | None:
    """读取语义表情名对应的候选表情 ID。"""
    return SEMANTIC_EXPRESSION_CANDIDATES.get(semantic_expression_key(semantic_name))


def select_supported_expression(candidates: Iterable[str], supported_expression_ids: Container[str]) -> str | None:
    """从候选表情 ID 中选择当前模型支持的第一个表情。"""
    for candidate in candidates:
        if candidate in supported_expression_ids:
            return candidate
    return None


def select_expression_for_motion(
        group_name: str,
        motion_file: str | None,
        supported_expression_ids: Container[str],
) -> str | None:
    """根据动作文件名和动作组名选择当前模型支持的自动表情。"""
    if motion_file:
        motion_candidates = expression_candidates_for_tokens(normalized_name_tokens(motion_file))
        expression_id = select_supported_expression(motion_candidates, supported_expression_ids)
        if expression_id is not None:
            return expression_id

    semantic_candidates = semantic_expression_candidates(group_name)
    expression_id = select_supported_expression(semantic_candidates or (), supported_expression_ids)
    if expression_id is not None:
        return expression_id

    group_tokens = normalized_name_tokens(group_name)
    group_candidates = expression_candidates_for_tokens(group_tokens)
    expression_id = select_supported_expression(group_candidates, supported_expression_ids)
    if expression_id is not None:
        return expression_id

    for group_token in group_tokens:
        semantic_candidates = SEMANTIC_EXPRESSION_CANDIDATES.get(group_token)
        expression_id = select_supported_expression(semantic_candidates or (), supported_expression_ids)
        if expression_id is not None:
            return expression_id

    return select_supported_expression(SEMANTIC_EXPRESSION_CANDIDATES["idle"], supported_expression_ids)
