# RAG 标注、审核与世界书发布实现计划

## 1. 目标

按照 ADR 0021、0022、0031、0032，把现有逐集 Stage 3 和直接入库流程改造成以下稳定链路：

1. Stage 1、Stage 2A、Stage 2B 继续生成逐集局部证据。
2. 每集 RAG artifact 只承载 Story Event 与 Lore Entry 候选。
3. Stage 3 Relation 和 Thought 分别在完整集数范围内聚合长期状态序列。
4. 四类候选经过统一、可迁移的人工审核。
5. 发布器从全部审核产物确定性构建正式世界书包，先审计 staging，再替换正式包。
6. 正式包作为权威来源；Qdrant 只从正式包和用户状态全量重建。

本计划是编码前的实施基线。未经明确批准，不进入代码实现阶段。

## 2. 本轮范围

### 包含

- Story Event、Lore Entry 的稳定候选身份和统一审核模型。
- Relation Observation 到跨集 Relation Type 状态序列的全量聚合与审核。
- Thought Update 到跨集 Thought Thread 状态序列的全量聚合与审核。
- Lore 去重决策的新鲜度、迁移与审核。
- 来源 SHA-256、`review_basis_sha256`、自动审核迁移和删除确认。
- 开发侧 ID map、正式 UUID 分配和 active/inactive/retired 状态。
- 单包与批量世界书构建、审计、发布和全量索引重建。
- Character Thought 正式 Type Module 和第四张世界书 collection。
- 一次性 Story/Lore 旧产物升级命令。
- CLI、审核界面、说明文件和自动化测试。

### 不包含

- 修改 Stage 2B 的逐场景抽取语义。
- 为超过模型上下文设计窗口或分块聚合 fallback。
- 迁移旧逐集 Thought Review 或旧逐集 Relation 产物。
- 跨世界书包文本相似度检测或自动去重。
- Event Fact 正式发布或运行时 Event Fact 引用。
- 跨包 Story Event 引用。
- 孤立 Override 的高级管理或自动删除。
- `entry_migrations`、身份沿革提示或持久迁移决策日志。
- 世界书热发布、备份、回滚、发布锁和索引写入后的二次全量扫描。
- 当前应用的 RAG 初始化或 readiness 到检索入口的接线；待数据库实际接入时另做。
- 将四类 entry 的 `schema_version` 冻结为 v1；本轮保持实验 v0。

## 3. 实施原则

- 先稳定数据契约，再修改生成器和编辑器，最后替换发布器。
- 生成、人工审核、正式发布三层模型分开，不复用一个“万能 document”。
- LLM 只生成机器候选和非权威建议；最终审核状态与发布处置只能由人工审核工具写入。
- 所有生成命令先在内存完成新产物、迁移和校验，再通过同目录临时文件与 `os.replace` 替换。
- 正式包采用固定排序和固定 JSON 编码；相同输入与 ID map 必须产生字节一致的输出。
- 新 Python 模块和测试遵守项目 Python 规范：完整类型标注、不使用 `Any`、中文 docstring、修改后执行语法检查。

## 4. 目标模块边界

### 标注侧

- 保留 `pipeline/schemas.py` 中 Stage 1、Stage 2A、Stage 2B 模型。
- 新建聚焦 Stage 3 的审核模型模块，避免继续扩大 `schemas.py`：
  - `pipeline/review_models.py`：统一审核状态、处置、原因、来源指纹和迁移报告。
  - `pipeline/stage3_document_models.py`：Story/Lore 机器文档与人工文档候选。
  - `pipeline/stage3_relation_models.py`：Observation、Relation Type、Relation State 序列。
  - `pipeline/stage3_thought_models.py`：Update、Thread、Thought State 序列。
- 新建 `pipeline/review_migration.py`，集中实现 basis、previous review 匹配、消失候选检查和安全替换。
- 现有 Stage 3 模块只负责各自业务转换，不自行重复实现审核迁移规则。

### 世界书侧

- 扩展 `worldbook/models.py`：第四类 entry、依赖约束和正式 manifest 契约。
- 新增 `worldbook/entry_types/character_thought.py`，并更新默认 registry。
- 新建：
  - `worldbook/build_models.py`：单包／批量配置、ID map、构建报告。
  - `worldbook/identity_map.py`：UUID 分配、状态转换和只读 provisional 投影。
  - `worldbook/builder.py`：读取审核产物并构建确定性 staging。
  - `worldbook/build_audit.py`：输入、新鲜度、审核门槛、跨引用和全局包集合审计。
