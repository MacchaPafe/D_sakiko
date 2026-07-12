# 世界书管理与 Qdrant 同步设计

本文记录第一版世界书查看、用户编辑和 Qdrant 派生索引同步的实现设计。条目内容 Schema 仍在演进，因此本文固定管理 envelope、生命周期与模块 seam，不冻结 Story Event、Character Relation State 和 Lore Entry 的最终字段。

相关架构决定见：

- [ADR-0022：以世界书包为权威来源并派生独立索引](../adr/0022-use-authoritative-worldbook-packages-and-a-derived-index.md)
- [ADR-0023：按世界书包依赖限定检索范围](../adr/0023-scope-worldbook-retrieval-by-package-dependencies.md)
- [ADR-0024：将剧情时间轴与可空剧情学年分离](../adr/0024-separate-story-timeline-from-optional-story-year.md)
- [ADR-0025：在只读官方世界书包上叠加用户状态](../adr/0025-layer-user-state-over-read-only-worldbook-packages.md)
- [ADR-0026：分别版本化世界书包与条目 Schema](../adr/0026-version-worldbook-envelopes-and-entry-schemas-separately.md)
- [ADR-0027：从权威 JSON 对账派生世界书索引](../adr/0027-reconcile-the-derived-worldbook-index-from-json.md)

## 1. 当前范围

第一版包含：

- 发现并校验随应用发布的内置官方世界书包；
- 查看 Story Event、Character Relation State 和 Lore Entry；
- 通过通用类型化表单修改部分字段；
- 为官方条目保存 Override；
- 隐藏和恢复官方条目；
- 新增、编辑和永久删除归属于现有官方包的用户扩展；
- 合并官方内容和用户状态，形成有效条目；
- 将所有已安装包的有效条目同步到独立 Qdrant 索引；
- 暴露后台同步进度、错误和结构化就绪状态；
- 提供重新同步与全量重建入口。

首个正式支持世界书的完整安装包和面向已有用户的自动更新必须直接交付 `multilingual-e5-small`（当前约 470 MB），并将 `qdrant-client` 与 `sentence-transformers` 纳入正式运行环境。worker 只加载版本确定的本地模型，不允许在后台静默联网下载；模型缺失或摘要不符时 readiness 为 unavailable，reason 为 `embedding_model_missing` 或 `embedding_model_mismatch`。

第一版继续遵守“大模型不属于普通可修复程序资源”的现有规则：自动更新负责模型首次交付、摘要校验和失败回滚，程序资源修复 profile 仍排除 `pretrained_models/**`。模型之后被删除或损坏时，查看器提示用户重新运行对应版本安装或更新，不调用普通程序资源修复器；独立大模型资源修复不属于本阶段。

第一版明确不包含：

- 创建、导入、下载或发布新的用户/第三方世界书包；
- 世界书管理器独立联网更新官方包；
- 对话创建时选择世界书或剧情节点；
- 从 Qdrant 检索并注入对话 prompt；
- 完整 JSON 预览或任意 JSON 编辑；
- 发布标注证据、字幕证据或流水线调试信息；
- 正式构建拒绝实验性 `schema_version: 0` 的发布门禁。

未来世界书检索上下文归属于对话，角色配置只提供创建对话时的默认值；该决定不要求当前阶段实现对话接入。

## 2. 数据流与所有权

```text
标注审核 artifact
        │ publish-worldbook
        ▼
只读官方世界书包 ─────┐
                       ├─ 合并 ─► 有效世界书条目 ─► WorldbookIndex
包级用户状态 JSON ─────┘                              │
                                                     ▼
                                              独立 Qdrant 目录
```

权威数据只有：

- 随应用发布的只读官方世界书包；
- 用户数据目录中的包级用户状态 JSON。

Qdrant、同步状态和有效条目都可以重新计算，不得作为恢复用户内容的唯一来源。查看器读取合并后的 JSON 视图，不从 Qdrant 反向构造条目。

## 3. 包粒度、依赖与时间轴

