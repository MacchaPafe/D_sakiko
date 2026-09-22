# Character Thought 标注与检索实施计划

## 1. 目标

在现有 `story_events`、`character_relations` 和 `lore_entries` 之外，增加角色视角安全的 `character_thoughts` 数据，使角色扮演只获得当前角色在当前剧情时间确实持有的认知、推测、否认或主观解释。

本计划不把客观 Story Event 摘要直接当作角色知识，也不尝试从字幕完整还原每个角色的全部心理活动。

## 2. 核心领域模型

- `StoryEvent`：一段相对完整的客观剧情。
- `EventFact`：为角色观点链接或知情隔离而按需拆出的客观原子事实，不追求完整分解事件。
- `CharacterThoughtUpdate`：单个场景提供的局部观点变化证据。
- `ThoughtSubject`：观点谈论的语义对象，可以是事件、事实、独立主题或暂时无法判断的对象。
- `ThoughtThread`：同一角色围绕同一对象的某个认知方面形成的连续观点脉络。
- `CharacterThought`：Stage 3 从更新证据投影出的、在一个剧情时间区间内有效的角色观点。
- `CharacterRelation`：一个角色面对另一个角色时相对持续的互动态度、称呼和说话倾向，不承载对具体事件的解释。

完整术语定义见仓库根目录 `CONTEXT.md`。

## 3. 总体流程

```text
Stage 1
字幕 → 说话人、受话人、提及角色、内心独白等标注

Stage 2A（现有）
单场景 → StoryEventCandidate / CharacterRelationCandidate / LoreEntryCandidate

Stage 2B（新增）
单场景 + 当前场景的 Stage 2A Story Event 候选
  → EventFactCandidate
  → CharacterThoughtUpdateCandidate

Stage 3A
规范化 Story Event 与按需 Event Fact

Stage 3B
ThoughtReferenceLinker
  → linked / standalone / unresolved

Stage 3C
ThoughtTimelineBuilder
  → Thought Thread
  → 时间区间内有效的 CharacterThoughtDocument 候选

Stage 3D
ThoughtRiskAnalyzer + 数据集查看器人工复核

Stage 3E
导入 Qdrant `character_thoughts`
```

## 4. Stage 2 输入补充

当前第一阶段已有但第二阶段输入丢失了以下字段：

- `speaker_confidence`
- `is_inner_monologue`

应将它们补入 `Stage2Utterance` 和 `stage2_input_builder.py`。旧 JSON 读取时提供安全默认值，避免破坏已有数据。

Stage 2B 第一版直接复用完整 scene；只有 prompt 超过配置上限时，才使用带重叠的 utterance 窗口 fallback。`present_characters` 仅表示整个 scene 的候选集合，不能单独证明某个角色听见了局部台词。

## 5. Stage 2B 候选结构

### 5.1 EventFactCandidate

建议字段：

```text
scene_id
fact_local_id
event_local_id: str | None
fact_text
tags
evidence_u_ids
evidence_s_ids
confidence
```

只在以下至少一种情况成立时生成：

- 被当前 Character Thought Update 引用；
- 不同角色的知情范围明显不同；
- 涉及秘密、原因、身份、计划、责任或其他高泄漏风险信息。

### 5.2 CharacterThoughtUpdateCandidate

最小必填字段：

```text
scene_id
update_local_id
character_name
thought_text
subject_kind
subject_text
epistemic_status
provisional_update_type
evidence_strength
evidence_u_ids
extraction_confidence
```

可选字段：

```text
about_event_local_id
about_fact_local_id
effective_from_hint
inference_note
ambiguity_notes
```

不增加结构化 `information_basis` 或 `information_source_name`。若来源对观点有意义，直接写入 `thought_text` 并由字幕证据支持。

### 5.3 枚举建议

`subject_kind`：

```text
event
event_fact
standalone_topic
uncertain
```

`epistemic_status`：

```text
knows
believes
suspects
uncertain
rejects
```

该字段只描述角色主观确信程度，不保证观点客观正确。

`provisional_update_type`：

```text
acquired
reaffirmed
revised
retracted
disclosed_existing
unspecified
```

`evidence_strength`：

```text
explicit
inferred
```

`effective_from_hint`：

```text
current_scene
earlier_than_current_scene
explicit_prior_scene
unknown
```

## 6. Stage 2B 提取原则

允许两类证据：

1. 明确表达：台词、内心独白或明确回应直接表达角色观点。
2. 合理推断：局部上下文较可靠地表明角色目击、听见或理解了可直接感知的信息。

模型必须引用实际 evidence ID。不得仅凭整个 scene 的 `present_characters` 推断听众，也不得通过直接观察推断隐藏动机、未说出口的原因或他人内心。

同一段字幕可同时支持 Character Thought 和 Character Relation，但两者必须分别聚焦事件/事实解释与对人的持续互动态度，不能复制相同摘要。

## 7. Stage 3 引用链接

Stage 2B 不强迫生成全局事件或事实 ID。`subject_text` 必填，本地 ID 可选。

`ThoughtReferenceLinker` 按以下顺序处理：

