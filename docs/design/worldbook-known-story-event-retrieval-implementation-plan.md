# 世界书角色知情 Story Event 检索实施计划

## 1. 文档状态

- 当前阶段：`plan-then-code` Phase 2，代码实现与自动化验证已完成。
- 编码状态：阶段 0～6 已完成；阶段 7 等待用户完成人工内容审核与真实 E5 发布门。
- 编码授权：用户已于 2026-07-30 明确批准开始编码。
- 适用范围：普通单角色聊天、Stage 2A/Stage 3 世界书审核、正式世界书包与用户内容编辑。
- 最后更新：2026-07-30。

本文记录在现有四类世界书条目内，为 Story Event 增加显式角色知情权限，并让事实事件与 Character Thought 共同参与角色安全检索的实施方案。生产代码、测试与设计文档已经按本计划落地；现有正式数据的知情列表仍由用户人工审核，不由实现自动填充。

## 2. 背景与问题

当前聊天运行时只对当前角色的 Character Thought 进行直接语义检索；`search_character_memory` 也先检索 Thought，再展开其显式关联的 Story Event。没有任何 Thought 链接的客观经历无法进入模型上下文。

“爱音最初如何认识灯”暴露了这个缺口：

- 正式世界书中存在爱音摔倒、灯提供企鹅创可贴的 Story Event；
- 该 Event 没有 Character Thought 链接；
- 当前角色实际能够回忆这段亲历事件；
- 运行时没有检索到它，模型依靠自身训练记忆回答。

直接把“无 Thought 链接”解释为“公开事件”不安全，因为链接缺失可能来自标注取舍、标注遗漏、没有长期观点或角色本来就不知道完整事件。`participants` 也只表示参与事件，不保证角色知道 Event 摘要中的全部信息。

## 3. 目标

1. 保持 Story Event、Character Thought、Character Relation、Lore Entry 四类正式条目，不增加第五类条目或第五张 collection。
2. 为 Story Event 增加显式、正向授权的角色知情列表。
3. 让授权 Story Event 同时参与直接上下文和隐藏记忆工具检索。
4. 保留 Character Thought 的角色主观认知语义，不把普通经历改写成 Thought。
5. Thought 链接只描述语义关联，不授予完整 Event 正文权限。
6. 独立 Event 召回与 Thought 关联展开按 Event UUID 去重。
7. 在审核工作台显示 Thought 反向链接，帮助审核者识别互补信息和潜在重复。
8. 对旧数据和旧用户内容 fail closed，缺字段一律按空列表处理。
9. 扩展诊断与检索验收，使权限过滤、召回缺失和去重行为可观察、可回归。

## 4. 不在本轮范围

- 新增 Character Event Memory、Event View 或其他第五类正式条目。
- 为不同角色保存不同的 Event 摘要。
- 为每个知情角色保存独立的获知时间。
- 从 `participants`、`present_characters` 或 Thought 链接自动推导最终知情权限。
- 发布 Event Fact 或让运行时直接检索 Event Fact。
- 整轮维护“已经向模型发送过的 Event”状态。
- 把 Event/Thought 阈值暴露为普通用户设置。
- 修改多角色小剧场或旧 `QdrantRagService` 检索链路。
- 由代码或模型替代用户人工确定当前两集正式 Event 的最终知情列表。

## 5. 领域语言与核心不变量

### 5.1 新术语

**事件知情角色**：
依据剧情证据，可以在某个 Story Event 的 `visible_from` 之后安全获得该 Event 完整公开正文的角色。事件知情角色与事件参与者相互独立；Character Thought 链接既不是知情权限的充分条件，也不是必要条件。

正式字段名：

```text
known_by_character_ids
```

开发侧 Stage 2A 候选使用角色标准名，建议字段名：

```text
known_by_character_names
```

Stage 3 规范化时将标准名解析为 `CharacterId`，写入正式字段。

### 5.2 权限正文范围

当前角色位于 `known_by_character_ids` 时，运行时可以向模型公开：

- `title`
- `summary`
- 参与者显示名

不得因该权限向模型公开：

