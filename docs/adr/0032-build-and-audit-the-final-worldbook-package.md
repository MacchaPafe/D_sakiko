---
status: accepted
---

# 直接构建并审计正式世界书包

官方发布把审核后的 Story Event、Character Relation、Lore Entry 和 Character Thought 直接构建成带正式稳定身份的 staging 世界书包，并使用正式 loader、Schema adapter 和跨条目校验审计该包；不再生成重复内容的 `worldbook_release.json`。审计通过后整体替换正式包目录，再从全部有效官方包与用户状态全量重建四张 Qdrant collection。

## 构建输入

可复用的 `worldbook_build.json` 显式列出包元数据、预期连续集数范围、唯一的全量 Relation Review、唯一的全量 Thought Review、Lore 去重决策和开发侧 ID map，并用 `episodes` 对象数组为每集绑定 `episode`、Stage 2 Input、Stage 2A annotation、Stage 2B annotation 与 RAG artifact；不使用多组依赖下标对应的平行数组。配置由单个 `publish-worldbook --build-spec` 命令读取，只保存参数和输入路径，不复制发布内容；路径相对配置文件解析，也不使用目录扫描或通配符自动纳入输入。

构建配置显式声明 `package_id`、`package_version`、`display_name`、`package_type`、`series_id`、`timeline_id`、`canon_branch` 和可空的 `story_year`。前三类范围字段作为所有输入必须满足的构建预期，不能从第一份 artifact 隐式推导；`story_year` 非空时才要求适用输入一致，空值表示不强制整个包属于同一学年。`series_id` 与 `canon_branch` 即使不进入 manifest，也必须参与构建校验。

每份单包构建配置还必须由发布者显式提供 `dependencies` 列表及其 `package_id`、`version_spec`；发布器原样生成正式 manifest 并执行依赖图审计。依赖不从系列、品牌、时间线、包名或上一版 manifest 推导，省略列表即表示没有依赖。构建报告可以显示依赖集合变化，但不自动继承、恢复或判断其业务合理性；构建配置是开发侧依赖声明的唯一来源，manifest 是运行时来源。

一个 `worldbook_build.json` 始终只构建一个世界书包。需要随软件一次发布多个包时，另用轻量批量配置按路径引用多份单包构建配置：先为每个包构建 staging，再把全部 staging 与未更新的现有官方包作为一个预期包集合执行全局审计；任一包失败都不替换正式目录，全部通过后依次整体替换，并且只在最后全量重建一次索引。批量替换不提供跨包事务、备份或自动回滚，中断后通过重新执行整批发布恢复。

单包和批量发布都对上述预期包集合执行一次全局依赖图审计，每个包只加载一次，不沿依赖边重复构建或重新读取上游标注产物。审计必须验证包自身可用、`package_id` 与 entry UUID 全局唯一、依赖目标存在且可用、目标版本满足 `version_spec`，并拒绝依赖循环；这样发布 Mujica 时可以校验现有或本批次 MyGO，单独升级 MyGO 时也能发现对现有反向依赖的破坏。

第一版 `version_spec` 只接受一个或多个以逗号连接的显式 SemVer 比较器，例如 `==1.2.0`、`>=1.0.0`、`>=1.0.0,<2.0.0` 或 `>1.0.0,<=1.5.0`，默认值仍为 `>=0.0.0`；暂不支持 caret、tilde、通配符或 `||` 联合范围。无法解析的表达式必须使包加载或发布审计失败，不能被静默忽略。

运行时依赖采用传递闭包语义：选择根包时递归引入每个依赖包的完整依赖，同一个包只出现一次，所有官方 entry 作为全局 UUID 唯一的并列内容参与检索，不发生根包覆盖依赖包。用户状态仍按条目所属包分别应用；闭包中任一必需包不可用或版本不满足时，根包检索不可用而不是静默缺少知识。发布审计因此必须验证每个根包的完整无环依赖闭包。

第一版不对不同世界书包执行文本相似度检测、warning 或自动去重，只阻断全局重复的 entry UUID。包内 Lore 继续使用各自的去重与审核流程；跨包语义重复由发布者人工判断为删除季度包副本并依赖既有包、保留不同适用范围的独立 entry，或将真正共享的内容迁入通用世界书包。

发布前必须确认预期范围内每集恰好一份 RAG artifact，没有缺集、重复或越界，所有输入的系列、时间线和剧情分支一致，而且 Relation 与 Thought Review 声明的覆盖范围与预期集数完全相同。各集 RAG artifact 只提供 Story Event 和 Lore 候选，旧的逐集 Relation 结果不参与发布；长期 Relation 和 Thought 分别来自唯一的全量审核产物。