- 一个动画季度对应一个可独立版本化的官方包；单集只是包内内容分片。
- 多个季度明确共享的世界观进入独立通用包。
- 连续的前三季可以形成传递依赖：`s3 -> s2 -> s1 -> common`。
- MyGO、Ave Mujica 等是否依赖前作包由内容关系明确声明。
- 梦限大在世界连续性未确认时不依赖旧通用包；品牌相同、知道旧乐队或使用相同 `canon_branch` 都不能隐式建立依赖。
- 所有已安装包统一进入索引；未来对话检索再按根包和依赖闭包过滤，切换对话不重建索引。

每个包声明 `timeline_id`。只有同一剧情时间轴内的时间位置和有效区间可以比较。

本轮实施先删除固定三值的 `SeasonId`，将其替换为剧情时间轴和可空剧情学年：

- Story Event 使用可空 `occurred_story_year`，只描述事件发生学年，不限制以后回忆；
- Character Relation State 和 Character Thought 依靠有效时间区间，不默认按学年相等过滤；
- Lore Entry 可以使用 `applicable_story_years` 表达设定适用学年；
- 当前剧情学年未知时，受学年限制且不能由有效区间证明适用的 Lore 默认不可用；
- 不实现 `restrict_to_current_story_year`，未来事件泄漏由时间轴可见区间阻止。

运行时模型、现有未发布检索代码、标注流水线和已有标注数据在世界书 adapter 实现前完成字段迁移。具体对话查询策略、根包选择和 prompt 注入仍留待对话 RAG 接入阶段实现。

## 4. 官方包目录与 manifest

官方包作为只读程序资源跟随现有自动更新和程序资源修复系统发布。建议目录：

```text
worldbooks/
  official.bang_dream.common/
    manifest.json
    content/
      package_lore.json
  official.bang_dream.its_mygo/
    manifest.json
    content/
      ep01.json
      ep02.json
      package_lore.json
```

`format_version: 1` 的最小 manifest：

```json
{
  "format_version": 1,
  "package_id": "official.bang_dream.its_mygo",
  "package_version": "0.1.0",
  "display_name": "BanG Dream! It's MyGO!!!!!",
  "package_kind": "anime_season",
  "timeline_id": "bang_dream_original",
  "dependencies": [
    {
      "package_id": "official.bang_dream.common"
    }
  ],
  "content_files": [
    {
      "path": "content/ep01.json",
      "sha256": "..."
    },
    {
      "path": "content/package_lore.json",
      "sha256": "..."
    }
  ]
}
```

字段规则：

- `package_id` 跨版本不变；
- `package_version` 使用 SemVer，只标识一次内容发布；
- `package_kind` 第一版支持 `anime_season` 和 `common`；
- `dependencies` 第一版只声明包身份，不实现复杂版本范围；
- `content_files` 显式列出相对路径与摘要，不扫描目录猜测；
- 路径必须留在包目录内，拒绝绝对路径、目录逃逸和大小写冲突；
- source revision 由规范化 manifest 和有序文件摘要计算；
- 下载地址、签名、远程频道、标注模型和默认对话节点不进入 v1。

应用更新系统负责正式交付完整性；内容文件摘要同时服务运行时包校验和快速对账。

## 5. 正式内容文件与条目身份

内容文件统一使用自描述列表：

```json
{
  "entries": [
    {
      "entry_id": "019f...",
      "entry_type": "story_event",
      "schema_version": 0,
      "content": {}
    },
    {
      "entry_id": "019f...",
      "entry_type": "lore_entry",
      "schema_version": 0,
      "content": {}
    }
  ]
}
```

- `entry_id` 是首次正式发布或用户创建时分配的 UUID；
- 内容、标题、时间顺序、来源场景和文件分片变化不得改变 ID；
- 拆分与合并由发布者确认哪些 ID 保留或移除；
- `entry_type` 决定类型模块和目标 collection；
- `schema_version` 只版本化该类型的 `content`；
- 文件路径只服务审核，移动文件不改变条目身份；
- 当前未冻结的三类内容使用实验性 `schema_version: 0`；公开发布时再冻结 v1。

正式包不包含 evidence ID、来源场景、本地候选 ID、抽取置信度、标注模型、prompt 模板或 normalization issues。开发侧审核 artifact 独立保留这些信息；如果未来需要证据查看，应设计包含可解引用证据的独立数据集。

## 6. 发布转换

标注流水线不能直接写入正式内置世界书目录。独立 `publish-worldbook` 步骤负责：