- entry UUID、point ID、candidate ID
- package ID
- `retrieval_text`
- importance、tags
- 内部剧情时间整数
- embedding 分数、boost 或阈值
- 审核、证据和索引元数据

### 5.3 时间语义

- 知情权限从 Event 自身的 `visible_from` 开始。
- 第一版不表示“某角色在晚于事件发生时间的节点才完整获知事件”。
- 后来获得的部分知识继续由自包含 Character Thought 表达。
- `known_by_character_ids` 可以包含非 `participants` 角色，但审核者必须确认从 Event 的 `visible_from` 起公开完整摘要是安全的。

### 5.4 权限不变量

1. 缺少 `known_by_character_ids` 等价于空列表。
2. 空列表表示没有角色可以直接检索完整 Event，不表示 Event 尚未发生。
3. `participants` 不能自动授予权限。
4. Thought 链接不能自动授予权限。
5. 当前角色没有 Event 权限时，可以返回她自己的 Thought，但不得展开完整 Event。
6. Event 有权限时，不要求必须存在 Thought 链接。
7. 同一角色同时拥有 Event 权限和相关 Thought 是合法状态，编辑器只提示，不自动修改。
8. Event 和 Thought 的模型可见正文分别保留；去重只针对重复的 Event 正文。

## 6. 正式与开发侧数据契约

### 6.1 Stage 2A Story Event 候选

在 `StoryEventCandidate` 增加：

```text
known_by_character_names: list[str] = []
```

约束：

- 值使用 Stage 2 输入中的角色标准名。
- 允许空列表。
- 保存时去重并保持首次出现顺序。
- 只在当前场景提供正面证据、能够确认角色知道完整 Event 摘要时加入。
- 不能仅因角色出现在 `participants` 或 `present_characters` 中加入。
- 摘要任一重要部分超出角色知情范围时，不加入该角色。
- 不确定时输出空列表。
- Thought 链接不参与 Stage 2A 权限推断。

旧 Stage 2A JSON 缺少字段时按空列表加载。更新 Prompt、response 示例、标注指南和组装测试，但不对现有输出做自动补全迁移。

### 6.2 Stage 3 与正式 Story Event

在以下强类型模型中增加：

```text
known_by_character_ids: list[CharacterId] = []
```

- `StoryEventPayload`
- `StoryEventDocument`

校验：

- 允许空列表。
- 非法 `CharacterId` 拒绝加载或保存。
- 重复值规范化去重并保持首次出现顺序。
- 不要求非空。
- 不要求是 `participants` 的子集。

`known_by_character_ids` 必须：

- 进入 Story Event 完整文档；
- 进入 `review_basis_sha256`；
- 进入正式包 JSON；
- 进入 Type Module payload 和 semantic fields；
- 作为过滤字段进入 Qdrant payload；
- 不参与 embedding 文本生成。

正式包始终显式写出空列表，不通过省略字段区分旧数据与明确审核为空。

### 6.3 Schema 与索引版本

- Story Event 继续使用实验 `schema_version=0`。
- 不新增 v1 adapter。
- `INDEX_SCHEMA_VERSION` 从 3 升到 4，强制派生索引全量重建。
- Story Event payload indexes 增加 `known_by_character_ids: KEYWORD`。
- 如果实现中索引投影契约也需要显式区分，则同步提升 `PROJECTION_VERSION`；不得只改 payload index 而让旧 fingerprint 继续复用。

## 7. 标注与审核工作流

### 7.1 Stage 2A 提议

Stage 2A LLM 只产生非权威候选。Prompt 明确：

- “知道完整摘要”与“参与事件”分开判断；
- 禁止从在场列表机械推断知情；
- 内心独白、离场台词、秘密、原因、责任和他人心理需要特别保守；
- 输出空列表是合法且优先于猜测的结果。

Stage 3 规范化将角色标准名解析为 `CharacterId`。无法解析的名字形成结构化 issue，不静默丢弃。

### 7.2 Stage 3 审核工作台

Story Event 表单增加“可直接知道完整事件的角色”多选字段，并支持：