季度包配置中的 Relation Review、Thought Review 和 Lore decisions 即使最终为零条也必须提供。审核完成度不使用人工维护的权威布尔值，而由发布器根据完整 episode coverage、当前直接来源摘要、所有候选均处于终态且没有未解决冲突或结构错误自动推导；合法的空记录列表自然满足候选终态要求。产物可以保存只供展示的派生 `review_summary`，但发布器必须重新计算。缺少文件、覆盖范围不全或推导为未完成都阻断发布；合法空结果不生成对应正式内容分片，每集 RAG artifact 仍然必填。纯 Lore 的 common 包将来使用不要求伪造 episode coverage 的独立简化输入模式。

覆盖范围相同不足以证明审核产物仍对应当前输入，因此全量审核链路必须保存直接来源文件的 SHA-256：Relation Review 记录各集 Stage 2 Input 与 Stage 2A annotation，Thought Review 记录各集 Stage 2 Input、Stage 2B annotation 与相关 RAG artifact，Lore decisions 记录参与去重的各集 RAG artifact。构建配置显式列出同一批来源，发布器按逻辑角色和集数配对当前路径并重新计算摘要；文件移动或改名不影响判断，但任一内容摘要不一致都阻断发布，并给出重新生成相应 Relation、Thought 或 Lore 产物的命令。正式 `publish-worldbook` 不提供 `--allow-stale` 绕过；即使发布者确认变化无影响，也应重新生成审核产物来确认新的来源摘要。来源摘要只存在于审核产物和构建报告，不进入正式世界书包。

新鲜度校验按流水线分层且只验证直接依赖：各集 RAG artifact 负责记录其 Stage 2 直接来源，全量 Relation、Thought 和 Lore 审核产物负责记录上述直接输入，发布器只验证构建配置与这些审核产物，不递归读取字幕、Prompt 模板或 LLM response。字幕到 Stage 2 的来源一致性由相应上游生成阶段负责，因此错误应在最接近需要重跑步骤的边界被报告。

人工编辑某个 artifact 不使该文件自我失效，但会使记录了其旧摘要的直接下游失效：修改 RAG artifact 后需要重新生成或复核 Thought Review 与 Lore decisions，但不影响不依赖它的 Relation Review；修改 Stage 2B 只使 Thought Review 失效。人工编辑 Relation Review 或 Thought Review 本身无需重新聚合，只要最终 Schema、审核状态和来源摘要仍合法即可发布。发布器始终针对构建配置中的当前输入重新计算摘要，手工修改审核产物中的摘要字段不能绕过实际不一致。

直接来源文件的完整 SHA-256 与记录级 `review_basis_sha256` 职责不同：前者变化即要求重新生成直接下游产物，不尝试忽略已知字段；重新生成后再用后者决定人工快照和处置能否迁移。因而只有置信度或风险字段变化时仍需重跑聚合，但审核基础摘要保持相同即可自动保留原人工审核；语义、证据或机器结果变化才要求重新人工审核。

## 审核门槛

审核工作流状态与发布处置分开保存：前者只表示尚未审核、需要跟进或已经完成，后者只表示 `publish`、`reject` 或 `exclude`。新候选和缺失审核字段的旧 artifact 都按未审核处理，LLM 只能提出处置建议，不能直接把审核状态变为完成；人工编辑作为独立审计信息保存，不再充当审核状态。Story Event 与 Lore Entry 逐条审核，Character Relation 按完整 Relation Type 状态序列审核，Character Thought 按完整 Thread 状态序列审核。只有审核完成且处置为 `publish` 的候选能进入 staging 包；完成后处置为 `reject` 或 `exclude` 的候选留在审核产物但不发布。任何未审核、需要跟进、缺少最终处置、未解决冲突或结构错误都阻断整个包。

四类审核产物都保留不可编辑的机器基准和可空的人工完整替换。Story Event 与 Lore Entry 使用 `generated_document` / `reviewed_document`，第一次人工修改时复制整条文档，之后只编辑人工文档；Relation Type 与 Thought Thread 使用 `generated_sequence` / `reviewed_sequence`。审核界面与发布器始终优先使用人工快照，人工快照不存在时才使用机器基准，正式包只保存最终投影，不保存双份数据或编辑标记。

四类候选都计算统一语义的 `review_basis_sha256`。Story Event 与 Lore Entry 摘要包含直接来源或证据稳定 ID 及完整规范化 `generated_document`；Relation Type 与 Thought Thread 摘要包含所覆盖局部证据稳定 ID、证据内容和完整规范化 `generated_sequence`。机器生成标题、文本、时间、标签、引用、分组或状态结构任一变化都会使旧审批失效；摘要排除人工快照、审核字段、备注、正式 UUID、文件路径和 JSON 排版。只有摘要完全相同时才迁移人工快照、审核处置与备注；摘要变化时旧人工内容只读展示供参考并重新审核，但正式身份仍可按身份继承规则保留。

