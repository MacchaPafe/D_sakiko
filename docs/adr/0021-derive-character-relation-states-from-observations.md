# 从场景关系观察聚合角色关系状态

Stage 2 将单场景中的有向角色关系表现保存为离线、不可变的 `Relation Observation`，Stage 3 再跨场景聚合为带有效期的 `Character Relation State`，并仅把 State 写入现有 `character_relations` collection。相比让每个场景直接覆盖长期关系，这项分层增加了一次离线聚合，却避免短期情绪覆盖长期关系并减少运行时重复文档。

每个 Relation Type 保存不可编辑的 `generated_sequence` 和可空的 `reviewed_sequence`；第一次人工修改文本或结构时复制机器序列，之后只编辑人工序列，审核界面和发布优先使用人工序列。是否人工编辑由 `reviewed_sequence` 是否存在自动推导，不再手工维护 `human_edited`。

完整范围重聚合时，上一版已发布的 Relation Type 状态序列和经人工排除的 Observation 及其原因可以作为可推翻的 LLM 先验，经人工拒绝的错误候选默认不进入 LLM 上下文而只由确定性前处理按稳定来源 ID 提示。每个 Relation Type 序列对所覆盖 Observation 的稳定 ID 与规范化语义内容以及完整 `generated_sequence` 计算 `review_basis_sha256`。Observation 部分包括有向角色对、时间顺序、观察文本、说话提示、称呼、证据 ID 和证据强度；机器序列部分包括生成文本、分组结构、State 顺序、语义字段和时间边界。摘要不包含抽取置信度、风险提示、审核字段、`reviewed_sequence`、审核备注、正式 UUID 或 JSON 排版。

只有新旧摘要相同时才迁移稳定身份、人工序列与审核处置；摘要变化时只保留身份继承建议并重置为未审核，旧人工序列仅在审核界面只读展示供对照，人工重新确认后才能复制或修改为新的 `reviewed_sequence`。新证据可以推翻旧处置。

全量重生成默认在 `--output` 已存在时自动将其作为 previous review，也允许用 `--previous-review` 指定其他旧文件；只有显式 `--fresh` 才从零开始而不迁移。旧文件的产物类型、系列、时间线或覆盖范围不兼容时必须报错，不能静默忽略。实现必须先完整读取旧 Review，在内存中构建并校验全新的 Review，按上述规则迁移身份与人工审核数据，再写入同目录临时文件并用 `os.replace` 整体替换；任何步骤失败都保留旧文件不动，禁止原地逐字段更新。