- 显示所有合法角色；
- 标注哪些角色同时属于 `participants`；
- 一次性“从参与角色复制”按钮；
- 复制只修改当前草稿，不与参与者持续同步；
- 允许审核者添加非参与者；
- 修改后通过现有 `ReplaceStoryDocumentCommand` 撤销该 Event 的旧审批；
- 不新增独立的“知情范围已审核”布尔字段。

现有审核状态继续表达：

- `completed + []`：人工明确确认没有角色可直接获得完整 Event；
- 未完成状态加空列表：尚未完成审核。

### 7.3 Thought 反向链接提示

工作台从 `Stage3ThoughtReviewArtifact` 动态扫描：

```text
threads[*].effective_content().states[*].story_event_candidate_ids
```

对当前 Story Event 展示：

- Thought 持有角色；
- Thread 规范主题和方面；
- Thought State 文本；
- Epistemic Status；
- `visible_from` / `visible_to`；
- Thread 的审核状态和发布处置；
- Thought artifact 是否 stale。

显示所有 effective Thought 链接，不只显示已发布链接。提示为非阻断信息：

- 不自动勾选知情角色；
- 不自动取消知情角色；
- 不阻止 Story Event 保存或完成审核；
- 不替代运行时权限检查和去重。

### 7.4 Thought freshness

当前 Thought direct source 使用整份 Stage 3 RAG 文件摘要。修改 Story Event 的知情列表会让 Thought artifact 形式上 stale。

第一版保持保守规则：

1. Story Event 知情列表修改后重新组装 Thought Review。
2. 如果 Thought 的语义审核基础未变化，迁移既有人工内容、处置和备注。
3. 不在本轮引入字段级来源指纹。

## 8. 用户世界书编辑

用户创建的 Story Event、官方 Story Event Override 和正式世界书查看器中的 Story Event 编辑表单都支持 `known_by_character_ids`。

要求：

- 与 Stage 3 工作台使用相同字段含义和校验。
- 新建 Story Event 默认空列表。
- 基础检索文本仍只由 Event 摘要生成，不拼接角色 ID。
- 修改该字段属于语义与检索可见性变化，触发既有 retrieval review/index sync 流程。
- 用户 Story Event 只有显式设置当前角色权限后才能进入角色 Event 检索。
- 旧 Override/Extension 缺字段时按空列表加载，不能继承官方新增权限。

正式包显式增加空数组会改变 Event revision。现有用户 Override 可能形成一次可解释的基准冲突；继续遵守完整替换与冲突复核规则，不做字段级自动合并。

## 9. 运行时检索设计

### 9.1 检索仓库约束

在 `RetrievalConstraints` 增加独立字段：

```text
known_by_character_id: CharacterId | None
```

规则：

- 只对 `story_event` 使用。
- Qdrant filter 使用 `known_by_character_ids == current_character_id` 的数组成员匹配。
- 不能复用 `subject_character_id`，避免错误查询 `subject_character_id` 字段。
- 服务层再次执行 Python membership 硬过滤，不能只信 Qdrant。
- 包、时间线、分支、时间和角色知情权限全部满足后 Event 才可见。

### 9.2 模型安全 DTO

新增或重命名模型安全 DTO，使 Event 和 Thought 顶层分离：

```text
KnownStoryEvent
  title
  summary
  participant_names

DirectWorldbookContext
  events
  thoughts

CharacterMemoryKnowledge
  events
  thoughts
```

内部 trace 保留 Event UUID，模型可见 DTO 不包含 UUID。

### 9.3 直接上下文

以新的聚合入口替换聊天层对 `direct_thoughts()` 的直接依赖，例如：

```text
retrieve_direct_context(context, query, current_user_text)
```

实现内部独立执行：

1. 当前角色 Character Thought 检索；
2. 当前角色授权 Story Event 检索；
3. 从已选 Thought 收集关联 Event；
4. 对关联 Event 应用同样的角色知情权限与时间可见性过滤；
5. 合并独立检索到的 Event 与授权关联 Event；
6. 分来源阈值、排序与配额；
7. Event UUID 去重；
8. 分来源失败合并；
9. 模型安全投影。