Story Event 与 Lore Entry 的记录级审核基础具体包含 `source_scene_id`、`source_local_id`、排序去重后的 `evidence_u_ids`、排序去重后的 `evidence_s_ids` 和完整机器文档，不重复嵌入字幕文本。字幕内容变化由上游 Stage 2/RAG 文件新鲜度校验负责；重新生成后证据 ID 集合变化时，即使机器文档文本相同，也会使审批失效。

逐集 RAG artifact 重新规范化时也可以把旧文件作为 previous review；`entry_type + source_scene_id + source_local_id` 只用于来源完全未变时确定性找到旧候选，长期对应关系由独立 `candidate_id` 表示。`review_basis_sha256` 相同才迁移 `reviewed_document`、审核状态、最终处置和备注；摘要变化时只保留身份对应建议并重置审核，旧人工文档只读展示，候选拆分或合并也不自动继承人工文档。previous 与 output 可以是同一路径，但必须先完整读取旧文件，在内存生成并校验新 artifact，再通过同目录临时文件和 `os.replace` 安全替换，失败时保留旧文件。

Story Event 与 Lore Entry 审核记录拥有独立稳定的 `candidate_id`，ID map 以它而不是来源场景或本地编号作为 identity key。来源未变且身份约束一致时可以确定性沿用；场景重切分、本地编号变化或候选重组时，程序可以结合类型、范围、证据重叠和语义相似度提出旧到新候选的继承建议，LLM 也只能参与建议，不能仅凭“文本差别不大”自动决定。人工确认仍为同一项知识后，新候选继承旧 `candidate_id`；拆分时一个延续候选继承，合并时指定一个旧身份继承，其余获得新身份或 retired。未解决的身份歧义阻断正式发布，`source_scene_id` 与 `source_local_id` 继续只描述来源证据。

Story/Lore 身份继承确认直接集成到其审核编辑器，不另建迁移命令或身份建议 JSON。来源和身份约束无歧义时可自动继承，其余项在界面中并排展示新旧记录、来源证据与建议理由，由审核者选择继承旧身份、作为新候选或进入拆分/合并；未处理建议使审核保持 `needs_followup` 并阻断发布。

Relation 与 Thought 的序列及内部 State 身份确认也集成到各自审核器：先并排确认上一版 Relation Type/Thread 与新版序列是继承、新建、拆分还是合并，再处理每个 State 的 entry 身份继承；身份未决时整个序列保持 `needs_followup`，只有全部身份问题解决后才能设为 `completed`。身份确认和内容审核可以在同一页面完成，不另建身份迁移工具。

新建候选在审核阶段立即获得并持久化开发侧稳定 ID：Story/Lore 使用 `candidate_id`，Relation/Thought 使用各自的 relation type、thread 与 state ID；这些 ID 写入审核产物并纳入 Git，用于重生成匹配、审核基础和身份继承，不进入正式世界书。审核器不修改 ID map；只有候选审核完成并进入正式发布时，发布器才由 ID map 为 entry、Thought Thread 和 Relation Type 分配运行时 UUID。

开发侧稳定 ID 统一使用可校验的类型前缀加 UUIDv4：`story_candidate:<uuid>`、`lore_candidate:<uuid>`、`relation_type:<uuid>`、`relation_state:<uuid>`、`thought_thread:<uuid>` 和 `thought_state:<uuid>`。ID 不依赖内容、时间、场景或模型输出，Schema 必须校验记录类型与前缀一致；不使用递增数字，以避免并行标注和 Git 合并时发生冲突。

现有数据只执行一次兼容升级：旧单集 Thought Review 和旧逐集 Relation 结果不作为迁移来源，新全量聚合直接生成新的开发侧身份；现有 Story/Lore RAG 记录首次升级时生成 `candidate_id`，并把旧 `point_id` 保留为开发侧 `legacy_source_id`。升级器通过旧 point ID 和旧 ID map 找回已存在的正式 entry UUID，再把新 candidate identity key 映射到同一 UUID，避免已有用户 Override 失去目标；完成核对后普通流程只使用 candidate ID。`legacy_source_id` 可以留在审核产物供追溯，但不进入正式包，这段兼容逻辑不成为长期正常发布路径。

该兼容升级由显式、幂等的一次性 `upgrade-stage3-review-schema` 子命令完成，而不隐藏在普通 normalize 流程中。命令只处理 Story/Lore RAG 与旧 ID map，通过安全替换把它们转换为新候选身份和命名空间映射；已升级记录保持原 candidate ID，重复运行不产生新身份。一个旧 UUID 对应多个新候选或其他身份歧义必须在转换前解决，命令不处理旧 Thought 或逐集 Relation。转换过程不保留专门的 decisions 文件或迁移审计记录，成功后的新审核产物与新 ID map 就是最终结果。