- 读取并验证审核 artifact；
- 分配或复用稳定 `entry_id`；
- 通过当前发布 adapter 生成自描述正式条目；
- 移除本机路径、证据引用和流水线调试字段；
- 生成内容分片、manifest 和摘要；
- 检查重复 ID、未知类型和空 embedding 输入；
- 仅在明确的正式构建流程中写入官方包目录。

开发阶段暂不实现“正式构建拒绝所有 schema v0”的门禁。

## 7. 包级用户状态

第一版每个官方包对应一个用户状态文件：

```text
user-data/worldbooks/package-state/
  official.bang_dream.common.json
  official.bang_dream.its_mygo.json
```

```json
{
  "format_version": 1,
  "target_package_id": "official.bang_dream.its_mygo",
  "overrides": [
    {
      "base_entry_hash": "sha256:...",
      "modified_at": "2026-07-12T12:00:00+08:00",
      "note": null,
      "entry": {
        "entry_id": "official-entry-uuid",
        "entry_type": "story_event",
        "schema_version": 0,
        "content": {}
      }
    }
  ],
  "extensions": [
    {
      "entry_id": "user-entry-uuid",
      "entry_type": "lore_entry",
      "schema_version": 0,
      "content": {}
    }
  ],
  "tombstones": [
    {
      "entry_id": "official-entry-uuid",
      "base_entry_hash": "sha256:...",
      "hidden_at": "2026-07-12T12:00:00+08:00"
    }
  ]
}
```

写入时验证完整状态，写同目录临时文件，flush/fsync 后原子 replace；成功后才同步 Qdrant。一个包的用户状态损坏不得影响其他包。

### 7.1 Override

- `entry` 复用完整世界书条目模型和同一套 adapter；
- `entry_id` 和 `entry_type` 必须与目标官方条目一致；
- 跨类型修改通过隐藏原条目并新建用户扩展表达；
- replacement 是完整替换，不使用字段级 JSON Patch；
- 官方内容摘要仍等于 `base_entry_hash` 时状态为 clean；
- 官方摘要变化但 replacement 仍有效时状态为 base conflict，用户版本继续生效并提示复核；
- replacement 无法验证、迁移或投影时状态为 incompatible，对应条目隔离且不静默回退官方版本。

### 7.2 用户扩展

- 必须归属于一个现有内置官方包；
- 第一版不允许创建新包或数据库全局自定义条目；
- 新建时分配 UUID；
- 可以编辑并在确认后永久删除。

### 7.3 删除语义

- 官方条目：写 tombstone，UI 使用“隐藏官方条目”，可以恢复；
- 被隐藏的官方条目更新后仍保持隐藏，但提示基础内容变化；
- Override：删除 replacement，UI 使用“恢复官方版本”；
- 用户扩展：从用户状态永久删除并删除索引 point。

## 8. 有效条目合并

每个包按以下顺序形成有效条目：

```text
官方条目
  - tombstones
  + valid overrides replacing same IDs
  + valid user extensions
  - incompatible entries
```

合并结果只存在于内存和派生索引中，不另存一份权威 JSON。每个有效条目计算规范化 `entry_revision`，作为增量同步依据。

## 9. Schema adapter 与类型模块

历史 Schema 兼容和当前类型行为分成两层：

```python
class EntrySchemaAdapter:
    entry_type: str
    schema_version: int

    def parse(self, content: dict) -> object: ...
    def migrate_to_current(self, content: object) -> object: ...
```

```python
class EntryTypeModule:
    entry_type: str
    current_schema_version: int

    def validate_current(self, content: object) -> None: ...
    def editor_schema(self) -> object: ...
    def build_embedding_text(self, content: object) -> str: ...
    def project(self, content: object) -> dict: ...
```

- 每个受支持的 `(entry_type, schema_version)` 注册轻量 adapter；
- 每种 `entry_type` 只有一个当前 Type Module；
- 历史 adapter 把旧内容迁移到当前模型，再复用表单和索引投影；
- 官方只读文件不会被运行时改写；
- 用户下次保存 Override 时写当前 Schema；
- 缺少 adapter 或迁移失败时隔离相应条目；
- `build_embedding_text()` 决定显式使用 `retrieval_text` 还是从内容生成；同步模块不假设通用字段；
- embedding 文本为空使条目验证失败；
- embedding 生成规则变化提高 `projection_version` 并触发重建。

