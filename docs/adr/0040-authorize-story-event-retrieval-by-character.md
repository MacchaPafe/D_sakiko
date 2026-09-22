# ADR-0040：按角色显式授权 Story Event 检索

## 状态

已接受，2026-07-30。

本决策取代 ADR-0013 中“缺少 Character Thought 即缺少充分知情证据”的绝对规则，并取代 ADR-0034 中“命中 Thought 后总是展开完整关联 Story Event”的规则；其他决策继续有效。它同时补充 ADR-0032 的正式 Story Event、审核与索引投影契约。

## 背景

当前运行时直接检索 Character Thought，记忆工具也先命中 Thought 再展开关联 Story Event。角色亲历但没有形成长期观点的客观经历因此无法召回，例如爱音初遇灯时摔倒并收到企鹅创可贴。反过来，直接把无 Thought 链接的 Event 视为公开，或把 `participants` 视为完整知情，也可能把观众视角、他人心理和秘密原因泄露给角色。

## 决策

继续保持 Story Event、Character Thought、Character Relation、Lore Entry 四类正式条目，不增加第五类角色事件记忆。

Story Event v0 增加 `known_by_character_ids`：

- 字段是可以安全获得完整事件标题、摘要和参与者显示名的角色 ID 列表。
- 缺字段和空列表都表示没有角色可以直接检索完整 Event。
- 权限从 Event 自身 `visible_from` 起生效；第一版不保存逐角色获知时间。
- `participants` 不授予权限，非参与者也可以通过明确证据获得权限。
- Thought 链接只表达语义关联，不授予 Event 权限。
- Stage 2A 可以基于正面证据提出候选，Stage 3 人工审核决定最终列表。

运行时独立检索当前角色的 Thought 和获授权 Event：

- 直接上下文最多 3 条 Event 与 2 条 Thought，配额不可互借，Event 先于 Thought。
- 记忆工具最多 5 条唯一 Event 与 5 条 Thought，顶层分别返回 `events` 和 `thoughts`。
- 授权 Thought 关联 Event 继续作为补充召回，但展开前重新检查时间与 `known_by_character_ids`。
- 独立 Event 命中和关联展开在单次结果内按 Event UUID 合并去重。
- Thought 与 Event 使用独立阈值；单一来源失败时保留另一来源结果。

`known_by_character_ids` 进入审核基础、正式 JSON、Type Module semantic fields 和 Qdrant payload，但不进入 embedding 文本。Story Event 继续使用 `schema_version=0`，世界书索引结构版本提升到 4。

## 结果

没有 Character Thought 的可知事实能够被召回，同时 Thought 不能再越权暴露完整 Event。旧正式包与旧用户内容安全地 fail closed，需要人工补充授权后才会产生新的 Event 检索结果。编辑器应显示 Thought 反向链接作为非阻断提示，并允许一次性从参与者复制候选，但不得自动同步或自动授权。

正式发布前，现有发布 Event 必须由用户逐项审核知情列表，随后重新构建正式包和索引，并通过真实 E5 正例、跨角色泄漏负例和未来事件负例。