升级命令默认只 dry-run 并在 CLI 展示转换结果，只有显式 `--apply` 才修改审核文件和 ID map；apply 必须重新扫描当前输入并重新校验。命令不使用交互式终端确认，也不为这次一次性转换持久化过程记录。

Story/Lore RAG、Relation Review、Thought Review 和 Lore decisions 的生成命令都默认在输出文件已存在时自动把它作为 previous review，也允许显式指定其他旧文件；只有 `--fresh` 才禁用迁移。旧产物类型、系列、时间线或覆盖范围不兼容时必须报错，不能静默忽略。迁移是重生成命令内部的自动步骤，不另设 `migrate-review` 子命令。

每次自动迁移都必须输出可见摘要，至少统计旧候选、新候选、完整迁移人工审核、因审核基础变化而重置、新增未审核、消失候选和身份继承待确认的数量。CLI 展示这些计数及每类前若干稳定 ID，完整列表保存在可覆盖的派生 `migration_summary` 或当前构建报告中；该摘要不影响发布、不作为下一次迁移输入，也不保留历史版本。

不为消失候选另建删除清单文件。每个重生成命令都把完整迁移诊断写入固定的 `<output>.migration-report.json`，阻断和成功都覆盖同一文件；报告包含删除、重置、新增、完整迁移和身份待确认列表，CLI 只显示计数、前若干 ID 和报告路径。迁移报告不修改旧 Review，不作为迁移输入或删除授权，不纳入 Git，也不保留历史版本；成功安全替换 Review 后仍保留本次报告，供发布者核对后在下一次运行时覆盖。

迁移报告与对应 Review 并排保存，使用可机械推导的 `<output>.migration-report.json` 名称，并通过 `*.migration-report.json` 统一加入 `.gitignore`；发布阶段跨输入的 `build-report.json` 仍位于 `.build/`。

旧候选在新结果中消失时按既有人工处置分级：未完成审核或已完成拒绝/排除的候选允许自动消失并只在摘要中报告；已完成发布的候选不得静默消失。遇到后者时，重生成命令生成诊断但不覆盖旧 Review，并完整列出稳定 ID；发布者核对后可以为个别项重复传入 `--allow-removed-id <stable_candidate_id>`，也可以使用 `--allow-all-removed` 一次性确认本次重新计算出的全部消失集合。两种许可都只对当次命令有效，不写入新 Review 或永久 allowlist；`--allow-all-removed` 不放行 State/Thread 拆分合并的身份继承歧义，也不绕过结构、来源或审核错误。确认后才安全替换新 Review，普通消失对应的正式身份在后续发布时从 active 转为 inactive；拆分、合并等确认的身份终结才转为 retired。

消失确认按序列层级处理：整个 Thought Thread 或 Relation Type 消失时，确认序列稳定 ID 即连带允许其中全部已发布 State 消失；序列仍存在而个别 State 消失时必须逐 State 确认。State 文本、时间或证据变化不算消失，只使审核基础变化；State 拆分或合并进入独立的身份继承待确认流程，不能作为普通删除放行。

审核工具提供显式“恢复机器版本”操作，删除对应 `reviewed_document` 或 `reviewed_sequence` 而不保留与机器基准相同的副本，同时把 `review_status` 重置为 `unreviewed`、清空最终 `disposition`，但保留非权威的 `suggested_disposition` 供重新审核；恢复后必须再次由人工完成审核。

任何影响最终发布内容或状态序列结构的人工修改都会使既有审批失效，编辑器必须立即把 `review_status` 重置为 `unreviewed` 并清空最终 `disposition`。这包括 Story/Lore 内容字段、Relation/Thought State 文本、有效时间、State 增删拆合、Update/Observation 归属、Story Event 引用和 Epistemic Status；纯 UI 过滤排序折叠以及不参与发布投影的审核备注不触发重置。修改完成后必须重新选择处置并完成人工审核。

审核产物可以保存由编辑器提供的可空多行 `review_notes`，供审核者手工记录合并、拆分、排除、保留或后续复核的背景。该备注不进入正式世界书或 Qdrant，不参与 `review_basis_sha256`，修改它也不重置审核状态；最终拒绝或排除仍必须填写结构化的 `disposition_reason`，不能只依赖普通备注。审核基础未变时备注可以迁移，基础变化时旧备注只读展示供参考，不自动复制为新审核结果。

抽取或链接置信度、`risk_level` 和 `risk_reasons` 只用于审核队列排序与提示，不进入正式世界书或 `review_basis_sha256`，单独变化也不撤销已完成的人工审核。高风险候选在人工完成审核并选择最终处置后可以发布；风险字段本身不阻断发布，但结构错误、引用错误、来源新鲜度或 coverage 缺失仍属于阻断错误。