Event 配额内优先保留独立语义检索结果；授权关联 Event 作为补充召回，按其来源 Thought 的排序填充剩余位置。两类来源合计仍不得超过 3 条 Event。

硬配额：

- 最多 3 条 Event；
- 最多 2 条 Thought；
- 两类配额不可互借。

输出和临时消息中 Event 排在 Thought 前：

```json
{
  "events": [],
  "thoughts": []
}
```

Event 与 Thought 使用独立阈值：

- 保留当前 Thought 直接阈值，除非真实验收显示需要调整；
- 新增严格的直接 Event 阈值；
- 直接 Event 阈值通过真实 E5 案例校准；
- 阈值为内部常量，不进入用户配置。

### 9.4 隐藏记忆工具

`search_character_memory` 同时执行：

1. 当前 Character Thought 语义检索；
2. 授权 Story Event 独立语义检索；
3. 从已选 Thought 收集显式关联 Event；
4. 对关联 Event 重新执行完整权限和时间过滤；
5. 将授权关联 Event 与独立 Event 命中取并集；
6. 按 Event UUID 去重；
7. 顶层返回 `events` 与 `thoughts`。

硬配额：

- 最多 5 条 Thought；
- 最多 5 条唯一 Event；
- 两类配额不可互借。

Thought 关联展开保留为补充召回：

- 关联 Event 未通过独立向量阈值，但与已选 Thought 相关且当前角色有权限时，可以进入 Event 结果；
- 当前角色没有权限时只返回 Thought；
- 同一 Event 被多条 Thought 链接或同时被独立检索命中时只返回一次。

工具描述改为查询“当前角色的经历、已知事实、判断、信念或怀疑”，不再把记忆工具描述成只有主观 Thought。

### 9.5 去重范围

- 直接上下文单次组合结果内去重。
- 每次工具调用结果内部去重。
- 不在直接上下文和后续工具调用之间维护已发送 Event 集合。
- 重复工具调用继续返回完整、自包含结果。
- 不返回“该结果本轮已经提供”等隐式占位信息。

### 9.6 分来源降级

Thought 和 Event 两路检索分别保存 failure：

- Thought 失败、Event 成功：返回 Event。
- Event 失败、Thought 成功：返回 Thought。
- 两路均失败：返回空知识和两路诊断错误，聊天继续普通生成。
- 单路失败不能把另一路成功结果标记为整体不可用。

为避免一个可空 `failure` 无法表达多源状态，运行时结果增加结构化 source failures，例如：

```text
source_failures:
  source: thought | event
  failure: RetrievalFailure
```

## 10. 聊天接入与提示词

### 10.1 直接注入

`dp_local2.py` 的直接世界书注入改为调用聚合入口，并序列化：

```json
{
  "events": [...],
  "thoughts": [...]
}
```

只有两个数组都为空时才不插入 `<worldbook_context>`。

现有 system instruction 中“当前角色此刻可用的主观知识”改为：

> 当前角色此刻可用的知识，可能包含她亲历或已知的事实，以及她自己的判断、信念和怀疑。

同时继续要求：

- 不向用户提及世界书、检索、工具或标签；
- Event 是当前角色可知的事实正文；
- Thought 保留自己的 Epistemic Status；
- 工具为空或失败不等于事实不存在；
- 不根据其他角色 Thought 补全当前角色未知事实。

### 10.2 工具结果

记忆工具结果不再使用：

```text
results: [{thought, events: [...]}]
```

改为：

```text
ok
events
thoughts
source_errors
query_budget_notice
```

内部 Event UUID 只用于去重和诊断，不发送给模型。

## 11. 诊断设计

世界书诊断格式升级，直接检索和记忆工具分别记录：

- query；
- `thought_candidates`；
- `selected_thought_ids`；
- `event_candidates`；
- `selected_event_ids`；
- `linked_event_ids`；
- `unauthorized_linked_event_ids`；
- `deduplicated_event_ids`；
- Thought/Event 各自 failure；
- Thought/Event 各自耗时；
- 最终实际注入或工具返回的 `events`、`thoughts`。

