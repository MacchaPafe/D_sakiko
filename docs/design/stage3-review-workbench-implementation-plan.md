# Stage 3 审核工作台实现计划

## 1. 目标与边界

实现一个开发侧 NiceGUI“Stage 3 审核工作台”，以单个 `worldbook_build.json` 为正常入口，在一个世界书包范围内完成 Story Event、Lore Entry、Character Relation、Character Thought 和 Lore 去重的人工审核。

工作台负责：

- 加载并展示构建配置引用的全部审核产物与直接证据。
- 编辑人工完整快照，处理审核状态、最终处置、原因与备注。
- 处理 Relation/Thought 的跨集结构调整与开发侧身份继承。
- 显式重生成来源已过期的下游产物。
- 运行只读世界书构建审计，并把错误定位回文件和审核项。

工作台不负责：

- Stage 1/Stage 2 原始标注编辑；继续使用既有编辑器和 Prompt Package 流程。
- 凭空创建没有机器候选的 Story Event 或 Lore Entry；漏标回到上游修正。
- 跨角色、跨有向角色对或跨世界范围移动 Relation/Thought 数据。
- 正式发布世界书包；继续使用 `publish-worldbook(s)` CLI。
- 编辑运行时官方世界书、用户 Override 或 Qdrant 索引。

## 2. 已确定的工作流

1. 使用 `worldbook_build.json` 打开工作台；单 artifact 模式只保留为开发调试入口。
2. 每个审核文件拥有独立内存草稿、脏状态和撤销/重做栈。
3. 普通保存只保存当前 artifact；“保存全部”先校验全部脏 artifact，再逐文件安全替换。
4. 保存上游文件后，只标记直接下游来源过期，不自动重生成。
5. 用户显式触发重生成；执行前展示命令、输入与预计影响，执行后展示迁移报告。
6. 工作台可以运行 `validate-worldbook-build` 等价的只读审计，但不能正式发布。
7. 每个审核单位逐项处理；允许对显式勾选且无阻断问题的项目执行“批量审核通过”。
8. 正式发布要求所有审核单位到达终态；不存在发布时选择部分候选的流程。

## 3. 现有命令与界面能力映射

| 现有命令 | 工作台能力 | 处理方式 |
| --- | --- | --- |
| `review-stage3-item` | 审核通过、拒绝、排除 | 共享统一审核操作；GUI 不调用子进程 |
| `edit-stage3-item` | 编辑 Story/Lore 文档及 Relation/Thought 完整人工快照 | 扩展后端模型；CLI 作为文件参数适配器 |
| `note-stage3-item` | 编辑多行审核备注 | 不撤销审批 |
| `resolve-stage3-identity` | 新旧候选对比、继承或新建身份 | 集成到详情页；扩展序列与 State 身份处理 |
| `review-lore-decision` | Lore 重复组的保留、合并或丢弃 | 改为并排候选对比和合并文档表单 |
| `normalize-stage3-rag` | 重新生成逐集 Story/Lore Review | 后台任务；展示迁移摘要后重新加载 |
| Relation render/assemble | 重新生成全量 Relation Review | 分成生成 Prompt Package、等待 responses、组装三个可见步骤 |
| Thought render/assemble | 重新生成全量 Thought Review | 分成生成 Prompt Package、等待 responses、组装三个可见步骤 |
| `build-stage3-lore-decisions` | 重新扫描并生成 Lore decisions | 后台确定性任务 |
| `validate-worldbook-build` | 构建就绪审计 | 调用现有 Python 函数并展示结构化报告 |
| `publish-worldbook(s)` | 无 | 保持 CLI，不进入工作台 |
| `upgrade-stage3-review-schema` | 无 | 一次性维护命令，不图形化 |

CLI 继续作为自动化、故障恢复和构建错误中的可复制入口；GUI 与 CLI 必须调用同一无界面审核模块，不能各自实现状态转换或校验。

## 4. 可查看与可编辑字段

### 4.1 所有普通审核单位

共同只读信息：