- 重写现有 `worldbook/publisher.py` 为薄编排层；不再直接解析旧单一 artifact。
- 继续复用 `package_loader.py`、`effective_entries.py`、`sync.py`、`worker.py` 和索引抽象，但按新契约补齐能力。

## 5. 关键数据契约矩阵

| 产物 | 人工审核单位 | 直接来源摘要 | 开发侧稳定身份 | 人工替换 |
| --- | --- | --- | --- | --- |
| Story Event | 单条候选 | 本集 Stage 2 Input + Stage 2A | `story_candidate:<uuidv4>` | `reviewed_document` |
| Lore Entry | 单条候选 | 本集 Stage 2 Input + Stage 2A | `lore_candidate:<uuidv4>` | `reviewed_document` |
| Relation Review | 完整 Relation Type 序列 | 全部集的 Stage 2 Input + Stage 2A | `relation_type:<uuidv4>`、`relation_state:<uuidv4>` | `reviewed_sequence` |
| Thought Review | 完整 Thought Thread 序列 | 全部集的 Stage 2 Input + Stage 2B + RAG artifact | `thought_thread:<uuidv4>`、`thought_state:<uuidv4>` | `reviewed_sequence` |
| Lore decisions | 一个重复组决策 | 全部参与的 RAG artifact | 组成员 candidate ID | 决策内人工文档 |

`review_basis_sha256` 分别使用以下规范化内容：

- Story/Lore：`source_scene_id`、`source_local_id`、排序去重的证据 ID 和完整 `generated_document`。
- Relation：覆盖的 Observation 稳定 ID、规范化 Observation 语义和完整 `generated_sequence`。
- Thought：覆盖的 Update 稳定 ID、规范化 Update 语义和完整 `generated_sequence`。
- Lore group：成员 candidate ID、完整机器文档和规范化标题。

正式投影必须使用字段白名单：

- Story Event：现有正式剧情字段、有效期、标签和检索文本。
- Lore Entry：现有正式适用范围、正文、有效期、标签和检索文本。
- Character Relation：有向角色对、series/timeline/branch、Relation Type UUID、`state_summary`、说话提示、称呼、有效期、标签和检索文本；不再发布 `relation_label`。
- Character Thought：角色、series/timeline/branch、Thought Thread UUID、规范化 Subject、Thought Aspect、自包含观点、Epistemic Status、有效期、可选同包 Story Event UUID、标签和检索文本。

正式包不包含证据、confidence、risk、审核字段、开发侧稳定 ID、Event Fact、Transition 或身份迁移信息。

## 6. 分阶段实施

### 阶段 A：统一审核基础设施

1. 定义公共枚举和校验：
   - `review_status`: `unreviewed | needs_followup | completed`。
   - `disposition`: `publish | reject | exclude | null`。
   - `suggested_disposition`、结构化原因代码、可空说明和 `review_notes`。
2. 定义按逻辑角色和 episode 保存的直接来源指纹，不把绝对路径作为身份。
3. 实现规范化 JSON 和四类 `review_basis_sha256` 计算入口。
4. 实现通用审核状态转换：
   - 内容或结构修改自动清空最终处置并重置未审核。
   - 修改备注不重置审核。
   - 恢复机器版本删除人工快照并重置审核。
5. 实现 previous review 迁移框架：
   - basis 相同才迁移人工快照、处置和备注。
   - basis 不同只留下旧内容只读参考和身份继承建议。
   - 不兼容范围或产物类型直接报错。
6. 实现 `<output>.migration-report.json`、`--previous-review`、默认同路径 previous、`--fresh`、`--allow-removed-id` 和 `--allow-all-removed`。
7. 更新 `.gitignore` 忽略 migration report 和 `.build/` 派生内容。

验收：公共状态机、basis 稳定性、安全替换、迁移、消失候选阻断和放行均有纯单元测试。

### 阶段 B：Story Event 与 Lore Entry 逐集产物

1. 调整 `normalize-stage3-rag`：
   - 输出只包含 Story Event 和 Lore Entry。
   - 不再从逐集产物发布 Character Relation。
   - 每条候选获得 `story_candidate:<uuidv4>` 或 `lore_candidate:<uuidv4>`。
2. 把旧 `document` 拆成 `generated_document` 与可空 `reviewed_document`。
3. 加入统一审核字段、basis、风险信息和直接来源摘要。
4. 来源完全一致时确定性继承 candidate ID；来源重切分时生成身份继承建议并进入 `needs_followup`。
5. 改造 `stage3_dataset_editor.py`：
   - 分别审核 Story 与 Lore。
   - 支持 publish/reject/exclude、原因、备注、恢复机器版本和批量确认。
   - 展示旧人工文档、证据和身份建议。
