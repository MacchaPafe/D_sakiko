# 从场景关系观察聚合角色关系状态

Stage 2 将单场景中的有向角色关系表现保存为离线、不可变的 `Relation Observation`，Stage 3 再跨场景聚合为带有效期的 `Character Relation State`，并仅把 State 写入现有 `character_relations` collection。相比让每个场景直接覆盖长期关系，这项分层增加了一次离线聚合，却避免短期情绪覆盖长期关系并减少运行时重复文档；具体采用全量还是增量聚合、如何生成内部语义键，属于可调整的实施策略，不由本 ADR 固定。