要求：

- trace 中选中 Event ID 能被 evaluation runner 读取。
- 权限过滤与低分未命中可区分。
- 关联 Event 因无权限被拒绝必须可见于诊断，但不进入模型结果。
- 诊断 JSON `format_version` 从 1 升级到 2。
- 旧 JSONL 仍可按原始字典显示或导出，不要求迁移历史日志。
- UI 不从提示词或再次检索重建诊断。

## 12. 评估与测试

### 12.1 Schema 与兼容

覆盖：

- 旧 Stage 2A candidate 缺字段时读取为空。
- 旧 Stage 3/正式 Story Event 缺字段时读取为空。
- 非法角色拒绝。
- 重复角色确定性去重。
- 非参与者可以进入知情列表。
- 正式 payload round-trip 保留字段。
- embedding 文本不包含知情角色。
- 正式包显式序列化空数组。

### 12.2 标注与审核

覆盖：

- Stage 2A Prompt 包含严格知情规则。
- response 组装保留候选知情角色。
- 规范化把标准名解析为 `CharacterId`。
- 无法解析的角色产生 issue。
- Story 表单保存字段并撤销审批。
- “从参与角色复制”只执行一次复制，不建立同步关系。
- Thought 反向链接使用 effective content。
- 反向链接显示角色、状态、时间、审核和 stale 信息。
- 反向链接不自动修改知情列表。
- 修改知情列表后 freshness 和 Thought Review 迁移符合保守规则。

### 12.3 索引与权限过滤

覆盖：

- 索引 Schema 版本提升并触发重建。
- `known_by_character_ids` 建立 KEYWORD payload index。
- 知情参与者可以命中。
- 不知情参与者不能命中。
- 知情非参与者可以命中。
- 未来 Event 不能命中。
- Qdrant fake 绕过过滤时，Python 二次过滤仍拒绝。
- Thought 链接不能绕过 Event 权限。

### 12.4 运行时组合

覆盖：

- 直接上下文最多 3 Event + 2 Thought。
- 两类配额不互借。
- Event 在序列化结果中位于 Thought 前。
- 工具最多 5 Thought + 5 unique Event。
- 独立 Event 命中与链接展开按 UUID 去重。
- 多条 Thought 链接同一 Event 时只输出一次 Event。
- 无权关联 Event 不输出，Thought 仍输出。
- Event 未通过独立阈值但经授权 Thought 链接补充召回。
- Thought/Event 单路失败时保留另一路结果。
- 重复工具调用保持自包含，不跨调用去重。

### 12.5 诊断与工具循环

覆盖：

- 直接诊断分别记录两类候选、选中项、失败与耗时。
- 工具诊断记录关联、权限拒绝和去重 ID。
- 诊断 `format_version=2`。
- 模型可见结果不包含内部 UUID、分数或过滤字段。
- 世界书工具仍遵守单回合七次预算和隐藏展示规则。

### 12.6 正式检索验收

扩展 evaluation trace 和 case 类型，使模型实际收到的 Event ID 可验收。至少增加：

1. 爱音询问“最初如何认识灯”命中创可贴 Event。
2. 灯视角可以命中同一 Event。
3. 参与但不在知情列表中的角色不能命中。
4. 非参与但明确知情的角色可以命中。
5. Thought 链接不能越权展开 Event。
6. 独立检索和 Thought 链接同时召回时 Event 只出现一次。
7. 当前进度不能看到未来 Event。
8. 与世界书无关的问题不注入 Event。
9. Event/Thought 单路失败的降级测试。

创可贴正例和至少一个跨角色泄漏负例必须进入真实 `multilingual-e5-small` 发布门，不只使用 fake embedding。

阈值校准流程：

1. 为 Event 直接检索和工具检索定义独立内部常量。
2. 在人工知情列表完成后运行真实 E5 案例。
3. 保存候选分数和误召回诊断。
4. 选择能通过正例与泄漏/无关负例的阈值。
5. 不根据当前两集小样本宣布最终冻结；继续遵守 ADR-0038 的增量校准策略。

## 13. 旧数据与发布迁移