- 开发侧稳定 ID、artifact 路径与内容类型。
- `generated_*` 机器基准、`previous_reviewed_*` 上一版人工结果。
- `review_basis_sha256`、直接来源状态、风险等级、风险原因和模型建议。
- 来源 scene/local ID、证据 ID 和相关上下文。

共同可编辑信息：

- `review_notes`。
- 审核动作：审核通过、拒绝、排除、标记待跟进。
- `reject/exclude` 对应的合法结构化原因和可选说明。
- 恢复机器版本；删除人工快照并撤销旧审批。
- 存在建议时处理开发侧身份继承。

任何参与正式投影的内容修改均创建或更新完整人工快照，并重置为 `unreviewed + disposition=null`。普通备注、列表过滤、折叠和排序不撤销审批。

### 4.2 Story Event

表单编辑完整 `StoryEventPayload`：

- `timeline_id`、`occurred_story_year`、`series_id`、`episode`。
- `time_order`、`visible_from`、`visible_to`、`canon_branch`。
- `title`、`summary`、`participants`、`importance`、`tags`、`retrieval_text`。

右侧证据区按 `evidence_u_ids/evidence_s_ids` 展示 Stage 2 字幕、画面文本和同场景上下文。包范围字段可编辑，但与 build spec 不一致时立即显示校验错误，不能保存为已审核完成状态。

### 4.3 Lore Entry

表单编辑完整 `LoreEntryPayload`：

- `scope_type`、`series_ids`、`timeline_id`、`canon_branch`。
- `applicable_story_years`、`visible_from`、`visible_to`。
- `title`、`content`、`retrieval_text`、`tags`。

证据区与 Story Event 相同；同时显示其是否参与 Lore 重复组以及当前组决策。

### 4.4 Character Relation

只读范围：

- `relation_type_id`、主客体、series、timeline、branch。
- 原始 Observation 内容、证据和机器生成内容。

人工完整内容可编辑：

- `semantic_label`。
- State 顺序。
- 每个 State 的 `supporting_observation_ids`、`state_summary`、`speech_hint`、`object_character_nickname`、`visible_from`、`visible_to`、`tags`、`retrieval_text`。
- State 新增、删除、拆分、合并及稳定身份处理。

同一有向角色对内允许：

- 在 Relation Type 之间移动 Observation。
- 把未归属 Observation 归入既有或新拆分的 Type。
- 拆分或合并 Relation Type，并显式选择 Type/State 身份继承。

未归属 Observation 如果不归入 Type，只能逐项拒绝或排除，不能发布为独立 Relation。

### 4.5 Character Thought

只读范围：

- `thought_thread_id`、角色、series、timeline、branch。
- 原始 Update、Transition、Event Fact、证据和机器生成内容。

人工完整内容可编辑：

- `canonical_subject`、`thought_aspect`。
- State 顺序。
- 每个 State 的 `supporting_update_ids`、`thought_text`、`epistemic_status`、`visible_from`、`visible_to`、Story Event/Event Fact 引用、`tags`、`retrieval_text`。
- State 新增、删除、拆分、合并及稳定身份处理。

同一角色内允许：

- 在 Thought Thread 之间移动 Update。
- 把未归属 Update 归入既有或新拆分的 Thread。
- 拆分或合并 Thread，并显式选择 Thread/State 身份继承。

未归属 Update 如果不归入 Thread，只能逐项拒绝或排除，不能直接发布。`retracted` 仍只结束旧状态，不创建正式 State。

### 4.6 Lore 去重决策

每个重复组展示：

- 组内全部候选的有效文档、集数、来源证据、审核状态和稳定 ID。
- 机器完全相同组的只读 `auto_merge_identical` 结果。
- 上一版决策和当前 `decision_basis_sha256` 是否一致。

人工操作：

- `keep_separate`：保留全部已审核候选。
- `merge`：选择主候选；默认复制其有效文档为合并草稿，并允许编辑完整 Lore payload。
- `drop`：丢弃整个重复组。
- 编辑 `review_notes`，或清除人工决定回到 `pending`。

## 5. UI 结构