6. 保留风险和 confidence 作为排序信息，不让它们自动批准或进入正式投影。

验收：重新规范化同一输入可完整迁移审核；更改机器文档会保留 candidate identity 建议但撤销审批。

### 阶段 C：Relation 跨集聚合与审核

1. 保留 Stage 2A Relation Observation 为不可变局部证据。
2. 重构 `stage3_relation_aggregation.py`：
   - 重复接收完整集数范围内的 Stage 2 Input 与 Stage 2A annotation。
   - 校验 series/timeline/branch 和 episode coverage。
   - 每个有向角色对形成一个 LLM 任务。
   - LLM 建议 Relation Type 创建、复用、拆分、合并和不合并 Observation。
3. 生成新的 Relation Review：
   - `relation_type:<uuidv4>` 和每个 `relation_state:<uuidv4>`。
   - `generated_sequence`、可空 `reviewed_sequence`、完整 Observation coverage。
   - Relation Type 级审核；内部 State 不单独存审核状态。
4. 删除 LLM 生成 snake_case `relation_type_key` 的权威语义；正式 Relation Type UUID 由发布时 ID map 映射。
5. 实现序列迁移、拆分／合并身份确认和 State 身份继承建议。
6. 新增或重构 Relation 审核器：
   - 并排显示 Observation、机器序列、旧人工序列和当前人工序列。
   - 支持 State 文本、时间、顺序、增删拆合及 Observation 归属调整。
   - 未归并 Observation 只能人工 reject/exclude；要发布必须归入 Type。
7. 正式 Relation 文档移除 `relation_label`，以 `state_summary` 为展示和检索内容。

验收：每个 Observation 恰好被一个完成审核的 Type 覆盖，或单独完成 reject/exclude；重叠区间、空摘要、身份未决均阻断完成审核。

### 阶段 D：Thought 跨集聚合与审核

1. 用跨集聚合替换当前逐集 `stage3_thought_pipeline.py` 的静态主题键时间线：
   - 每个角色读取完整 episode 范围的 Stage 2 Input、Stage 2B 和相关 RAG artifact。
   - 确定性前处理先解析精确本地 Event/Fact 引用并整理所有 Update。
   - 一个角色一个 LLM 任务，不实现窗口 fallback。
2. 新 Prompt 和 response Schema 在一次响应中完成：
   - 剩余引用链接。
   - 规范化 Thought Subject 与 Thought Aspect。
   - Thread 创建、复用、拆分或合并。
   - acquired/reaffirmed/revised/retracted Transition。
   - 自包含 Thought State 文本。
   - `suggested_disposition` 和排除理由建议。
3. 确定性后处理：
   - 校验每个 Update 恰好为 threaded、unresolved 或 excluded。
   - 同一 Thread 任一时间最多一个有效 State。
   - reaffirmed 不新建 State；retracted 只关闭旧 State；revised 才建立后继 State。
   - 计算有效期、风险、basis 和来源摘要。
4. 新 Thought Review 使用 `thought_thread:<uuidv4>`、`thought_state:<uuidv4>`、`generated_sequence` 和可空 `reviewed_sequence`。
5. 上一版全量 Review 可作为可推翻先验；旧逐集 Thought Review 明确拒绝作为 previous。
6. 重构 Thought 审核器：
   - Thread 为审核和编辑单位。
   - 支持 Update 归属、Thread 拆合、State 编辑、Story Event 引用和 Epistemic Status 修改。
   - unresolved Update 必须归线或人工 reject/exclude。
7. 更新 Prompt Package 命令和指南，将“unresolved 单条链接”改为“按角色全量 Thought 聚合”。

验收：01–13 集范围能够生成唯一全量 Thought Review；角色跨集同一长期观点形成一条 Thread 序列，不再因单集边界产生无限期冲突状态。

### 阶段 E：Lore 去重决策

1. 把现有 dataclass/自由 JSON Lore review 改为严格 Pydantic artifact。
2. 为重复组保存成员 candidate ID、完整机器文档、规范化标题和 `review_basis_sha256`。
3. 支持 `auto_merge_identical`、`keep_separate`、`merge`、`drop`，并按 ADR 实施审核门槛。
4. 接入统一 previous review 迁移、安全替换和 migration report。
5. 在审核界面中完成组决策，不再生成额外的“去重后审核文件”。

验收：成员或机器文档变化会撤销旧组决策；完全相同且来源已审核的组可机械合并。

### 阶段 F：审核工具统一收口

