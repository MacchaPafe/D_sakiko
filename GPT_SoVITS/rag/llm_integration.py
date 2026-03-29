"""RAG 与 LLM 对话系统的集成模块。"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional, Tuple

from .context_builder import RagContextBuilder
from .models import (
    CharacterRelationQuery,
    LoreQuery,
    StoryEventQuery,
)
from .prompt_formatter import RagPromptFormatter
from .services import QdrantRagService


class LLMRagIntegration:
    """RAG 与 LLM 对话的集成层。

    整合检索上下文构建、向量检索和结果格式化的完整流程，
    并提供消息列表注入能力，使 RAG 信息能以临时方式
    融入每轮 LLM 请求。
    """

    def __init__(
        self,
        rag_service: QdrantRagService,
        context_builder: RagContextBuilder,
        formatter: RagPromptFormatter,
        cache_enabled: bool = True,
        cache_ttl_minutes: int = 5,
    ) -> None:
        """初始化 RAG 集成层。

        :param rag_service: Qdrant RAG 服务实例。
        :param context_builder: 检索上下文构建器。
        :param formatter: 检索结果格式化器。
        :param cache_enabled: 是否启用查询结果缓存。
        :param cache_ttl_minutes: 缓存有效期（分钟）。
        """
        self.rag_service = rag_service
        self.context_builder = context_builder
        self.formatter = formatter
        self.cache_enabled = cache_enabled
        self.cache_ttl_seconds = cache_ttl_minutes * 60
        self._cache: Dict[str, Tuple[str, float]] = {}

    def _get_cache_key(
        self,
        query_text: str,
        character_name: str,
        current_time: int,
    ) -> str:
        """根据查询参数生成缓存键。

        :param query_text: 查询文本。
        :param character_name: 角色名称。
        :param current_time: 当前剧情时间点。
        :return: MD5 哈希字符串作为缓存键。
        """
        content = "{0}|{1}|{2}".format(query_text, character_name, current_time)
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """尝试从缓存中获取结果。

        :param cache_key: 缓存键。
        :return: 缓存的格式化文本，若已过期或不存在则返回 None。
        """
        if not self.cache_enabled:
            return None
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        cached_result, cached_time = entry
        if time.time() - cached_time > self.cache_ttl_seconds:
            del self._cache[cache_key]
            return None
        return cached_result

    def _put_to_cache(self, cache_key: str, result: str) -> None:
        """将结果存入缓存。

        :param cache_key: 缓存键。
        :param result: 要缓存的格式化文本。
        """
        if self.cache_enabled:
            self._cache[cache_key] = (result, time.time())

    def retrieve_and_format(
        self,
        current_message: str,
        recent_messages: List[str],
        character_name: str,
        current_time: int,
        mode: str = "single",
        theater_pair: Optional[Tuple[str, str]] = None,
        situation: Optional[str] = None,
        series_id: Optional[str] = None,
        season_id: Optional[int] = None,
        canon_branch: str = "main",
        top_k_events: int = 3,
        top_k_relations: int = 2,
        top_k_lore: int = 2,
    ) -> str:
        """执行 RAG 检索并返回格式化后的 prompt 文本。

        :param current_message: 当前用户消息。
        :param recent_messages: 最近对话历史消息列表。
        :param character_name: 当前角色名称。
        :param current_time: 当前剧情时间点。
        :param mode: 对话模式，"single" 或 "theater"。
        :param theater_pair: 小剧场模式的角色名称对（可选）。
        :param situation: 场景描述（可选）。
        :param series_id: 系列 ID（可选）。
        :param season_id: 季 ID（可选）。
        :param canon_branch: 剧情分支，默认 "main"。
        :param top_k_events: 剧情事件返回数量。
        :param top_k_relations: 角色关系返回数量。
        :param top_k_lore: 世界观条目返回数量。
        :return: 格式化后的 RAG prompt 文本，无结果时返回空字符串。
        """
        # 构建查询文本
        query_text = self.context_builder.build_query_text(
            current_message=current_message,
            recent_messages=recent_messages,
            situation=situation,
        )

        # 检查缓存
        # 所有查询文本、查询角色名称和时间节点一致的查询视为同一个查询。
        cache_key = self._get_cache_key(query_text, character_name, current_time)
        cached_result = self._get_from_cache(cache_key)
        if cached_result is not None:
            return cached_result

        # 构建检索上下文
        retrieval_context = self.context_builder.build_retrieval_context(
            current_time=current_time,
            series_id=series_id,
            season_id=season_id,
            canon_branch=canon_branch,
        )

        # 执行检索
        story_hits = self.rag_service.query_story_events(
            query_text=query_text,
            context=retrieval_context,
            # 查询故事时，不要求当前角色必须出现在故事中
            options=StoryEventQuery(require_character_match=False),
            top_k=top_k_events,
        )

        relation_hits = self.rag_service.query_character_relations(
            query_text=query_text,
            context=retrieval_context,
            options=CharacterRelationQuery(),
            top_k=top_k_relations,
        )

        lore_hits = self.rag_service.query_lore_entries(
            query_text=query_text,
            context=retrieval_context,
            options=LoreQuery(),
            top_k=top_k_lore,
        )

        # 格式化
        if mode == "theater" and theater_pair is not None:
            result = self.formatter.format_for_small_theater(
                story_events=story_hits,
                character_relations=relation_hits,
                lore_entries=lore_hits,
                character_pair=theater_pair,
            )
        else:
            result = self.formatter.format_for_single_character(
                story_events=story_hits,
                character_relations=relation_hits,
                lore_entries=lore_hits,
                perspective_character=character_name,
            )

        # 写入缓存
        self._put_to_cache(cache_key, result)

        return result

    @staticmethod
    def inject_rag_into_messages(
        original_messages: List[Dict[str, str]],
        rag_prompt: str,
        injection_mode: str = "system_before_user",
    ) -> List[Dict[str, str]]:
        """将 RAG prompt 注入到消息列表副本中。

        此方法不会修改原始消息列表，而是返回一个新的副本。
        RAG 内容为临时性内容，每轮对话应重新调用此方法。

        :param original_messages: 原始对话消息列表。
        :param rag_prompt: RAG 格式化后的 prompt 文本。
        :param injection_mode: 注入模式。
            - "system_before_user": 在最新 user 消息前插入一条独立的 system 消息。
            - "inline_user": 将 RAG 内容追加到最新 user 消息末尾。
        :return: 注入 RAG 后的消息列表副本。
        """
        if not rag_prompt:
            return [dict(msg) for msg in original_messages]

        # 创建消息列表的浅拷贝（每条消息也复制一份字典）
        messages: List[Dict[str, str]] = [dict(msg) for msg in original_messages]

        # 找到最后一条 user 消息的索引
        last_user_index: Optional[int] = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_index = i
                break

        if last_user_index is None:
            # 没有 user 消息，回退：在末尾追加 system 消息
            messages.append({"role": "system", "content": rag_prompt})
            return messages

        if injection_mode == "inline_user":
            # 方式二：将 RAG 内容追加到最新 user 消息中
            messages[last_user_index]["content"] = (
                messages[last_user_index]["content"] + "\n\n -- \n\n" + rag_prompt
            )
        else:
            # 方式一（默认）：在最新 user 消息前插入 system 消息
            rag_message: Dict[str, str] = {"role": "system", "content": rag_prompt}
            messages.insert(last_user_index, rag_message)

        return messages