### 13.1 自动兼容

- 旧字段缺失统一解释为空列表。
- 不从 `participants` 自动填充。
- 不自动修改现有正式 Story Event。
- 不改变 Story Event UUID。
- 不新增 schema adapter。

### 13.2 人工审核检查点

当前审核数据包含 31 个 Story Event 候选：

- 27 个 `publish`；
- 4 个 `exclude`。

实现完成后：

1. 新字段进入 review basis。
2. 只重置 27 个发布 Event 的审核。
3. 4 个 excluded Event 保持既有完成状态。
4. 用户在 Stage 3 工作台逐项确认 `known_by_character_ids`。
5. 代码和 LLM 不替用户猜测这 27 个最终列表。

在人工审核完成前：

- 可以完成 Schema、编辑器、索引和运行时代码测试；
- 旧正式包因默认空列表不会直接检索 Story Event；
- 不运行以创可贴 Event 为必过正例的正式发布门；
- 不宣称该功能已经完成内容发布。

### 13.3 正式重建

人工审核完成后：

1. 重新组装因来源指纹而 stale 的 Thought Review。
2. 在 Thought basis 未变化时迁移既有人工审批。
3. 运行世界书 build validation。
4. 重建正式 MyGO 包，显式写出全部空/非空知情列表。
5. 保持正式 Event UUID 稳定。
6. 接受内容 revision 变化及可能的用户 Override 基准冲突。
7. 触发索引 Schema v4 全量重建。
8. 运行 fake 与真实 E5 发布门。

## 14. 文档与 ADR

实施前或与第一阶段代码同时完成：

1. 在 `CONTEXT.md` 增加“事件知情角色”定义。
2. 新建 ADR-0040，记录：
   - 保持四类条目；
   - Story Event 使用正向角色授权；
   - `participants` 与知情权限分离；
   - Thought 链接不是权限；
   - 授权关联展开与独立检索取并集；
   - Event UUID 去重；
   - 缺字段 fail closed；
   - 不选择第五类角色事件记忆的原因。
3. ADR-0040 显式 supersede ADR-0013 中“缺 Thought 即无充分知情证据”的绝对规则。
4. ADR-0040 显式 supersede ADR-0034 中“命中 Thought 后总是展开完整关联 Event”的规则。
5. 为 ADR-0032 增加 Story Event 正式字段、审核与索引投影补充，不改变四类条目和实验 v0 决策。
6. 更新已完成的 RAG 聊天接入计划，以追加变更说明的方式记录直接上下文从“最多三条 Thought”变为“最多三条 Event + 两条 Thought”，不重写历史实施状态。
7. 更新 Stage 2A 标注指南、Stage 3 工作台设计和检索验收说明。

## 15. 预计文件影响

### 15.1 领域模型与标注

- `CONTEXT.md`
- `GPT_SoVITS/rag/models.py`
- `GPT_SoVITS/rag/pipeline/schemas.py`
- `GPT_SoVITS/rag/pipeline/prompts/extraction_pass.jinja`
- `GPT_SoVITS/rag/pipeline/annotation_guides/stage2a_document_extraction.md`
- `GPT_SoVITS/rag/pipeline/stage3_rag_import.py`
- `GPT_SoVITS/rag/pipeline/stage3_document_models.py`
- `GPT_SoVITS/rag/pipeline/stage3_document_review.py`

### 15.2 审核与用户编辑

- `GPT_SoVITS/rag/pipeline/stage3_review_workbench.py`
- `GPT_SoVITS/rag/pipeline/stage3_review_workspace.py`
- `GPT_SoVITS/rag/worldbook/editing.py`
- `GPT_SoVITS/ui/components/worldbook_editor.py`
- 相关 Stage 3、editing 和 Qt UI 测试

### 15.3 正式世界书与索引

- `GPT_SoVITS/rag/worldbook/entry_types/story_event.py`
- `GPT_SoVITS/rag/worldbook/index_schema.py`
- `GPT_SoVITS/rag/worldbook/qdrant_index.py`（仅在现有通用逻辑不能自动处理新索引时）
- `GPT_SoVITS/rag/worldbook/builder.py`（预计不需要字段专用拷贝，只补测试）
- `GPT_SoVITS/rag/worldbook/build_audit.py`（如需补审核门）

