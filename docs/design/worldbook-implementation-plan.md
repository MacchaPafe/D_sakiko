# 世界书管理与 Qdrant 同步实施计划

状态：首轮范围已实施（2026-07-12）

本计划把[世界书管理与 Qdrant 同步设计](worldbook-management.md)转化为可执行的实现顺序。用户已经明确批准编码；阶段零至阶段六和第一轮只读 UI 已实施，类型专属编辑仍按字段稳定性逐类解锁。

## 1. 目标与交付边界

本轮实现的最终目标是：先把现有 RAG 的固定 `SeasonId` 语义迁移为“剧情时间轴 + 可空剧情学年”，再让应用能够发现和查看内置世界书包，保存包级用户状态，并在不阻塞 Qt UI 的前提下通过独立 worker 把所有有效条目同步到独立 Qdrant 索引。

由于标注流程仍在探索三类数据的最佳字段，第一轮 UI 以只读管理框架为完成目标；类型专属表单、用户扩展创建和完整 Override 编辑只有在相应 Type Module 提供稳定编辑描述后才逐类开放。

本轮不实现：

- 对话 RAG 查询或 prompt 注入；
- 对话剧情节点设置；
- 新建、导入或联网下载第三方世界书包；
- 任意 JSON 编辑；
- 标注证据发布；
- Character Thought 世界书管理；
- 大模型资源修复；
- 正式构建拒绝 Schema v0 的发布门禁；
- 尚未稳定的三类完整编辑表单。

本轮会修改尚未接入主程序的 RAG 运行时数据模型、标注流水线和已有标注数据，但仍不实现对话检索策略或 prompt 注入。时间语义迁移是世界书格式的前置工作，不代表启用 RAG。

## 2. 建议路径

实现时先集中建立路径模块，避免继续依赖当前工作目录：

```text
<app-root>/GPT_SoVITS/rag/worldbooks/official/
  <package-id>/manifest.json
  <package-id>/content/*.json

<app-root>/knowledge_base/worldbooks/package-state/
  <package-id>.json

<app-root>/knowledge_base/worldbook_index/
  Qdrant 本地索引

<app-root>/knowledge_base/worldbook_index.lock

<app-root>/GPT_SoVITS/pretrained_models/multilingual-e5-small/
```

路径函数放在 `GPT_SoVITS/rag/worldbook/paths.py`，全部接受可注入的 `app_root`，测试不得依赖真实仓库目录。

## 3. 阶段零：剧情时间语义迁移

### 3.1 冻结替代语义

删除固定三值的 `SeasonId` 及 `season_id` 字段，统一采用：

- `timeline_id: str`：标识能够直接比较剧情顺序和有效区间的连续时间线；
- `current_story_year: int | None`：`RetrievalContext` 中可空的当前剧情学年；
- `occurred_story_year: int | None`：Story Event 的事件发生学年；
- `applicable_story_years: list[int] | None`：Lore Entry 的适用学年；
- Character Relation State 和 Character Thought 只使用时间线及有效/可见区间，不做同学年相等过滤。

MyGO 与 Ave Mujica 使用 `bang_dream_original` 时间线；世界连续性尚未确认的梦限大使用独立时间线且可以不填写剧情学年。`time_position` 只允许在同一 `timeline_id` 内比较。

### 3.2 迁移运行时模型与检索服务

修改现有 `rag.models`、context builder、service 和 LLM integration：

- 删除 `SeasonId` enum 以及 `season_id`、`season_ids`；
- `RetrievalContext` 改用 `current_timeline_id` 和可空 `current_story_year`；
- Story Event、Character Relation State、Character Thought 和 Lore Entry 按 3.1 使用新字段；
- 删除 `limit_to_season`、`require_season_match` 等旧选项；
- 不新增 `restrict_to_current_story_year`；事件可以在后续学年被回忆；
- 保留未来事件隔离所需的可见/有效区间语义，但本阶段不设计或接入对话查询策略。

