# 分别版本化世界书包与条目 Schema

世界书包格式、条目类型 Schema 和一次内容发布分别版本化：内容文件保存带稳定 UUID、`entry_type`、条目 Schema 版本和类型专属 `content` 的自描述条目，包 manifest 只描述身份、内容版本、显示信息、时间轴、依赖和带摘要的内容文件。每个历史 `(entry_type, schema_version)` adapter 负责迁移到该类型的当前模型，当前 Type Module 集中提供验证、编辑描述、嵌入文本和索引投影；未发布结构使用实验版本零，标注证据留在开发审核产物。相比固定三类顶层数组和一套全局 Schema，这允许各知识类型独立演进，但要求维护明确的版本 adapter。
