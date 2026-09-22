---
status: accepted
---

# ADR-0047：允许人工重新确认 Stage 3 来源指纹

## 背景

Stage 3 审核产物使用直接来源文件的完整 SHA-256 阻止过期数据进入正式世界书。该防护无法区分真正影响下游语义的修改和无关字段、JSON 排版或权限元数据变化。例如只修改 Story Event 的 `known_by_character_ids` 会使 Thought Review 与 Lore decisions 的整文件来源摘要过期，但该字段既不进入 Thought 聚合消费投影，也不影响 Lore 去重。

审核工作台的使用者是数据集审核者本人。来源过期应当形成明确的复核门槛，而不应无条件强迫重新调用 LLM。

## 决策

所有带 `direct_sources` 的 Stage 3 审核产物都支持“接受当前来源并保留审核结果”：

- 自动模式比较版本化、类型化的下游消费投影；投影完全相同时可以刷新来源指纹。
- 投影不同、缺少基线或投影版本不兼容时，审核者可以填写说明并显式人工接受。
- 两种模式都只更新 artifact 的 `direct_sources`，不修改候选内容、人工快照或审核处置，也不调用 LLM。
- 来源缺失、无法解析、episode coverage 错误或 Thought/Lore 出现悬空候选引用时禁止接受。
- 确认时重新检查 artifact、build spec 和全部来源摘要，拒绝使用已经过时的差异预览。

消费投影复用真实生成流水线的规范化逻辑：

- Story/Lore Review 使用解析后的 Stage 2 Input 与 Stage 2A annotation。
- Relation 使用规范化后的关系观察和问题。
- Thought 使用 Thought updates、Event facts、Event 候选 ID、来源 ID、标题、摘要与处置；不包含 `known_by_character_ids`。
- Lore decisions 只使用已发布 Lore 的候选 ID 和有效文档，不受 Story Event 变化影响。

工作台为 fresh artifact 尽力建立本地投影基线。基线和完整接受历史保存在并排的 `*.source-acceptances.json` sidecar 中；sidecar 不属于正式构建输入、不纳入 Git，缺失或损坏只会禁用自动判等。artifact 中的 `direct_sources` 始终是发布器使用的权威状态。

GUI 与 `revalidate-stage3-sources` CLI 调用同一工作区操作。人工覆盖需要确认说明；CLI 额外提供 `--force`、`--reason` 与 `--dry-run`。接受成功后立即重新加载工作台并运行全包构建审计；其他产物仍过期不会回滚本次确认。

## 结果

来源完整 SHA-256 仍是正式构建的严格门槛，`publish-worldbook` 不增加 `--allow-stale`。区别在于审核者可以通过一个有差异预览、结构校验和本地记录的显式操作更新权威来源契约，而不再需要手工编辑 SHA 或对无语义变化重复调用 LLM。

本决策补充并部分取代 ADR-0032 中“即使确认变化无影响也必须重新生成”的规则。重生成仍是输入确实影响下游内容时的默认选择。
