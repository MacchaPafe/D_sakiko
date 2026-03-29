"""RAG 检索上下文构建模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set

from .models import (
    CanonBranch,
    RetrievalContext,
    SeasonId,
    SeriesId,
)


@dataclass
class RagContextBuilder:
    """根据对话状态构建 RAG 检索所需的输入。

    负责将当前用户消息、最近对话历史和场景描述组合为
    适合向量检索的查询文本，以及构建检索上下文对象。
    """

    #: 滑动窗口大小：用于构建查询文本时纳入的最近消息条数。
    query_window_size: int = 3

    def build_query_text(
        self,
        current_message: str,
        recent_messages: Optional[List[str]] = None,
        situation: Optional[str] = None,
        max_length: int = 500,
    ) -> str:
        """构建用于向量检索的查询文本。

        将当前用户输入、最近几条对话消息和（可选的）场景描述
        拼接为一段紧凑的文本，用于生成 embedding 后进行向量检索。

        :param current_message: 当前用户输入的消息文本。
        :param recent_messages: 最近几条对话消息列表（可选）。
        :param situation: 小剧场模式下的场景描述（可选）。
        :param max_length: 查询文本的最大字符长度限制。
        :return: 拼接后的查询文本。
        """
        parts: List[str] = []

        # 小剧场场景描述放在最前面
        if situation:
            stripped_situation = situation.strip()
            if stripped_situation:
                parts.append(stripped_situation)

        # 当前用户消息是核心
        stripped_message = current_message.strip()
        if stripped_message:
            parts.append(stripped_message)

        # 添加最近消息的滑动窗口
        if recent_messages:
            window = recent_messages[-self.query_window_size:]
            for msg in window:
                stripped = msg.strip()
                if stripped:
                    parts.append(stripped)

        query_text = "\n".join(parts)

        # 截断到最大长度
        if len(query_text) > max_length:
            query_text = query_text[:max_length]
        
        print(f"构建的查询文本：{query_text}")

        return query_text

    def build_retrieval_context(
        self,
        current_time: int,
        series_id: Optional[str] = None,
        season_id: Optional[int] = None,
        canon_branch: str = "main",
    ) -> RetrievalContext:
        """构建 RetrievalContext 对象，供 QdrantRagService 查询使用。

        :param current_time: 当前剧情时间点。
        :param series_id: 系列 ID 字符串（可选），将转换为 SeriesId 枚举。
        :param season_id: 季 ID 整数（可选），将转换为 SeasonId 枚举。
        :param canon_branch: 剧情分支字符串，默认为 "main"。
        :return: 构建好的 RetrievalContext 实例。
        """
        resolved_series: Optional[SeriesId] = None
        if series_id is not None:
            resolved_series = SeriesId(series_id)

        resolved_season: Optional[SeasonId] = None
        if season_id is not None:
            resolved_season = SeasonId(season_id)

        resolved_branch = CanonBranch(canon_branch)

        return RetrievalContext(
            current_time=current_time,
            current_character_id=None,
            current_series_id=resolved_series,
            current_season_id=resolved_season,
            current_canon_branch=resolved_branch,
        )

    def extract_tag_keywords(
        self,
        text: str,
        known_tags: Optional[Set[str]] = None,
    ) -> List[str]:
        """从文本中提取可能的标签关键词。

        如果提供了已知标签集合，从文本中匹配这些标签并返回。

        :param text: 要从中提取关键词的文本。
        :param known_tags: 已知的标签集合（可选），用于精确匹配。
        :return: 提取到的关键词列表（已去重）。
        """
        keywords: List[str] = []

        if known_tags:
            normalized_text = text.lower()
            for tag in known_tags:
                if tag.lower() in normalized_text:
                    keywords.append(tag)

        # 保序去重
        return list(dict.fromkeys(keywords))
