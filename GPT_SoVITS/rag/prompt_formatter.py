"""RAG 检索结果格式化模块。"""

from __future__ import annotations

from typing import List, Tuple

from .models import (
    CharacterRelationDocument,
    LoreEntryDocument,
    StoryEventDocument,
)
from .services import QueryHit


class RagPromptFormatter:
    """将 RAG 检索结果格式化为 LLM 可理解的提示文本。

    提供单角色对话模式和小剧场模式两种格式化方案，
    将结构化的 QueryHit 结果转化为自然语言描述。
    """

    def format_for_single_character(
        self,
        story_events: List[QueryHit[StoryEventDocument]],
        character_relations: List[QueryHit[CharacterRelationDocument]],
        lore_entries: List[QueryHit[LoreEntryDocument]],
        perspective_character: str,
    ) -> str:
        """为单角色对话模式格式化 RAG 检索结果。

        :param story_events: 剧情事件检索结果列表。
        :param character_relations: 角色关系检索结果列表。
        :param lore_entries: 世界观/名词检索结果列表。
        :param perspective_character: 当前扮演的角色名称。
        :return: 格式化后的 prompt 文本，无结果时返回空字符串。
        """
        sections: List[str] = []

        if story_events:
            event_lines = ["【相关剧情】"]
            for hit in story_events:
                event_lines.append(self.format_single_event(hit.document))
            sections.append("\n".join(event_lines))

        if character_relations:
            relation_lines = ["【角色关系参考】"]
            for hit in character_relations:
                relation_lines.append(
                    self.format_single_relation(hit.document, perspective_character)
                )
            sections.append("\n".join(relation_lines))

        if lore_entries:
            lore_lines = ["【相关设定】"]
            for hit in lore_entries:
                lore_lines.append(self.format_single_lore(hit.document))
            sections.append("\n".join(lore_lines))

        if not sections:
            return ""

        header = "以下是与当前对话相关的背景信息，请在回复时自然融入这些设定。对于和当前角色无关的设定，请忽略它们。"
        return header + "\n\n" + "\n\n".join(sections)

    def format_for_small_theater(
        self,
        story_events: List[QueryHit[StoryEventDocument]],
        character_relations: List[QueryHit[CharacterRelationDocument]],
        lore_entries: List[QueryHit[LoreEntryDocument]],
        character_pair: Tuple[str, str],
    ) -> str:
        """为小剧场模式格式化 RAG 检索结果。

        :param story_events: 剧情事件检索结果列表。
        :param character_relations: 角色关系检索结果列表。
        :param lore_entries: 世界观/名词检索结果列表。
        :param character_pair: 参与小剧场的两个角色名称。
        :return: 格式化后的 prompt 文本，无结果时返回空字符串。
        """
        sections: List[str] = []

        if character_relations:
            relation_lines = ["【角色关系】"]
            for hit in character_relations:
                doc = hit.document
                line = "- {subject} 对 {obj}：{label}（{summary}）".format(
                    subject=doc.subject_character_id.common_name,
                    obj=doc.object_character_id.common_name,
                    label=doc.relation_label,
                    summary=doc.state_summary,
                )
                if doc.object_character_nickname:
                    line += "，称呼：{0}".format(doc.object_character_nickname)
                if doc.speech_hint:
                    line += "，说话方式：{0}".format(doc.speech_hint)
                relation_lines.append(line)
            sections.append("\n".join(relation_lines))

        if story_events:
            event_lines = ["【相关剧情】"]
            for hit in story_events:
                event_lines.append(self.format_single_event(hit.document))
            sections.append("\n".join(event_lines))

        if lore_entries:
            lore_lines = ["【世界观】"]
            for hit in lore_entries:
                lore_lines.append(self.format_single_lore(hit.document))
            sections.append("\n".join(lore_lines))

        if not sections:
            return ""

        header = "以下是本次对话涉及的剧情背景，请严格参照："
        return header + "\n\n" + "\n\n".join(sections)

    def format_single_event(self, event: StoryEventDocument) -> str:
        """格式化单个剧情事件。

        :param event: 剧情事件文档对象。
        :return: 格式化后的单行文本描述。
        """
        participants = "、".join(p.common_name for p in event.participants)
        return "- {title}：{summary}（参与者：{participants}）".format(
            title=event.title,
            summary=event.summary,
            participants=participants,
        )

    def format_single_relation(
        self,
        relation: CharacterRelationDocument,
        perspective_character: str,
    ) -> str:
        """格式化单个角色关系（以当前扮演角色为视角）。

        :param relation: 角色关系文档对象。
        :param perspective_character: 当前扮演的角色名称。
        :return: 格式化后的关系描述文本。
        """
        parts: List[str] = []
        parts.append("- {sub}对{obj}的态度：{summary}".format(
            sub=relation.subject_character_id.common_name,
            obj=relation.object_character_id.common_name,
            summary=relation.state_summary,
        ))
        if relation.object_character_nickname:
            parts.append("  称呼对方为：{0}".format(relation.object_character_nickname))
        if relation.speech_hint:
            parts.append("  说话风格：{0}".format(relation.speech_hint))
        return "\n".join(parts)

    def format_single_lore(self, lore: LoreEntryDocument) -> str:
        """格式化单个世界观/名词条目。

        :param lore: 世界观条目文档对象。
        :return: 格式化后的单行文本描述。
        """
        return "- {title}：{content}".format(title=lore.title, content=lore.content)