第一版不允许按置信度或风险等级自动写入 `completed + publish`。所有候选都必须经明确的人工处置；编辑器可以提供带数量和类型确认的批量“完成并发布”等操作，但 Story/Lore 仍逐条写入，Relation/Thought 的操作单位仍是完整序列而不是内部 State。机械一致的 Lore 去重是唯一不要求额外人工批准派生结果的例外。

统一审核字段为：`review_status` 取 `unreviewed`、`needs_followup` 或 `completed`；`disposition` 取 `publish`、`reject`、`exclude` 或空值；`suggested_disposition` 保存 LLM 或确定性规则的非权威建议；`disposition_reason` 在最终拒绝或排除时必填，发布时可空。审核未完成时最终 `disposition` 必须为空，生成器和 LLM response Schema 不能写入 `completed` 或最终处置，只有审核工具可以设置；发布要求 `review_status == completed` 且最终处置非空，并且只投影 `disposition == publish` 的候选。Relation 与 Thought 是否经过人工编辑由可空 `reviewed_sequence` 自动推导，不另存 `human_edited`；机械一致的 Lore 合并可以从全部已审核来源派生最终结果，无需伪造一次额外人工审核。

当前单人本地审核流程不保存 `reviewed_by`、`reviewed_at` 或其他审核者标签与时间戳，也不引入账号、权限或签名系统。

`reject` 与 `exclude` 都不进入正式世界书，但开发语义不同：拒绝表示候选本身不成立、证据或抽取错误；排除表示候选有依据，但不属于目标包的正式长期知识，例如瞬时表现、粒度过细或应归属其他包。发布器对二者同样不投影，区别只保留在审核产物中供后续重聚合、标注质量分析和潜在复用；二者都必须审核完成并填写原因。

最终拒绝或排除使用结构化的 `disposition_reason_code` 加可空的 `disposition_reason_note`。拒绝只允许 `unsupported`、`extraction_error`、`duplicate` 或 `other`，排除只允许 `transient_state`、`too_trivial`、`wrong_package`、`not_long_term_knowledge` 或 `other`；选择 `other` 时说明必填，其他代码的说明可选，编辑器按最终处置只展示合法选项，发布器再次校验组合。Lore 去重中的 `drop` 由已审核去重决策独立解释，不映射成候选的 `reject + duplicate`。原因字段只存在于审核产物和质量统计，不进入正式包。

阻断报告按来源文件和内容类型列出未就绪条目的 ID、状态与原因，并在审核工具存在时输出代入实际输入路径的可复制命令。Story、Lore 和 Relation 需要补齐与 Thought 等价的状态审核能力，不能输出实际上无法完成审批的命令。

Lore 门槛在应用去重决策后的最终候选上执行：完全等价的 `auto_merge_identical` 仅在全部来源均已审核时自动通过；`keep_separate` 后仍逐条审核；`merge` 和 `drop` 决策本身必须审核完成并具有明确处置，人工文本修改作为独立审计信息保留。未解决重复组、未确认的人工决策草稿和未审核决策阻断发布，不另建去重后的审核文件。

Lore 重复组也计算 `review_basis_sha256`，包含组内来源记录稳定 ID、各记录完整 `generated_document` 以及相似度分组使用的规范化标题。重新扫描当前 RAG artifact 时，只有摘要完全相同的组才能迁移旧的 merge、drop、keep_separate 决策、人工文档与审核处置；成员或机器文档变化时旧决策仅只读展示，不得自动应用，已经不再成组的旧决策不进入新文件。Lore decisions 允许 previous 与 output 使用同一路径，但必须在内存生成并校验完整新文件后，通过同目录临时文件和 `os.replace` 安全替换，失败时保留旧文件。

## 正式 Character Relation

正式 Relation 只保留有向角色对、系列、时间线、剧情分支、`relation_type_key`、自包含的 `state_summary`、说话提示、称呼、有效区间、标签和检索文本，并取消语义重复的 `relation_label`。`state_summary` 才是展示、检索和角色提示内容；Observation、字幕证据、置信度、风险与审核状态只留在 Relation Review 或构建报告。

Relation Review 保存不可编辑的机器序列和可空的人工审核序列；发布器优先使用人工序列、为空时使用机器序列，并把最终 State 投影为正式内容。正式包不保存双份序列或人工编辑标记。