这一步只迁移模型及现有未发布检索代码，使其不再表达错误语义；不会让主程序开始调用 RAG。

### 3.3 迁移标注流水线

修改 `GPT_SoVITS/rag/pipeline/` 中的 schema、CLI、各阶段输入输出、编辑器和 Stage 3 导入逻辑：

- 标注任务元数据接收 `timeline_id` 和可空 `story_year`，不再产出 `SeasonId`；
- 各类产物只在语义适用时写入 `occurred_story_year` 或 `applicable_story_years`；
- 时间位置分配和排序显式绑定 `timeline_id`，不同时间线可以使用相同局部位置；
- 清理提示词、说明文本和校验规则中的“季度等于剧情学年”假设；
- Character Thought 虽不进入第一版世界书 UI，也随流水线和运行时模型一起迁移，避免保留双轨结构。

现有按 `SeriesId` 分配事件位置的实现可以保留局部分段思想，但必须验证其时间线归属；不能再用动画系列身份代替时间线。

### 3.4 迁移已有标注数据

在实施阶段编写一次性、可重复运行的数据迁移工具，并用它转换已跟踪的发布数据和仍作为测试输入的标注 artifact：

- 元数据中的 `season_id` 转为 `timeline_id` 与可空 `story_year`；
- Story Event 的 `season_id` 转为 `occurred_story_year`；
- Relation/Thought 删除学年字段并写入 `timeline_id`；
- Lore 的 `season_ids` 转为 `applicable_story_years`；
- 无法可靠推断时间线或学年的数据必须报告并等待人工确认，不得静默猜测；
- 迁移后重新运行 schema 校验，并确认已纳入范围的数据不存在旧字段。

迁移工具属于开发工具，不进入正式运行时，也不要求修改无关备份或临时文件。

### 3.5 阶段验收

- 运行时代码、pipeline schema、正式 fixture 和测试中不再引用 `SeasonId` 或旧 `season_id(s)` 字段；
- 同时间线内的位置可比较，不同时间线不会因局部位置重叠而互相过滤；
- 后续学年仍能检索较早事件，Relation/Thought 不做同年过滤；
- Lore 的学年适用性与事件发生学年是两个独立概念；
- 无剧情学年的独立时间线可以完成标注、发布转换和模型验证；
- 现有 RAG/pipeline 测试迁移并通过。

## 4. 阶段一：管理 envelope 与纯逻辑内核

### 4.1 建立包级模型

新增 `GPT_SoVITS/rag/worldbook/models.py`，定义并验证：

- `WorldbookManifest`
- `PackageDependency`
- `ContentFileRecord`
- `WorldbookEntry`
- `WorldbookUserState`
- `WorldbookOverride`
- `WorldbookTombstone`
- `EffectiveWorldbookEntry`
- `ValidationIssue`
- `PackageLoadResult`
- `PackageReadiness`
- `WorldbookReadiness`
- `SyncPlan` 与 `SyncReport`

要求：

- 新文件使用 future annotations；
- 完整类型注解，不使用 `Any`；
- 新类和函数使用中文 docstring；
- `entry_id` 必须是 UUID；
- manifest v1、用户状态 v1 和条目 Schema v0 的版本职责严格分开；
- 所有摘要基于规范化 JSON，而不是文件缩进或键顺序。

### 4.2 建立安全包加载器

新增 `package_loader.py` 和 `hashing.py`：

- 从显式官方根目录发现 manifest；
- 校验 SemVer、包类型和依赖字段；
- 拒绝绝对路径、目录逃逸、大小写冲突和未声明内容文件；
- 校验每个内容文件 SHA-256；
- 加载统一 `entries` 列表；
- 检查包内和全局重复 entry ID；
- 构造依赖图，检查缺失依赖和循环；
- 包结构错误整包失败，条目 Schema 错误保留为条目级 issue。

### 4.3 建立包级用户状态仓库

新增 `user_state.py`：