## 10. 查看器

世界书查看器实现为 `GPT_SoVITS/ui/interfaces/worldbook_area.py` 中的 `WorldbookArea`，注册到 `DSakikoConfigWindow` 的顶层导航；主程序“更多功能”增加入口，通过 `dsakiko_configuration.py WorldbookArea` 直接打开该页面。它复用现有配置应用的 FluentWindow、interface 注册和跨进程启动方式，不另建独立窗口框架，也不复用开发侧 NiceGUI 标注编辑器。

默认展示合并后真正会进入索引的有效条目，而不是把官方、Override、用户扩展和 tombstone 四层并列暴露。建议三栏布局：左侧世界书包，中间类型/搜索/筛选与条目列表，右侧当前有效条目的类型化表单；底部展示索引状态和重新同步/全量重建入口。

条目显示来源徽标：官方、已修改、用户、基准冲突。筛选支持三种条目类型、来源、仅冲突、显示已隐藏，以及按标题、角色、标签和主要文本搜索。Override 详情提供当前用户版本、可折叠官方基础、保留并重新确认与恢复官方版本；已隐藏官方条目默认不出现在有效列表，开启筛选后灰显并提供恢复。

内容管理可用性与索引可用性分离。只要官方包和用户 JSON 可以加载，查看器就允许浏览、内存搜索、编辑和原子保存；Qdrant、embedding 或同步故障只显示明确错误、使索引和未来 RAG unavailable，不禁用编辑器，也不回滚已保存内容。顶部持续显示不可用原因，并保留重新同步入口。

查看器只渲染 Type Module 提供的通用字段描述，不写死三类表单。第一版至少支持：

- 单行和多行文本；
- 整数和可空整数；
- 枚举；
- 标签列表；
- 角色列表；
- 只读字段。

`entry_id`、`entry_type`、`schema_version` 和目标包始终只读。第一版不实现完整 JSON 预览，也不允许绕过 adapter 校验保存原始 JSON。

编辑采用显式保存，不在字段变化时自动写盘。表单维护草稿，保存时由当前 Type Module 完整验证，通过后原子写入包级用户状态 JSON，并立即显示“内容已保存，正在同步索引”；索引 worker 异步运行，不阻止继续查看其他条目。切换条目或关闭窗口时若有未保存草稿，应提示保存、放弃或取消操作。

同一配置进程已有 worker 运行时不为每次保存创建新的等待进程，而设置内存 `reconcile_pending`；当前 worker 完成后如果期间发生过保存，再启动一次 reconcile。其他配置进程的并发请求仍由跨进程文件锁串行化。

## 11. 独立 Qdrant 索引

第一版 `WorldbookIndex` 使用独立 Qdrant 本地目录，仅拥有：

- `story_events`
- `character_relations`
- `lore_entries`

Character Thought、运行时长期记忆和其他数据不自动纳入。建议接口：

```python
class WorldbookIndex:
    def reconcile_all(self) -> SyncReport: ...
    def sync_package(self, package_id: str) -> SyncReport: ...
    def rebuild(self) -> SyncReport: ...
    def readiness(self) -> WorldbookIndexReadiness: ...
```

模块与进程职责：

```text
WorldbookArea
    ↓
WorldbookSyncController
    ↓ QProcess 命令、进度事件、完成结果
worldbook_sync_worker
    ↓
WorldbookSyncCoordinator
    ↓ WorldbookIndex interface
QdrantWorldbookIndex / InMemoryWorldbookIndex
```

- `WorldbookSyncController` 位于 UI 进程，只启动和观察 `QProcess`、转发 Qt signals，不导入 `qdrant_client`、不加载 embedding、不解析世界书；
- worker 获取跨进程文件锁，创建依赖，执行一次任务并关闭所有资源；
- `WorldbookSyncCoordinator` 加载权威 JSON、合并有效条目、扫描实际索引 revision、计算同步计划并调用索引 interface，不直接调用 `QdrantClient`；
- `QdrantWorldbookIndex` 是唯一允许调用 `qdrant_client` 的生产 adapter；
- `InMemoryWorldbookIndex` 作为测试 adapter，不依赖真实模型和数据库。