### 5.1 顶部工作区栏

- 世界书显示名、package ID/version、build spec 路径。
- 当前文件脏状态、所有脏文件数量、保存当前、保存全部、撤销、重做。
- “重新生成受影响产物”和“运行构建审计”。
- 缺失文件、来源过期、外部文件变化和审计就绪状态。

### 5.2 左侧导航

第一层按审核域分栏：

- 总览。
- Story Events（按 episode 分组）。
- Lore Entries（按 episode 分组）。
- Relations（按主客体分组）。
- Thoughts（按角色分组）。
- Lore 去重。
- 问题与审计报告。

第二层是统一审核队列，支持：

- 文本、稳定 ID、episode、角色或角色对搜索。
- `unreviewed/needs_followup/completed`、处置、风险、身份待确认、来源过期、人工已编辑过滤。
- 风险优先、剧情顺序、ID、审核状态排序。
- 显式多选与“批量审核通过”；不能对仅因筛选而可见的全部项目隐式执行。

### 5.3 中央编辑区

- 顶部显示稳定 ID、审核状态、最终处置、风险和身份警告。
- 默认显示“有效内容”表单；首次编辑时自动从机器基准创建完整人工快照。
- Relation/Thought 用可排序 State 卡片；结构操作通过明确按钮和确认对话框完成。
- 内容错误在字段附近显示；整项错误在页首汇总。

### 5.4 右侧证据与对比区

- 机器基准、上一版人工结果与当前草稿三者可切换或并排比较。
- 展示对应字幕、场景上下文、Observation/Update、Transition、Event Fact 和 Story Event 引用。
- 身份建议显示新旧内容、证据重叠、建议理由与置信度，再选择继承或新建。
- Lore 去重使用专门的多候选并排视图，不压缩成普通单记录表单。

### 5.5 审核操作区

- “审核通过（纳入世界书）”。
- “拒绝候选”和“排除长期知识”；根据处置动态限制原因选项。
- “标记待跟进”、审核备注和恢复机器版本。
- 完成处置前再次显示内容类型、稳定 ID 和是否存在人工修改。

## 6. 数据模型调整

当前 Relation/Thought 只把状态数组放在 `reviewed_sequence`，无法让 `semantic_label`、`canonical_subject` 和 `thought_aspect` 遵循完整快照语义。重构为：

- `RelationTypeContentDraft`：`semantic_label + states`。
- `ThoughtThreadContentDraft`：`canonical_subject + thought_aspect + states`。
- Relation/Thought Review Record 分别保存 `generated_content`、可空 `reviewed_content` 和可空 `previous_reviewed_content`。
- `effective_content()` 统一选择人工或机器完整内容。

范围、证据、风险、审核字段和稳定 ID 继续保留在 Record 外层。正式构建器改用 `effective_content()`；审核基础摘要仍只基于机器内容与证据，不包含人工快照。

目前没有权威全量 Relation/Thought Review 文件，可以直接重新聚合生成新结构；Story/Lore Review Schema 不变。格式仍保持实验 `format_version=0`，不为尚未正式发布的数据格式增加版本迁移。

Lore decisions 模型补充“清除人工决定”操作，但不改变现有 action 语义。

## 7. 共享后端模块

### 7.1 审核操作模块

把现有 `stage3_review_editor.py` 中混合的加载、修改和写盘拆成一个深层无 UI 模块：

- 外部接口以 `apply(artifact, typed_command) -> validated_artifact` 为核心。
- typed command 覆盖内容编辑、完成处置、待跟进、备注、恢复机器版本、身份处理、证据移动、拆分与合并。
- 每条命令负责维护完整快照、覆盖唯一性、时间窗口、稳定身份和审批重置不变量。
- 现有 CLI 函数变为薄文件适配器；读取 JSON/参数、调用同一操作、再安全写入。
- GUI 只提交 typed command，不直接修改 Pydantic model 内部字段。

这样测试通过同一接口覆盖 CLI 与 GUI 的全部业务行为，避免在 NiceGUI 回调中散布领域规则。