- 一个官方包对应一个用户状态 JSON；
- 原子保存：同目录临时文件、flush/fsync、replace；
- 支持读取空状态、保存 Override、删除 Override、隐藏/恢复官方条目；
- 为用户扩展预留新增、更新、永久删除接口；
- 不允许 Override 改变 entry ID 或 entry type；
- 不允许用户扩展指向不存在的目标包；
- 单包状态损坏不影响其他包。

### 4.4 建立有效条目合并器

新增 `effective_entries.py`：

- 官方条目减去 tombstone；
- 有效 Override 替换同 ID 官方条目；
- 用户扩展加入结果；
- 基准摘要改变时标记 base conflict，但继续使用有效 replacement；
- 不兼容 Override 只隔离该条目，不回退官方内容；
- 生成稳定 entry revision 和来源徽标；
- 输出面向查看器与同步模块的同一份有效条目集合。

### 4.5 阶段验收

- 所有纯逻辑测试不加载 Qdrant 或 embedding；
- 相同内容不同 JSON 排版得到相同 revision；
- 官方更新后 Override、tombstone 与扩展行为符合设计；
- 依赖缺失、循环、重复 ID 和路径逃逸都有明确错误码；
- 一个包损坏时其他独立包仍可加载。

## 5. 阶段二：条目 adapter 与发布转换

### 5.1 建立两层 adapter 注册表

新增 `adapters.py` 及 `entry_types/`：

- `EntrySchemaAdapter`：解析特定 `(entry_type, schema_version)` 并迁移为当前模型；
- `EntryTypeModule`：当前验证、只读展示描述、embedding 文本和 Qdrant 投影；
- adapter registry 拒绝重复注册；
- 当前三类各提供一个 Schema v0 adapter；
- 第一轮 editor schema 可以全部只读，不承诺最终字段；
- v0 adapter 映射阶段零迁移后的 `rag.models` 字段，但不得把这些仍在探索的内容字段声明为公开 v1；
- embedding 文本第一轮可以沿用当前 `retrieval_text`，同步模块本身不得依赖该字段名。

### 5.2 建立发布转换

新增 `publisher.py`，并在现有 RAG pipeline CLI 增加独立 `publish-worldbook` 命令：

- 输入 Stage 3 审核 artifact；
- 输出正式 manifest v1 和内容分片；
- 清除 evidence、confidence、source scene、模型、模板路径和 issues；
- 使用开发侧 entry ID map 分配或复用稳定 UUID；
- 新来源默认失败，只有显式 `--allocate-new-ids` 才分配；
- 拆分/合并后无法自动确认身份时要求人工更新 ID map；
- 生成内容文件摘要与 package source revision；
- 当前允许发布 Schema v0。

建议开发侧 ID map 放在标注 artifact 附近但不进入正式包；它只辅助发布者复用身份，不能成为运行时依赖。

### 5.3 建立第一个开发包

- 使用现有 `ep01_published.json` 生成 MyGO 开发包；
- 保持原 artifact 不变；
- 仅用作包加载、查看和同步 fixture；
- 在三类字段稳定前不把它宣称为正式 v1 数据。

### 5.4 阶段验收

- 发布输出不含本机绝对路径或证据字段；
- 重复发布在 ID map 不变时保持所有 entry ID；
- 包文件可被运行时加载器完整验证；
- 未知条目版本只隔离对应条目；
- Type Module 可以在不依赖 UI 的情况下生成只读字段描述、embedding 文本和扁平投影。

## 6. 阶段三：独立索引 seam 与真实 Qdrant adapter

### 6.1 定义索引 interface

新增 `index.py`：

- `WorldbookIndex` Protocol/抽象 interface；
- `InMemoryWorldbookIndex` 测试 adapter；
- 查询实际 point metadata；
- 批量 upsert、按 ID 删除、按 package 删除；
- 重建 collection；
- 返回结构化操作结果；
- interface 不暴露 `QdrantClient` 对象。

### 6.2 实现 `QdrantWorldbookIndex`