1. 为四类审核器提供稳定 CLI 入口，供构建错误直接生成可复制命令。
2. 共享以下无 UI 业务函数并优先测试这些函数：
   - 创建人工快照。
   - 编辑后重置审批。
   - 完成审核与处置校验。
   - reject/exclude 原因校验。
   - 恢复机器版本。
   - 批量完成确认。
   - 身份继承确认。
3. UI 保持按风险、状态和身份待确认过滤；不增加 reviewed_by/reviewed_at。
4. 保存前重新执行完整 artifact 校验，禁止 UI 写出半合法 JSON。

验收：构建器报告的每一种“未审核”错误都对应一个实际存在、参数正确的审核命令。

### 阶段 G：正式世界书 Schema 与运行时模块

1. `EntryType` 增加 `character_thought`，保持四类 `schema_version=0`。
2. 更新正式 Relation v0 字段，新增正式 Thought v0 字段和 Type Module。
3. registry、loader、display、embedding projection、payload 和 Qdrant collection 全部支持第四类 entry。
4. manifest 依赖解析支持显式 SemVer 比较器、版本满足检查、传递闭包、反向依赖破坏和循环检测。
5. 修正用户状态合并：
   - 孤立 Override/tombstone 保留但不显示、不索引、不产生 degraded issue。
   - 不兼容 Override/Extension 只隔离自身并产生 degraded。
   - UUID 恢复后旧用户状态自然重新适用。
6. 不改应用启动初始化或实际 RAG 查询门槛，只保证底层模型、loader 和 worker 已具备未来接入能力。

验收：四类官方 entry 和用户状态可通过内存索引测试；依赖闭包和 Override 边界均有测试。

### 阶段 H：构建配置、ID map 与纯审计

1. 定义并校验版本化 `worldbook_build.json`：
   - 包元数据、输出位置、构建报告路径、依赖声明。
   - 预期连续 episode 范围。
   - 每集 Stage 2 Input、Stage 2A、Stage 2B、RAG artifact 路径。
   - 全量 Relation Review、Thought Review、Lore decisions 和 ID map。
   - 所有路径相对配置文件解析。
2. 定义批量配置，只引用多份单包 build spec。
3. 实现结构化 ID map：
   - namespaced identity key、正式 UUID、active/inactive/retired。
   - entry、Thought Thread、Relation Type UUID 全局不可碰撞。
4. 实现 `validate-worldbook-build --build-spec`：
   - 全程只读，不修改 ID map、正式包或索引。
   - 新身份使用进程内 UUIDv5 provisional 投影，不输出具体 provisional UUID。
   - 校验 coverage、scope、直接来源 SHA、审核完成度、身份歧义和跨引用。
   - 构建临时 staging，并用正式 loader、Type Module 和全局包集合审计。
5. 实现正式投影：
   - Story/Lore 使用最终 document。
   - Relation/Thought 把每个 State 投影成独立 entry。
   - Thought Event 引用转换为当前包内正式 Story Event UUID。
   - 不把证据、风险、审核、Event Fact 或迁移信息写入正式包。
6. 固定条目、依赖、content file 和 JSON 输出顺序，加入重复构建字节一致性测试。

验收：纯 validate 在全新 ID map 下仍能完成全部 Schema 和引用审计，运行前后版本管理文件无变化。

### 阶段 I：单包与批量发布

1. 用 `publish-worldbook --build-spec` 替换旧长参数 CLI；不保留旧未正式使用的数据格式兼容路径。
2. 单包发布顺序：
   - 读取并校验全部输入。
   - 通过审核门槛。
   - 为新身份分配 UUIDv4，并安全持久化 ID map。
   - 构建 staging。
   - 审计 staging 与完整预期官方包集合。
   - 应用删除／恢复许可并写入 ID map 状态转换。
   - 整体替换正式包目录。
   - 调用 worker 全量重建四张 collection。
3. 支持 `--reactivate-id`、`--reactivate-all`、删除确认参数及 ADR 规定的限制。
4. 批量发布：
   - 先构建所有 staging。
   - 对 staging 加未更新正式包执行一次全局审计。
   - 任一失败时不替换任何正式包。
   - 全部通过后依次替换，最后只重建一次索引。
5. 不实现备份、回滚、热发布或跨包替换事务；中断后通过重跑恢复。
6. `.build/build-report.json` 在所有成功和失败路径覆盖写入；记录输入摘要、正式 ID、状态变化、Git HEAD 和结果，但不复制完整内容。
7. 退出语义：
   - `ready` 和带隔离项的 `degraded` 返回成功。
   - `unavailable` 或 worker 异常返回非零。
   - 始终独立输出 `package_published`、`index_rebuilt`、`index_readiness`。
   - 索引失败不回滚正式 JSON，并输出可复制的 worker rebuild 命令。