1. 当前场景本地 ID 精确映射；
2. 时间、角色、标签与文本的确定性筛选；
3. 向量或关键词召回少量历史事件/事实候选；
4. LLM 仅在候选集合内选择链接目标；
5. 输出 `linked`、`standalone` 或 `unresolved` 及独立的 `link_confidence`。

`standalone_topic` 不强制链接事件；`unresolved` 保留供人工复核，在解决前不得进入 Qdrant。

## 8. Stage 3 时间线聚合

一个角色对同一 Story Event 可以同时持有多个 Character Thought。Stage 3 使用 `thought_thread_key` 区分状态、原因、责任等认知方面。

Stage 2B 的 `provisional_update_type` 只是局部判断；Stage 3 根据跨场景 Thought Thread 生成 `resolved_update_type`。二者不一致时保留差异并增加风险标记。

Transition 只用于：

- 创建新状态；
- 合并重申证据；
- 关闭被修正或撤回的旧状态；
- 生成 `valid_from/valid_to`。

Transition 不进入最终 CharacterThoughtDocument，也不注入角色 prompt。

默认以 Evidence Time 作为 `valid_from`。只有字幕提供明确且可可靠链接的先前时间锚点时才允许回溯；“早就知道”“一直认为”但无具体锚点时只保留提示，不猜测起点。

## 9. CharacterThoughtDocument

建议采用扁平、自包含的 Qdrant payload：

```text
character_id
series_id
timeline_id
canon_branch

thought_thread_key
subject_kind
about_event_id: str | None
about_fact_id: str | None
standalone_topic_key: str | None

thought_text
epistemic_status
valid_from
valid_to

tags
retrieval_text

source_scene_ids
evidence_u_ids
evidence_strength
extraction_confidence
link_confidence
```

约束：

- `event` 必须有 `about_event_id`；
- `event_fact` 必须有 `about_fact_id`；
- `standalone_topic` 必须有 `standalone_topic_key`；
- `uncertain` 不得进入最终 collection。

证据与置信度保留在 payload，但只为角色、系列、季、分支和时间等过滤字段建立 Qdrant 索引。

## 10. 风险与人工复核

所有合法记录都允许人工复核。结构校验错误负责阻止入库，语义风险只负责排序与提醒。

每条外层 import/review record 建议保存：

```text
review_status
risk_level
risk_score
risk_reasons
validation_errors
```

`review_status`：

```text
unreviewed
approved
edited
rejected
needs_followup
```

高风险来源包括：

- evidence 缺失、越界或跨 scene 不一致；
- 内心独白错误传播给其他角色；
- speaker 置信度低；
- `inferred` 证据；
- unresolved、低置信度或多候选接近的链接；
- 链接到未来事件；
- 无锚点回溯时间；
- 同一 Thought Thread 中时间重叠且观点冲突；
- 推断隐藏动机或私人事实；
- 全知口吻、关系表重复、近似重复或过宽观点；
- Stage 2B 与 Stage 3 对 subject/update 类型判断不一致。

风险等级取最高原因严重度，风险分数用于同级排序；阈值保持可配置。

数据集查看器应支持：

- Character Thoughts 分栏；
- 风险 badge、筛选和降序排序；
- 下一条高风险记录；
- evidence 台词及前后文高亮；
- 事件/事实链接；
- 同一 Thought Thread 的前后状态；
- review status 编辑。

## 11. 运行时策略

单角色模式以 `character_thoughts` 作为角色剧情认知的主要来源，按当前角色和时间过滤。客观 `story_events` 保留为离线索引、链接、审查和全知模式数据，默认不直接注入角色 prompt。

小剧场模式分别查询两个角色的 Character Thoughts，再结合双向 Character Relations。

缺少当前角色的 Character Thought 时采用安全默认：不注入对应客观事件。未来可另行设计经过明确标注的公共事实机制。

## 12. 实施顺序

### Phase 1：Stage 2B

- 补齐 Stage2Utterance 字段；
- 新增 schema、prompt、执行模块和 CLI；
- 生成候选 JSON，不改 Qdrant。

### Phase 2：Stage 3 与查看器

- 实现 Event Fact 规范化；
- 实现引用链接、Thought Thread 与时间线投影；
- 实现风险分析；
- 扩展数据集查看器和 review decisions。

### Phase 3：小规模校准

优先覆盖：CRYCHIC 解散、素世得知祥子在羽丘、爱音留学失败、自我修正、回溯披露、内心独白和 standalone topic。

### Phase 4：Qdrant 与运行时

- 新增 `character_thoughts` collection、导入与检索；
- 新增 formatter 与当前角色过滤；
- 使用配置开关并行比较新旧检索；
- 确认质量后关闭单角色模式的客观 Story Event 默认注入。

## 13. 验收标准

- 当前角色不会获得其他角色的观点；
- 内心独白不会传播给旁人；
- 早期时间点不会检索后期观点；
- 被修正的旧观点不会与新观点同时有效；
- standalone topic 不被强制链接事件；
- unresolved 和结构非法记录不进入 Qdrant；
- Event Fact 不被无谓完整拆分；
- 高风险记录可在查看器中优先筛选；
- 每条入库观点都能回溯到字幕证据。
