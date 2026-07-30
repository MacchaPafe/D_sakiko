# Use character thoughts for perspective-safe RAG

> 部分决策已由 [ADR-0040](0040-authorize-story-event-retrieval-by-character.md) 取代：缺少 Thought 不再等于角色一定不知道 Event；完整 Event 正文改由 `known_by_character_ids` 显式授权。

Single-character roleplay uses `character_thoughts` as its primary source of narrative cognition and does not inject objective `story_events` summaries by default. Story Events remain the objective offline index for extraction, linking, review, and omniscient workflows; absence of a Character Thought is treated as insufficient evidence that the current character knows the event.