验收：故障注入覆盖审核失败、ID map 写入后 staging 失败、替换失败、部分批量替换和索引失败；每种结果符合 ADR 的持久化边界。

### 阶段 J：一次性旧数据升级

1. 实现幂等 `upgrade-stage3-review-schema`，默认 dry-run，只有 `--apply` 修改文件。
2. 只转换现有 Story/Lore RAG 和旧 ID map：
   - 生成 candidate ID。
   - 旧 `point_id` 保存为开发侧 `legacy_source_id`。
   - 已存在正式 UUID 映射到新的 candidate identity key。
3. 不读取旧逐集 Relation/Thought 作为迁移来源；它们由全量聚合重新生成。
4. apply 前重新扫描并校验；使用安全替换。
5. 不生成 decisions 文件或持久迁移过程报告；升级后的 artifact 和 ID map 即最终结果。

验收：重复 apply 不改变 candidate ID 或正式 UUID；一对多歧义在修改前阻断。

### 阶段 K：文档与真实数据验收

1. 更新 `pipeline/command.md` 和 annotation guides，移除旧逐集 Relation/Thought 发布说明。
2. 给出 MyGO 01–13 的推荐顺序：
   - 完成全部逐集 Stage 1/2A/2B。
   - 逐集生成并审核 Story/Lore RAG。
   - 全量生成并审核 Relation Review。
   - 全量生成并审核 Thought Review。
   - 生成并审核 Lore decisions。
   - 运行 validate。
   - 运行正式 publish。
3. 提供单包、批量、重生成迁移、删除确认、恢复 inactive UUID 和索引失败重试示例。
4. 用 01–13 真实产物做一次完整演练；修复所有 Schema、coverage、引用和可操作诊断问题。
5. 演练完成后再决定是否将各 entry schema 冻结为 v1，本轮不自动升级。

验收：从空 `.build` 和可重建索引开始，仅依赖版本管理内的 build spec、审核产物、ID map 与正式包即可重复 validate/publish。

## 7. 测试策略

### 单元测试

- Pydantic Schema、审核状态机、reason code、basis 和来源 SHA。
- candidate/thread/type/state ID 前缀与 UUID 格式。
- previous review 迁移、identity suggestion、删除阻断与放行。
- Thread/Relation 序列时间边界和 coverage。
- ID map 命名空间、碰撞、状态转换和 reactivation。
- SemVer 比较器和依赖闭包。
- 正式投影字段白名单与确定性排序。

### 集成测试

- 使用 fake LLM 的多集 Relation 和按角色 Thought Prompt Package。
- 四类审核器保存、恢复机器版本和编辑后撤销审批。
- build spec 到 staging、正式 loader、跨引用和全局依赖审计。
- 单包／批量发布的临时目录和故障注入。
- 四张内存索引 collection；真实 embedding 测试继续作为可选慢测试。

### 回归测试

- 保持 Stage 1、Stage 2A、Stage 2B 现有测试通过。
- 保持官方包加用户 Override/tombstone/extension 的有效内容语义。
- 确认世界书 rebuild 只删除世界书拥有的四张 collection。
- 两次相同构建输出逐文件 SHA-256 完全相同。

## 8. 推荐提交顺序

1. 审核公共模型与迁移基础。
2. Story/Lore 新 artifact 与审核器。
3. Relation 全量序列与审核器。
4. Thought 全量序列与审核器。
5. Lore decisions。
6. 四类正式世界书 Schema、loader 与索引。
7. build spec、ID map、builder 和 validate。
8. 单包／批量 publisher 与 rebuild 编排。
9. 一次性旧数据升级和 01–13 真实产物转换。
10. command/guide 更新与端到端验收。

每个提交必须保持测试可运行；不要在前一层数据契约尚未稳定时同时改 UI、publisher 和真实标注数据。

## 9. 完成定义

满足以下条件后，整项实现才算完成：

- 旧逐集 Thought 与 Relation 不再参与正常发布。
- 四类候选都使用统一审核状态、处置和 copy-on-write 人工快照。
- 重生成能够安全迁移未变化的审核，并对变化或删除给出可操作报告。
- 所有 Relation Observation 和 Thought Update 都有唯一终态覆盖。
- validate 是严格只读的；publish 只在完整审计成功后替换正式包。
- 正式包仅含白名单字段、正式 UUID 和确定性 JSON。
- 单包与批量依赖审计、UUID 唯一性和跨引用校验完整。
- 正式发布后从全部有效包和用户状态全量重建四张 collection。
- 索引失败不回滚正式包，并具有明确重试路径。
- MyGO 01–13 能按文档从审核产物重复构建并发布。