新增 `qdrant_index.py`：

- 使用独立 `knowledge_base/worldbook_index`；
- 只拥有 `story_events`、`character_relations` 和 `lore_entries`；
- 不修改现有包含 Character Thought 的 `QdrantRagService.create_database()` 行为，避免扩大重构；
- 从当前 `EmbeddingProvider`、payload index 和批量 upsert 逻辑中提取或复用必要实现，但不让新模块依赖旧 service 的四 collection 注册表；
- Qdrant point ID 直接使用 entry UUID；
- 所有 point 保存统一 envelope、entry revision 和索引指纹；
- 类型字段由 Type Module 扁平投影；
- `qdrant_client` 只允许出现在该生产 adapter 中；
- adapter `close()` 必须真实关闭 client 并释放模型引用，而不是只把属性设为 `None`。

### 6.3 索引指纹

计算并写入：

- index schema version；
- projection version；
- embedding model ID；
- embedding model文件摘要；
- vector size；
- distance。

不创建 sync-state 文件。每次 reconcile 直接分页扫描实际 point ID、entry revision 与指纹；空索引或无法验证的索引进入重建。

### 6.4 阶段验收

- 全量删除世界书索引不影响原有 Character Thought 或其他数据库；
- 未变化索引检查不加载 embedding 模型；
- 增量同步与全量重建得到相同 point 集合和 revision；
- 修改 embedding 指纹触发全量重建；
- 模型缺失产生明确 unavailable reason；
- Qdrant 失败不影响 JSON 加载与编辑模块。

## 7. 阶段四：同步协调、worker 与进程协议

### 7.1 实现同步协调模块

新增 `sync.py`：

- 加载所有官方包和包级用户状态；
- 合并有效条目；
- 扫描实际索引；
- 生成 upsert/delete/rebuild 计划；
- 普通变化按条目增量、官方变化按包对账、指纹变化全量重建；
- 包结构错误整包隔离；
- 条目错误单条隔离；
- JSON 已保存但同步失败时不回滚；
- 生成完整内存 `SyncReport` 和 readiness；
- 不写持久状态或结果文件。

### 7.2 实现独立 worker

新增 `worker.py`，支持：

```text
reconcile-all
sync-package <package-id>
rebuild
```

worker：

- 使用 `filelock` 获取跨进程锁；
- 锁忙时循环等待并输出 `waiting_for_lock`，最长 10 分钟；
- 获取锁后才重新读取最新 JSON；
- stdout 只输出 NDJSON；
- stderr 和常规日志保存人类诊断；
- 最后一个 completed/failed 事件直接携带报告；
- exit code 区分成功、验证失败、同步失败、锁超时与内部错误；
- finally 中关闭 Qdrant 和 embedding 资源。

### 7.3 实现 Qt controller

新增 `ui/controllers/worldbook_sync_controller.py`：

- 使用异步 `QProcess`，不创建操作 Qdrant 的 QThread；
- 解析 NDJSON 并发出 started/progress/waiting/completed/failed signals；
- 禁止 `waitForFinished()` 和阻塞式 subprocess 调用；
- 当前进程已有任务时设置 `reconcile_pending`，任务完成后最多补跑一次；
- 等待锁的进程允许取消，持锁 worker 不提供强制终止；
- 窗口销毁时断开 UI signals，但不得让主线程等待 worker。

### 7.4 主程序启动接入

- 主窗口展示完成后启动一次 `reconcile-all` QProcess；
- UI 启动不等待模型加载或 Qdrant；
- 本阶段不让主程序长期持有 Qdrant client；
- 未来对话检索接入时另行设计长生命周期查询与同步互斥。

### 7.5 阶段验收

- worker 等待锁时 Qt UI 保持响应；
- 两个配置进程同时请求同步时不会并发打开本地 Qdrant；
- 等待 worker 获取锁后读取的是最新 JSON；
- 连续保存只产生当前任务加至多一次补跑；
- worker 崩溃后下次 reconcile 能从实际索引恢复；
- 协议输出中没有混入普通 print 文本。

