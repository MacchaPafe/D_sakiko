# Codex 标注指南索引

这个目录中的每份文档只指导一个独立标注阶段。执行标注任务时，应只把对应阶段的文档交给 Codex，
不要把整个目录同时作为上下文。

| 阶段 | 指南 | 主要输出 |
| --- | --- | --- |
| Stage 1 | [stage1_speaker.md](stage1_speaker.md) | 每句字幕的说话人、对象和提及角色 |
| Stage 2A | [stage2a_document_extraction.md](stage2a_document_extraction.md) | Story Event、Relation Observation、Lore |
| Stage 2B | [stage2b_thought_extraction.md](stage2b_thought_extraction.md) | Event Fact、Character Thought Update |
| Stage 3 Relation | [stage3_relation_aggregation.md](stage3_relation_aggregation.md) | 跨场景 Character Relation State |
| Stage 3 Thought Link | [stage3_thought_linking.md](stage3_thought_linking.md) | unresolved 观点的语义链接决策与观点审查产物 |

这些指南面向 Codex 工作区标注。原有的一体化 LiteLLM 命令仍然可用，但不属于 Codex 直接标注的首选流程。