### 7.2 工作区会话模块

新增无 UI `ReviewWorkspace`：

- 通过 `load_build_spec()` 解析全部路径并建立 artifact registry。
- 允许缺失的后续产物以“待生成”占位，不阻止工作台启动。
- 为每个 artifact 保存加载时 SHA、内存草稿、脏状态、撤销栈和重做栈。
- 保存前检测磁盘文件是否被外部修改；发现冲突时禁止覆盖并要求重新加载或人工处理。
- `save_current()`、`save_all()`、`reload()`、`undo()`、`redo()` 是 UI 使用的主要接口。
- 保存全部先完成全部内存校验，再逐文件安全替换；失败逐文件报告并保留失败草稿。

撤销历史只存在于当前进程：字段编辑按一次提交合并，结构操作每次形成一个命令；关闭或重新加载后清空，不写操作日志。

### 7.3 来源与重生成模块

根据 build spec 建立直接依赖图：

- Stage 2 Input/2A → 本集 Story/Lore Review。
- 全集 Stage 2 Input/2A → Relation Review。
- 全集 Stage 2 Input/2B + Story/Lore Review → Thought Review。
- 全部 Story/Lore Review → Lore decisions。
- 四类最终审核产物 → 构建审计。

保存后重新计算内存中的来源状态。任何重生成或审计前必须先保存相关脏 artifact。

重生成不通过 shell 拼接命令：把现有 CLI 编排提取为可调用 Python 函数，CLI 和 GUI 共同使用。耗时步骤在 NiceGUI 后台任务中运行并流式显示状态；Prompt Package 流程明确分成 render、等待/检查 responses、assemble，不能假装一次按钮能完成人工或 Codex 响应。

### 7.4 审计适配

直接调用 `validate_worldbook_build()`，将 `WorldbookBuildReport` 映射为：

- 全包状态与问题数量。
- artifact 路径、稳定 ID、问题代码和建议动作。
- 可跳转的审核项；无法定位的包级问题留在总览。

审计仍覆盖 `.build` 中的单次报告；工作台不创建另一份持久审计结果。

## 8. CLI 调整

- 保留现有审核命令名称，内部改用共享审核操作。
- `edit-stage3-item` 的 Relation/Thought replacement 改为完整内容对象，而不是裸 State 数组。
- 增加“恢复机器版本”和“标记待跟进”的 CLI 入口，使 GUI 可完成的审核状态也能在无界面环境恢复。
- 为同一角色或角色对的结构替换提供单一 scope-level CLI 入口，replacement 是完整结构 JSON；不暴露大量容易错配的 ID 参数。
- 新增启动入口，例如：

  ```bash
  PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-workbench \
    --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
  ```

- `command.md` 在工作台完成后把 GUI 作为正常人工审核方式，保留 CLI 作为等价备用和自动化入口。

## 9. 分阶段实现

### 阶段 A：完整快照与统一审核操作

1. 新增 Relation/Thought 完整内容模型并更新聚合、迁移、basis 与 builder。
2. 把审核状态转换、内容修改、恢复机器版本和身份处理收口到 typed command 接口。
3. 增加 scope-level Relation/Thought 结构操作与覆盖唯一性校验。
4. 保留并改造现有 CLI 适配。

验收：CLI 与纯函数测试可以编辑三个新增重要字段、拆分合并结构，并正确撤销审批；builder 只投影有效完整快照。

### 阶段 B：工作区会话与 Story/Lore 垂直切片

1. 实现 build spec 加载、artifact registry、缺失文件占位和证据索引。
2. 实现逐文件草稿、显式保存、保存全部、外部修改检测和会话撤销/重做。
3. 建立 NiceGUI 壳层、总览、统一队列和 Story/Lore 表单。
4. 接入审核处置、原因、备注、恢复机器版本和候选身份确认。

验收：可从现有 MyGO build spec 打开第 1 集 Review，完成编辑、撤销、保存、重载和逐项审核；旧 `stage3_dataset_editor.py` 不再是新版正常入口。

### 阶段 C：Relation 结构审核