建议文件布局：

```text
GPT_SoVITS/
  rag/worldbook/
    models.py
    package_loader.py
    user_state.py
    adapters.py
    sync.py
    qdrant_index.py
    worker.py
  ui/controllers/
    worldbook_sync_controller.py
  ui/interfaces/
    worldbook_area.py
```

每个 Qdrant point 使用 `entry_id` UUID，并附加统一字段：

```json
{
  "package_id": "official.bang_dream.its_mygo",
  "entry_id": "uuid",
  "entry_type": "story_event",
  "entry_schema_version": 0,
  "entry_revision": "sha256:...",
  "effective_source": "official",
  "index_schema_version": 1,
  "projection_version": 1,
  "embedding_fingerprint": "sha256:..."
}
```

`effective_source` 取 `official`、`override` 或 `extension`。Type Module 在此基础上扁平投影可过滤和检索字段，并生成向量。Qdrant 不保存 Override 基础摘要、备注、tombstone、包依赖、内容文件路径、用户状态结构或标注证据。

## 12. 同步与一致性

用户编辑顺序：

```text
验证修改
  → 原子保存用户 JSON
  → 计算有效条目和 revision
  → 同步 Qdrant
  → 返回实时同步报告
```

JSON 保存成功但 Qdrant 失败时：

- 用户修改仍算保存成功，不回滚 JSON；
- 对应包状态为 dirty；
- UI 显示“内容已保存，但索引同步失败”；
- 自动重试或用户手动重新同步；
- 未来检索不得继续使用该包旧索引。

同步粒度：

- 日常新增、修改、删除：按条目增量 upsert/delete；
- 官方包更新：按包比较 entry ID 与 revision；
- 索引指纹变化、实际索引无法验证或用户请求：全量重建。

索引指纹至少包括：

```json
{
  "index_schema_version": 1,
  "projection_version": 1,
  "embedding_model_id": "multilingual-e5-small",
  "embedding_model_hash": "sha256:...",
  "vector_size": 384,
  "distance": "cosine"
}
```

## 13. 后台启动与 readiness

主界面显示后由单一后台任务执行 `reconcile_all()`：

- worker 扫描实际 Qdrant point 的 ID、revision 与索引指纹，并与当前有效 JSON 比较；
- source revision 与实际索引一致时不加载 embedding 模型；
- 不一致时才加载模型并同步；
- 同时只允许一个索引任务，后续编辑排队合并；
- 退出时未完成工作由下次启动重新对账；
- 查看器显示进度、错误并提供重新同步和全量重建入口。

worker 通过 stdout 输出逐行 JSON，`WorldbookSyncController` 使用 `QProcess` signals 实时解析；stderr 和常规日志保存人类诊断。最后一条事件直接携带完整 `SyncReport`，进程 exit code 表达成功、失败或锁占用。第一版不写 `sync-state.json`、结构化结果文件或活动 operation 文件；下次启动始终以权威 JSON 与实际 Qdrant 内容为准重新判断。

```json
{"protocol_version":1,"event":"started","operation":"reconcile_all"}
{"protocol_version":1,"event":"progress","current":25,"total":120}
{"protocol_version":1,"event":"completed","status":"ready","report":{}}
```

同步 worker 使用 `filelock` 在独立进程中互斥。锁被占用时 worker 定期输出 `waiting_for_lock` 事件并等待，最长等待 10 分钟；获取锁后才重新读取官方包和用户 JSON，不复用等待前的同步计划。等待中的 worker 可以由 UI 取消，已经持锁修改 Qdrant 的 worker 不提供强制终止入口。多个等待 worker 依次执行是允许的，后续 worker 若发现实际索引已一致应快速退出且不加载 embedding。

UI 不创建直接操作 Qdrant 的 `QThread`。`QProcess` 自身提供异步 signals，Qt 主线程只负责启动进程、解析小型 NDJSON 事件和刷新控件，禁止调用 `waitForFinished()`、`subprocess.run()` 或其他阻塞等待；加载模型、扫描索引、等待文件锁和同步操作全部发生在 worker 进程。