## 8. 阶段五：`WorldbookArea` 只读管理界面

### 8.1 注册 interface 与入口

- 新增 `ui/interfaces/worldbook_area.py`；
- 在 `DSakikoConfigWindow` 注册 `WorldbookArea` 和导航图标；
- `object_name_to_interface` 支持 `WorldbookArea`；
- 主程序“更多功能”增加“世界书管理”按钮；
- 入口使用 `dsakiko_configuration.py WorldbookArea`；
- 不新建另一套窗口框架，不复用 NiceGUI 标注编辑器。

### 8.2 第一轮 UI

实现三栏管理框架：

- 左侧：包名、版本、类型、依赖和加载状态；
- 中间：有效条目列表、类型/来源徽标、搜索和筛选；
- 右侧：adapter 驱动的通用只读详情；
- 底部：当前同步进度、错误、重新同步、全量重建。

第一轮允许：

- 查看有效条目；
- 查看官方、已修改、用户和冲突来源；
- 显示/恢复已隐藏官方条目；
- 隐藏官方条目；
- 查看官方基础与当前有效版本；
- 在 Qdrant/embedding 故障时继续浏览和保存不依赖字段的用户状态操作。

第一轮暂不承诺：

- 三类完整编辑表单；
- 新建用户扩展；
- 完整 Override 编辑；
- 原始 JSON 预览。

缺少编辑描述时显示“当前条目格式尚未开放编辑”。某一类型形成足够稳定的当前字段模型后，只补该 Type Module 和对应测试即可逐类解锁编辑，不等待其他类型。

### 8.3 后续编辑表单解锁条件

每个类型必须先明确：

- 当前字段模型和必填字段；
- 验证规则；
- embedding 输入；
- Qdrant 投影；
- 可编辑与只读字段；
- 新建用户扩展的默认值。

满足后再启用显式保存、未保存草稿提示、Override 编辑和用户扩展 CRUD。不能以任意 JSON 编辑器绕过该门槛。

### 8.4 阶段验收

- 从“更多功能”可直接打开配置程序的世界书页面；
- 内容管理和索引可用性相互独立；
- 查看器不会因为模型缺失而锁死；
- 同步过程、等待锁和真实模型加载不会冻结 UI；
- 包和条目错误显示在正确粒度；
- 未开放编辑的类型不会出现可保存的空壳表单。

## 9. 阶段六：发布与依赖交付

### 9.1 Python 运行依赖

- 将 `qdrant-client` 与 `sentence-transformers` 纳入正式程序运行环境，而不只留在标注用 `rag` extra；
- 保持标注专用的 NiceGUI、字幕和 prompt 依赖与运行时世界书依赖分开；
- 更新 lockfile 并验证 CPU/macOS/Windows 发布环境。

### 9.2 模型与官方数据

- 完整安装包和从旧版本升级的自动更新都交付本地 `multilingual-e5-small`；
- worker 不自动联网下载模型；
- 更新构建校验模型文件和摘要；
- 普通程序资源 repair 继续排除 `pretrained_models/**`；
- 模型缺失或损坏提示重新安装/更新，不提供本阶段模型修复。

### 9.3 发布前门禁

- 正式包包含 manifest 与声明的全部内容文件；
- 内容摘要正确且无本机绝对路径；
- 模型 fingerprint 与运行配置一致；
- 默认启动可以后台建立索引；
- 第二次启动在索引一致时不加载 embedding；
- 真实模型集成测试通过；
- 现有更新和修复测试无回归。

## 10. 测试计划

### 10.1 快速逻辑测试

建议新增：