Relation 的审核状态、最终处置和 `human_edited` 放在同一有向角色对下的完整 Relation Type 状态序列，不逐 State 保存；序列处置为发布时，其审核后的所有 State 进入正式包，任何过度拆分、合并、重叠或时间边界问题都必须在序列内修正后再完成审核。序列的拒绝表示聚合或分组错误，排除表示局部 Observation 有依据但不应形成长期关系状态。未合并 Observation 只能单独完成人工拒绝或排除，若要发布必须先纳入某个 Relation Type；发布前每条 Observation 必须恰好被一个已完成审核的序列覆盖，或自身已完成人工拒绝或排除。

`relation_type_key` 在同一有向角色对的关系方面首次审核通过时分配稳定 UUID，不再由 LLM 生成 snake_case。普通摘要修改和新增后继 State 保留该 UUID；关系方面拆分或合并时由人工确认一个旧 UUID 继承，其余 UUID retired，LLM 只能建议对应关系。每个 Relation State 仍有独立稳定 entry UUID，后继 State 不复用前一 State 的 entry UUID。

## 正式 Character Thought

正式 Thought 必须自包含，只保留角色、系列、时间线、剧情分支、Thread UUID、规范化 Subject、Thought Aspect、自包含观点文本、Epistemic Status、有效区间、可选 `story_event_entry_ids`、标签和检索文本。Event Fact、Thought Update、Transition、场景及字幕证据、证据强度、抽取或链接置信度、审核状态和风险原因只留在审核产物或构建报告；不发布 Event Fact entry，也不设置 `event_fact_id` 一类运行时引用。

Thought Review 保存不可编辑的机器序列和可空的人工审核序列；发布器优先使用人工序列、为空时使用机器序列，并把最终 State 投影为正式内容。正式包不保存双份序列或人工编辑标记。

Thought 的审核状态与最终处置只放在完整 Thread 上，不逐 State 保存；Thread 处置为发布时，其审核后的整个 State 序列进入正式包，任何 State 问题都必须在线程内修改、合并或删除后再完成审核，任一结构或文本修改都会使 Thread 的 `human_edited` 为真。Thread 的拒绝表示抽取或分组错误，排除表示内容有依据但不属于长期 Character Thought。尚未归入 Thread 的 Update 单独审核，只能最终拒绝或排除；若要发布，必须先归入或新建 Thread，`unresolved` Update 不能直接发布。发布前每个 Update 必须恰好由一个已完成审核的 Thread 覆盖，或自身已完成人工拒绝或排除。

`story_event_entry_ids` 由发布器转换为当前 staging 包内正式 Story Event UUID，只是可选关联，不决定 Thread 身份，也不能替代自包含文本。引用必须指向实际发布且系列、时间线和剧情分支兼容的 Story Event，重复项机械去重；未审核、被拒绝、缺失、类型错误或无法分配正式 ID 的目标都阻断发布而不静默移除。第一版不引用依赖包中的 Event。

Thought Thread 在首次审核通过时获得不由主题文本计算的稳定 UUID。规范化 Subject、Thought Aspect 或状态措辞的普通修改以及新增后继 State 均保留 Thread UUID；Thread 拆分时由人工指定一个延续 Thread 继承旧 UUID，合并时由人工指定一个旧 UUID 作为合并后身份，其余 UUID retired。每个有效期 State 是独立正式 Character Thought entry，并共享 Thread UUID；重申不创建新 entry，纯撤回只结束既有 entry 的有效期。

## 正式身份

普通文本、证据或有效期修订保留 State 的 entry UUID。一拆多时由人工指定一个延续 State 继承旧 UUID，多合一时由人工指定一个旧 UUID 作为合并后身份，其余 UUID retired；普通删除只把原身份转为 inactive。纯撤回不删除或退休旧 Thought State entry，只把其有效期截止到撤回时间。LLM 只能建议 Thread、Relation Type 和 State 的身份继承关系，未解决歧义阻断发布。inactive 与 retired UUID 都不得分配给其他身份；正式包不保存它们到 successor 的身份沿革，也不按文本相似度迁移用户 Override。

开发侧 ID map 使用带 `format_version` 和 `package_id` 的命名空间结构，分别保存 `entry/<entry_type>/<source_id>`、`thought_thread/<stage3_thread_id>` 和 `relation_type/<subject>/<object>/<stage3_relation_type_id>` 到 UUID 的映射，并记录 `active`、`inactive` 或 `retired`。来源键使用标注侧稳定 ID，不使用文件路径或内容哈希；`package_id` 不匹配时拒绝构建。该映射只用于开发侧标注数据到正式身份的转换，不写入正式世界书包，也不参与运行时加载、Override 合并或索引；正式包只保存转换完成的 UUID。

