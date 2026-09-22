---
status: proposed
---

# 跨集聚合长期 Character Thought

Stage 2B 继续逐场景提取局部 Character Thought Update，Stage 3 则汇总同一 `series_id`、剧情时间轴和剧情分支内的完整集数范围，并生成唯一的长期 Character Thought 审核产物；跨作品延续不属于第一版范围。Thought Thread 由角色、规范化语义主题和认知方面识别，可以跨越具体 Story Event 或 Event Fact 引用，且任一剧情时间最多有一个有效状态。

Stage 3 的确定性前处理负责隔离范围和精确本地引用链接，每个受约束的 LLM 任务按角色查看完整标注范围内的全部 Update，并在一次 response 中完成剩余引用链接、Thread 创建、合并或拆分、Transition 判断、自包含状态文本生成，以及对不应形成长期观点的候选作有理由的排除；每条 Update 必须恰好归为 threaded、unresolved 或 excluded，纯撤回只关闭旧状态，只有新的认知立场才建立后继状态。不按候选组隐藏 Update，也不为上下文长度设计窗口 fallback，旧的逐条 unresolved Thought Link 不再作为正常标注阶段。

确定性后处理负责候选链接约束、完整性校验、稳定 ID、证据合并和数值有效时间投影；人工复核以完整 Thread 的分组、Transition 和状态序列为主要审核单位，结构调整后重新投影有效期。Thread 保存不可编辑的 `generated_sequence` 和可空的 `reviewed_sequence`；第一次人工修改文本或结构时复制机器序列，之后只编辑人工序列，审核界面和发布优先使用人工序列。是否人工编辑由 `reviewed_sequence` 是否存在自动推导，不再手工维护 `human_edited`。

新增集数后，正式产物基于完整标注范围全量重建，并把上一版全量 Stage 3 Review 中已经审核的 Thread Catalog 作为可修正而非强制的 LLM 先验。上一版发布的 Thread 与被排除 Update 及其原因可以进入上下文，被拒绝的错误候选默认不进入 LLM 上下文而只由确定性前处理按稳定来源 ID 提示；新跨集证据可以推翻任何旧处置。

每个 Thread 对所覆盖 Update 的稳定 ID 与规范化语义内容以及完整 `generated_sequence` 计算 `review_basis_sha256`。Update 部分包括角色、时间顺序、Subject 与链接、观点文本、暂定变化类型、Epistemic Status、证据 ID 和证据强度；机器序列部分包括生成文本、分组结构、State 顺序与时间边界、Transition、Epistemic Status 和链接目标。摘要不包含抽取置信度、风险提示、审核字段、`reviewed_sequence`、审核备注、正式 UUID 或 JSON 排版。只有新旧摘要相同时，后处理才迁移稳定身份、人工序列和审核处置；摘要变化时只保留身份继承建议并重置为未审核，旧人工序列仅在审核界面只读展示供对照，人工重新确认后才能复制或修改为新的 `reviewed_sequence`。旧单集 `epXX_thoughts_review` 不作为迁移来源，内部仍可缓存和复用未变化的其他中间结果。

全量重生成默认在 `--output` 已存在时自动将其作为 previous review，也允许用 `--previous-review` 指定其他旧文件；只有显式 `--fresh` 才从零开始而不迁移。旧文件的产物类型、系列、时间线或覆盖范围不兼容时必须报错，不能静默忽略；旧单集 Thought Review 不能作为新全量 Review 的迁移来源。实现必须先完整读取旧 Review，在内存中构建并校验全新的 Review，按上述规则迁移身份与人工审核数据，再写入同目录临时文件并用 `os.replace` 整体替换；任何步骤失败都保留旧文件不动，禁止原地逐字段更新。