```text
GPT_SoVITS/test/test_rag_temporal_model.py
GPT_SoVITS/test/test_rag_annotation_migration.py
GPT_SoVITS/test/test_worldbook_models.py
GPT_SoVITS/test/test_worldbook_package_loader.py
GPT_SoVITS/test/test_worldbook_user_state.py
GPT_SoVITS/test/test_worldbook_effective_entries.py
GPT_SoVITS/test/test_worldbook_adapters.py
GPT_SoVITS/test/test_worldbook_sync.py
GPT_SoVITS/test/test_worldbook_worker_protocol.py
GPT_SoVITS/test/test_worldbook_ui.py
```

使用 fake embedding 和 `InMemoryWorldbookIndex` 覆盖：

- 时间线隔离、可空剧情学年和旧字段迁移；
- 所有验证和冲突分支；
- 原子写入失败；
- Qdrant adapter 失败注入；
- 锁等待、超时和取消；
- NDJSON 协议；
- UI 只读与索引不可用状态。

### 10.2 真实模型集成测试

新增 `worldbook_real_embedding` marker，使用本地 `multilingual-e5-small` 和临时 Qdrant 目录验证：

- 向量维度；
- 完整包导入；
- 语义相关样例进入预期 top-k；
- 修改条目后 revision 和向量更新；
- 删除和隐藏后 point 消失；
- 增量同步与全量重建结果一致；
- embedding fingerprint 变化触发重建。

不比较完整浮点向量。真实模型测试是发布前必跑项，快速逻辑测试仍保留 fake 以覆盖错误分支。

### 10.3 每阶段验证

每次修改 Python 文件后：

- 对该文件运行 `py_compile`；
- 运行相应最小测试集合；
- 完成阶段时运行全部世界书测试；
- UI 阶段运行 Qt offscreen 测试；
- 发布阶段运行真实 embedding 集成测试和相关现有 RAG/更新/修复回归测试。

## 11. 推荐提交/评审批次

为了减少一次性变更面，建议按以下批次评审：

1. **时间语义迁移**：运行时模型、pipeline、一次性数据迁移、现有测试；
2. **格式与纯逻辑**：模型、loader、用户状态、有效条目、测试；
3. **adapter 与发布器**：Schema v0、ID map、开发包、测试；
4. **索引与同步**：index interface、Qdrant adapter、coordinator、真实集成测试；
5. **worker 与 controller**：锁、NDJSON、QProcess、启动接入；
6. **只读 UI**：WorldbookArea、导航入口、状态与隐藏/恢复；
7. **发布交付**：运行依赖、模型与官方包、发布门禁；
8. **按类型解锁编辑**：标注字段稳定后逐类追加，不阻塞前七批。

每一批都必须保持已有功能测试通过；不要在前一批接口尚未稳定时同时推进后续 UI。

## 12. 总体验收标准

- JSON 是唯一可恢复内容来源，删除 Qdrant 目录后可完整重建；
- 固定 `SeasonId` 不再出现在运行时、标注 schema 或正式数据中，时间位置只在相同 `timeline_id` 内比较；
- 剧情学年保持可空，较早事件不会仅因当前学年不同而被排除；
- 官方更新不覆盖用户 Override、扩展或隐藏状态；
- 一条不兼容 Override 不阻塞整个包，一份损坏包不阻塞独立包；
- 全量重建只影响三类世界书 collection，不删除 Character Thought 或运行时记忆；
- 主程序和多个配置程序不会并发打开嵌入式 Qdrant；
- worker 加载真实模型、等待锁或重建时 Qt UI 始终响应；
- 没有持久 sync-state 时仍能从实际 point revision 发现中断和漂移；
- Qdrant 或模型不可用时查看器仍可管理 JSON；
- 未稳定的内容类型保持只读，不通过临时 JSON 编辑器绕过校验；
- 正式发布直接交付 embedding 模型，不发生后台隐式下载；
- 真实 embedding 集成测试、Qt 测试和相关现有回归测试通过。

## 13. 开始编码前的批准门

编码批准门已于 2026-07-12 通过。后续若重新加入以 `>` 开头的设计批注，应先暂停相关实现、吸收并删除批注，再继续修改对应范围。