inactive 只允许以完全相同的 identity key 和原 UUID 显式恢复，不属于 UUID 复用。当前已完成审核且处置为发布的候选命中 inactive 映射时，validate 只报告待恢复；正式发布可以为个别项重复传入 `--reactivate-id <identity-key>`，也可以使用 `--reactivate-all` 恢复本次构建中重新出现、identity key 完全相同且当前处置为发布的全部 inactive 映射。许可只对当次命令有效，不触及 retired，也不绕过审核、来源、身份歧义或结构校验。retired 必须重新进入拆分、合并或身份继承的人工确认流程。恢复 entry UUID 后，用户此前针对该 UUID 保存的孤立 Override 或 tombstone 会重新适用；Override 的基础 revision 不同则继续按基准冲突处理，不兼容内容仍隔离。构建报告必须列出所有停用、退休与恢复的身份变化。

候选通过审核门槛并进入正式 ID 分配阶段后，新 UUID 立即持久化到开发侧 ID map。即使后续 staging Schema 或跨引用审计失败也不回收，下次构建继续复用；inactive 与 retired 映射都继续保留在开发侧，UUID 永不转给其他身份。

ID map 是正式发布命令唯一会修改的已版本管理输入；重生成 Review 和 `validate-worldbook-build` 只在迁移或构建报告中显示预计停用、退休和恢复，不改变映射状态。单包发布先完成输入、新鲜度、coverage 与人工审核门槛，再为缺失身份分配 UUID，并通过临时文件安全持久化新增映射，随后构建和审计 staging；只有正式发布继续通过删除确认与全局审计后，才把不再发布的 active 映射改为 inactive、显式恢复的 inactive 改为 active、拆分或合并确认终结的身份改为 retired，并再次安全写入 ID map 后替换正式包。后续正式包替换或索引失败都不回滚映射。批量发布对各包采用相同的两阶段写入；构建报告必须列出本次新增与状态变化，失败发布产生的已持久化 ID map 变化也作为正常待提交变更保留。

各集 RAG artifact、全量 Relation Review、全量 Thought Review、Lore decisions、开发侧 ID map、单包与批量构建配置以及最终正式世界书包都必须纳入 Git 版本管理，以保留人工审核基线、身份映射和发布输入。迁移报告、`.build/` 中的 staging 与 `build-report.json`、Prompt 运行缓存和 Qdrant 索引都是可重建派生数据，不纳入 Git；这些权威文件不应包含用户数据或秘密。

四种条目在本轮发布中继续使用实验 `schema_version=0`。Story Event 与 Lore Entry 保持 v0，Character Relation 直接更新尚未形成兼容承诺的 v0 结构，新增 Character Thought 也从 v0 开始；不为从未实际使用的旧 Relation v0 编写迁移 adapter。完成 01–13 集发布和运行验证后，再分别决定各条目类型是否冻结为 v1。

目标 UUID 已不存在的孤立 Override 继续保存在用户状态中，但默认不显示、不应用、不索引，也不降低同步 readiness；第一版不提供高级管理入口或自动清理。

## 审计与替换

开发侧 `build-report.json` 只关联输入 artifact、来源 ID、正式 entry ID、内容摘要和验证结果，不复制完整运行时内容。其路径由构建配置显式指定，通常位于已加入 `.gitignore` 的 `.build/` 下；每次构建使用临时文件整体写入并覆盖同一路径，不保留历史报告。审核门槛失败、staging 审计失败、正式包替换失败、索引失败和完整成功都必须生成报告，CLI 只显示精简摘要和报告路径。报告可由已版本管理的构建配置、审核产物与 ID map 重建，不进入正式世界书包，也不纳入 Git。staging 必须经正式 loader、四类 Type Module、Schema、manifest 摘要、全局 ID 和跨引用校验。

构建报告可以尽力记录当前 Git commit hash，读取不到时记为未知且不影响发布；发布器不要求工作区干净，也不检查或记录 dirty 状态。每个实际输入仍以自身 SHA-256 精确标识。

`validate-worldbook-build --build-spec` 是纯检查命令：可以构建临时 staging 并执行全部单包及预期官方包集合审计，但不替换正式包、不修改开发侧 ID map，也不重建索引。ID map 尚无映射的新身份在检查进程内以固定 namespace 和身份 key 计算的 UUIDv5 临时投影完成 Schema、全局唯一与跨引用验证；报告只记录哪些来源身份尚未正式分配及其 provisional 状态，不保存临时 UUID 的具体值。临时 UUID 与 staging 均不持久化，也不承诺等于正式发布时分配的 UUIDv4。`publish-worldbook` 在单包替换完成后自动全量重建一次索引，`publish-worldbooks` 在整批包替换完成后也只重建一次；正式发布不提供 `--skip-index`，只有自动重建失败时才提示单独执行 worker 的 `rebuild` 命令。