结构化 readiness 至少包含：

```text
overall: initializing | ready | degraded | unavailable
qdrant_available
embedding_available
index_schema_compatible
packages[package_id]
```

包级状态：

```text
indexing | ready | degraded | dirty | failed
source_revision
observed_index_revision
usable_entry_count
isolated_entry_count
```

这些 revision 在本次检查中从 JSON 与实际 Qdrant point 聚合计算，不是另一份持久状态。

- ready：允许检索；
- degraded：存在隔离条目，但其他内容可用；
- indexing、dirty、failed：禁止使用该包；
- 未来 `can_retrieve(root_package_id)` 必须检查整个依赖闭包；
- 不得捕获异常后返回空列表冒充零命中。

上述状态只控制索引与未来 RAG，不控制 JSON 查看和编辑能力。

## 14. 故障隔离

包结构错误整包隔离：

- manifest 无法解析或 format 不支持；
- 内容文件摘要错误或路径不安全；
- 文件整体无法解析；
- 包内重复 entry ID；
- 依赖缺失或形成循环。

包完整验证通过后才允许修改其索引。失败包旧索引不可继续使用，并尽力按 `package_id` 删除；删除失败则保持 dirty。其他独立包继续对账，依赖失败包的根包未来不可检索。

条目错误只隔离相应条目：

- 未知 `entry_type`；
- 不支持的条目 Schema；
- 迁移、当前验证、embedding 文本或投影失败；
- 不兼容 Override。

## 15. 尚未冻结的内容

以下内容仍需后续设计或在条目 Schema 稳定时确认：

- 三种 `content` 的最终字段与可编辑字段；
- 实际程序资源目录和用户数据目录路径；
- 与 Qt 主界面后台任务设施的具体集成；
- 发布工具如何维护第一次分配的官方 entry ID；
- 对话 RAG 接入、根包选择、剧情节点 UI 和查询策略；
- 是否以及何时把 Character Thought 纳入可发布世界书。

## 16. 测试策略

逻辑测试继续使用 fake embedding 和 `InMemoryWorldbookIndex`，覆盖包校验、有效条目合并、Override 冲突、同步计划、锁竞争和可控故障分支。真实集成测试使用本地 `multilingual-e5-small` 与 Qdrant 临时目录，覆盖正式 JSON 到 embedding、point/revision、检索、修改、删除和重建的完整链路，并作为发布前必跑项。

真实测试不比较完整浮点向量，而检查向量维度、upsert、预期 top-k、revision 更新以及增量同步与全量重建后的条目集合一致性。建议使用 `worldbook_real_embedding` pytest marker 区分发布前真实链路测试。

## 17. UI 分阶段实现

三类条目的最终字段和可编辑字段尚在标注流程中探索最佳设计，因此第一阶段不得以完成固定表单为前提，也不得为了 UI 进度提前承诺 Schema v1。这是阶段性依赖，不是永久排除编辑功能。

可以先完成：

- `WorldbookArea` 导航和总体布局；
- 包列表、版本、依赖和内容/索引可用性；
- 有效条目列表、类型/来源徽标、搜索和筛选；
- adapter 驱动的通用只读详情；
- 同步进度、错误、重新同步和全量重建；
- 官方条目隐藏/恢复等不依赖类型字段的动作；
- 当 Type Module 已提供编辑描述时，按能力逐步启用表单。

需要等待相应 v0 Type Module 明确编辑描述后再完成：

- 三类条目的全部编辑控件；
- 新建用户扩展所需的类型专属必填字段；
- Override 的完整编辑与迁移交互；
- 依赖类型语义的字段搜索、校验提示和冲突比较。

缺少编辑描述时，条目以只读方式展示并明确提示“当前条目格式尚未开放编辑”，不能回退为绕过校验的原始 JSON 编辑器。

每种条目类型可以独立解锁编辑能力，不要求三类同时冻结。解锁条件是该类型在标注侧形成一个足够稳定的当前字段模型，能够定义必填项、验证规则、embedding 输入、Qdrant 投影和编辑字段描述；随后补齐 Type Module 及相应测试即可启用该类型的 Override、用户扩展创建和完整表单。首次公开发布前再决定是否将其从实验性 Schema v0 冻结为 v1。