1. 实现按有向角色对分组、Observation 证据展示和 State 时间序列编辑。
2. 实现 Observation 移动、Type/State 拆分合并及身份选择。
3. 接入未归属 Observation 的归线、拒绝与排除。

验收：任一 Observation 恰好归属一次或拥有终态处置；非法方向、重叠时间和未解决身份阻止完成审核。

### 阶段 D：Thought 结构审核

1. 实现按角色分组、Update/Transition/Event Fact/Story Event 证据展示。
2. 实现主题、方面、状态文本、认知立场、引用与时间序列编辑。
3. 实现 Update 移动、Thread/State 拆分合并及身份选择。
4. 接入未归属 Update 的归线、拒绝与排除。

验收：同一 Thread 不产生重叠有效状态；引用均合法；retracted 不生成正式 State；所有 Update 覆盖完整。

### 阶段 E：Lore 去重与批量审核

1. 从 build spec 的逐集 Review 解析重复组候选文档。
2. 实现多候选对比、主候选选择、合并文档编辑、保留、丢弃和恢复 pending。
3. 实现显式多选与“批量审核通过”，并排除所有阻断项目。

验收：所有 pending 组可在 GUI 完成；自动组只读；批量操作不把筛选结果隐式纳入，也不能绕过身份和结构错误。

### 阶段 F：新鲜度、重生成与审计闭环

1. 实现来源依赖图和过期状态展示。
2. 接入 Story/Lore、Relation、Thought、Lore decisions 的显式重生成流程。
3. 展示并重新加载 migration report；身份或删除许可仍按现有规则阻断。
4. 接入构建审计、问题定位和就绪总览。
5. 更新 `command.md`、标注指南和旧编辑器提示。

验收：从上游变化到重新生成、重新审核、纯审计成功形成完整 GUI 闭环；正式发布仍只能从 CLI 进行。

## 10. 测试与验证

### 单元测试

- 每种 typed command 的合法和非法状态转换。
- 修改内容后审批重置，修改备注不重置。
- Relation/Thought 完整快照的有效投影和恢复机器版本。
- Observation/Update 覆盖唯一性、跨范围移动拒绝、时间窗口和引用校验。
- Type/Thread/State 拆分合并与身份继承冲突。
- Lore 三种人工决策、自动组只读和恢复 pending。
- 批量审核只作用于显式选择且可完成的审核单位。

### 工作区测试

- 相对路径解析、缺失文件占位和 artifact 类型错误。
- 单文件保存、保存全部预校验、部分 I/O 失败后的脏状态。
- 磁盘外部修改冲突。
- 每文件独立撤销/重做和保存后的再次撤销。
- 上游保存后的准确过期传播。

### 集成测试

- 临时 build spec + 四类最小 artifact 的完整审核和审计路径。
- CLI 与 GUI 后端对相同命令生成字节等价的合法 artifact。
- 现有 Relation/Thought 聚合器生成完整内容模型并可被 builder 发布投影。
- `validate_worldbook_build()` 的错误能够定位到工作台条目。

### UI 验证

- 无浏览器业务逻辑测试优先覆盖 `ReviewWorkspace` 和 typed commands。
- NiceGUI 测试覆盖导航、过滤、脏状态、对话框条件和按钮启用规则。
- 使用 MyGO 第 1、2 集真实开发数据进行人工视觉检查，确认长文本、状态序列和多候选 Lore 对比不会溢出或遮挡。
- 最后执行 Python 语法检查及相关 Stage 3/worldbook 单元测试集合。

## 11. 完成标准

- `worldbook_build.json` 是正常入口，缺失或过期输入都有明确状态和修复路径。
- 四类候选和 Lore 去重均无需手工编辑原始 JSON。
- CLI 与 GUI 共享审核规则，任何正式内容修改都会可靠撤销旧审批。
- Relation/Thought 的主题级字段与结构调整能够被人工修正并保持稳定身份。
- 所有候选到达终态后，工作台纯审计成功；正式发布命令使用同一批权威文件无需额外转换。