### 15.4 运行时与聊天

- `GPT_SoVITS/rag/worldbook/runtime/models.py`
- `GPT_SoVITS/rag/worldbook/runtime/retrieval.py`
- `GPT_SoVITS/rag/worldbook/runtime/service.py`
- `GPT_SoVITS/rag/worldbook/runtime/tools.py`
- `GPT_SoVITS/rag/worldbook/runtime/diagnostics.py`
- `GPT_SoVITS/dp_local2.py`

### 15.5 评估与测试

- `GPT_SoVITS/rag/evaluation/models.py`
- `GPT_SoVITS/rag/evaluation/runner.py`
- `GPT_SoVITS/rag/evaluation/cases/its_mygo.json`
- `GPT_SoVITS/test/test_worldbook_core.py`
- `GPT_SoVITS/test/test_worldbook_builder.py`
- `GPT_SoVITS/test/test_worldbook_editing.py`
- `GPT_SoVITS/test/test_worldbook_ui.py`
- `GPT_SoVITS/test/test_worldbook_retrieval.py`
- `GPT_SoVITS/test/test_worldbook_tool_integration.py`
- `GPT_SoVITS/test/test_worldbook_diagnostics.py`
- `GPT_SoVITS/test/test_worldbook_evaluation.py`
- `GPT_SoVITS/test/test_worldbook_real_embedding.py`
- 相关 Stage 2A/Stage 3 工作台测试

### 15.6 设计文档

- 新 ADR-0040
- `docs/adr/0013-use-character-thoughts-for-perspective-safe-rag.md`
- `docs/adr/0032-build-and-audit-the-final-worldbook-package.md`
- `docs/adr/0034-deliver-worldbook-knowledge-through-direct-context-and-hidden-tools.md`
- `docs/技术文档/RAG 接入 LLM 对话系统实施计划.md`
- `docs/design/stage3-review-workbench-implementation-plan.md`

历史 ADR 的变更只用于明确 superseded/addendum 关系，不删除原始决策背景。

## 16. 分阶段实施顺序

### 阶段 0：领域契约与决策记录

1. 更新 `CONTEXT.md`。
2. 新建 ADR-0040。
3. 为受影响 ADR 和已有实施计划增加 superseded/addendum 说明。
4. 固定字段名称、权限范围、时间语义和 fail-closed 规则。

验收：文档与本计划一致，不再出现“Thought 链接自动授予完整 Event 权限”的表述。

### 阶段 1：Schema 与 Stage 2A/Stage 3 数据流

1. 扩展 Stage 2A candidate 与 Prompt。
2. 扩展 `StoryEventPayload`、`StoryEventDocument`。
3. 在 Stage 3 规范化中完成角色名到 ID 的转换。
4. 让 review basis、正式投影和 JSON round-trip 包含新字段。
5. 完成旧 JSON 缺字段兼容和非法角色校验。

验收：旧产物按空列表加载；新产物能稳定保存候选和正式 ID 列表。

### 阶段 2：审核工作台与用户编辑器

1. 增加知情角色多选和复制参与者按钮。
2. 动态计算 Thought 反向链接提示。
3. 展示审核、处置和 stale 状态。
4. 接入现有审批重置命令。
5. 扩展用户 Story Event 新建、Override 和编辑表单。

验收：空列表可被明确完成审核；链接提示不修改权限；用户 Event 默认不可直接检索。

### 阶段 3：正式投影与索引契约

1. Type Module 增加 semantic field。
2. Story Event payload index 增加知情角色。
3. 索引 Schema 升级到 4并验证 fingerprint 触发重建。
4. 增加 Qdrant 数组成员过滤与 Python 二次权限过滤测试。

验收：旧索引不会被错误复用；无权限 Event 即使 fake repository 返回也会被服务层拒绝。

### 阶段 4：运行时聚合模块