索引一致性继续复用现有同步 worker、跨进程锁、索引指纹和逐条 entry revision 对账；不另建持久化的索引代际或 `index_state.json`。进程异常退出后，由下一次全量对账发现缺失、过期和多余 point 并恢复。真正把世界书数据库接入运行时检索时，应用必须以单一应用级 readiness 作为检索门槛：启动对账完成前、重建过程中和同步失败后都不得查询世界书索引，也不能把这种状态解释成合法零命中。当前程序尚未实际接入该 RAG 数据库，因此本轮只规定未来接入边界，不提前修改应用初始化流程。

纯检查仍对现有 ID map 执行严格只读校验：`package_id` 必须匹配，状态只能为 `active`、`inactive` 或 `retired`，identity key 必须位于正确命名空间，同一 UUID 不得被多个 identity 使用，entry、Thought Thread 和 Relation Type UUID 不得相互碰撞，也不得与其他正式包 entry UUID 冲突。当前候选引用 retired 映射属于身份继承阻断错误；引用 inactive 映射时提示需要显式恢复。map 中当前未使用的 active 映射只产生“可能转为 inactive”的 warning，不在 validate 中修改。

正式包只为实际非空的条目类型生成 `content/*.json`，manifest 也只声明这些文件；不生成空 `entries` 占位分片。缺少某类内容文件表示该包没有该类条目，不是错误，季度包也不强制 Story 或 Lore 非空；全局四张 Qdrant collection 仍由所有有效包中的实际条目共同重建，与单包包含哪些类型解耦。

正式世界书包必须确定性构建：在构建配置、输入 artifact 和 ID map 相同时，重复构建产生字节一致的 manifest 与 content JSON。正式包不写入构建时间、Git commit、模型名称等运行期信息；content file、依赖和 entry 使用固定排序，业务排序字段相同时以正式 UUID 作为最终并列键，JSON 编码、缩进与结尾换行也保持固定。Git commit 等追踪信息只进入不随包发布的构建报告；正式内容未变时，包文件与 manifest 摘要不得因重复运行而变化。

`package_version` 由发布者手工管理：开发期可以持续使用 `0.1.0` 反复构建，首次向用户发布时手工改为 `1.0.0`，此后的有意内容发布由发布者自行提升 SemVer。发布器不比较条目语义或摘要来判断是否需要升版，只校验版本格式且不得低于现有正式包版本；相同版本始终允许重新构建和覆盖。构建报告可以记录新旧内容摘要用于排查，但不参与版本决策；Override 仍通过官方 entry revision 独立检测基础内容变化。

构建配置是只读发布输入，发布命令使用其中人工填写的 `package_version` 生成 manifest，不自动升版或回写构建配置；后续版本变化由发布者在 Git 中手工修改。

验证通过后，发布命令直接用 staging 目录整体替换目标包，不逐文件覆盖，也不另设 `--promote`、旧包备份或发布锁。若替换中断，允许目标包暂时缺失或不可用，并由下一次成功发布恢复；替换成功后重新加载正式目录并全量重建索引。索引失败不回滚正式 JSON，因为世界书包仍是权威来源，可以稍后重试重建；但 `publish-worldbook` 必须以非零退出码结束，明确输出 `package_published: true` 与 `index_rebuilt: false`，把失败原因写入构建报告，并给出可复制的 `python -m rag.worldbook.worker rebuild` 重试命令。应用侧保持世界书检索不可用或降级，不能把索引失败伪装成零命中。

正式世界书发布是离线的开发或构建操作，不支持向正在运行并可能同步、检索世界书的应用目录热发布。发布目标不应同时存在运行中的世界书 worker 或检索进程；用户端软件更新也应在应用退出后替换官方包，并在下次启动时对账派生索引。该运维边界与官方知识低频更新相符，因此第一版不增加发布锁、热更新代际切换或跨进程包读取协调；若未来需要不停机热更新，应另行设计而不能依赖当前无锁目录替换。

发布后索引对账得到 `degraded` 时仍视为重建成功：它只表示个别不兼容用户 Override 或 Extension 已被隔离，其余有效内容已经完成索引，不得因为用户局部状态问题否定正式包发布。CLI 与构建报告必须明确输出 `index_readiness: degraded`、受影响包或条目及处理建议，但命令仍以零退出码结束。只有索引整体 `unavailable`、worker 异常或重建操作失败才以非零退出码结束并要求重试；无论何种结果，都必须独立报告 `package_published` 与 `index_rebuilt`，不能要求调用者仅凭退出码推断正式包是否已经替换。

索引 worker 信任 Qdrant 使用 `wait=True` 完成的同步删除与写入；一次对账或重建不在写入后再次全量扫描 point 做后验验收。后续任意一次正常对账仍会比较当前有效条目、entry revision、索引指纹与实际 point，并修复缺失、过期或多余内容；若写入调用本身报错，则按索引失败处理而不是报告完成。