1. 增加 Event 专用检索 constraint。
2. 实现直接上下文聚合入口。
3. 实现记忆工具 Event/Thought 并行检索。
4. 保留授权 Thought 关联展开。
5. 在直接上下文和记忆工具各自的单次结果内按 UUID union/dedup。
6. 实现硬配额、独立阈值和分来源降级。
7. 更新模型安全 DTO 和 trace。

验收：直接上下文最多 3 Event + 2 Thought；工具最多 5 + 5；Thought 链接无法越权。

### 阶段 5：聊天、工具与诊断接线

1. 更新 `dp_local2.py` 直接注入。
2. 更新稳定 system instruction。
3. 更新记忆工具描述和结果结构。
4. 扩展直接/工具诊断并升级格式版本。
5. 保持隐藏工具、七次预算、回合快照和聊天历史不变。

验收：模型收到 Event-first 顶层结构；诊断能解释权限拒绝和去重；用户聊天历史不保存临时世界书正文。

### 阶段 6：自动化测试与阈值校准基础

1. 完成 Schema、pipeline、审核 UI、索引、运行时、工具和诊断测试。
2. 扩展 evaluation trace 和 case schema。
3. 增加 fake embedding 正例、负例和降级案例。
4. 运行 Python 语法检查和相关测试集合。

验收：不依赖人工标注内容的自动化测试全部通过；正式 E5 内容正例保持待人工数据检查点。

### 阶段 7：人工内容审核与正式发布门

本阶段包含用户人工工作，不由实现代码自动完成：

1. 用户审核 27 个发布 Event 的知情列表。
2. 重新组装 Thought Review 并确认审批迁移。
3. 重新构建正式包和索引。
4. 添加使用正式 UUID 的创可贴正例及泄漏负例。
5. 运行真实 E5 验收并校准 Event 阈值。
6. 完成正式发布审计。

验收：创可贴问题由世界书 Event 命中；跨角色和未来信息负例不泄漏；正式包与索引就绪。

## 17. Phase 2 验证要求

进入编码后，每个修改过的 Python 文件必须：

1. 保留或增加 `from __future__ import annotations`。
2. 使用完整类型标注。
3. 不新增 `Any` 类型标注。
4. 为新类和新函数编写中文 docstring。
5. 修改后运行 `py_compile` 或等价语法检查。

验证按风险分层执行：

1. 相关模块语法检查。
2. 定向单元测试。
3. 世界书 pipeline/build/runtime 组合测试。
4. Qt offscreen UI 测试。
5. fake embedding evaluation。
6. 人工数据完成后的真实 E5 发布门。

不得为了让测试通过而：

- 自动把 participants 复制到正式知情列表；
- 放宽权限过滤；
- 把检索失败当作合法零命中；
- 从模型已有记忆补写世界书数据；
- 跳过 stale、审核或索引 readiness 门槛。

## 18. 完成标准

代码实现完成需要同时满足：

- 四表结构保持不变。
- 新旧 Story Event 均能安全加载。
- 缺字段和空列表均 fail closed。
- Stage 2A 能提出候选，Stage 3 能人工确认。
- 用户 Story Event 编辑支持同一字段。
- Event 直接检索和记忆工具检索均接通。
- Thought 链接不能越权展开完整 Event。
- Event UUID 去重、硬配额和分来源降级符合约定。
- 诊断、fake evaluation 和自动化测试覆盖关键路径。
- 文档和 ADR 与实现一致。

正式内容发布完成还要求：

- 用户完成人工知情列表审核。
- 正式包重建并保持 Event UUID 稳定。
- 索引 Schema v4 重建完成。
- 创可贴真实 E5 正例通过。
- 至少一个跨角色泄漏负例和一个未来事件负例通过。

## 19. 编码授权检查点

本计划已进入并完成 `plan-then-code` Phase 2 的代码范围。下一检查点是：

- 用户为现有发布 Story Event 完成人工 `known_by_character_ids` 审核；
- 重新组装 Thought Review、构建正式包并重建索引；
- 增加创可贴正式 UUID 正例及跨角色泄漏负例；
- 运行真实 E5 发布门并据诊断校准 Event 阈值。
