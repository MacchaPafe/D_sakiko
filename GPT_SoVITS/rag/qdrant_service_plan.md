# Qdrant Service 层接口规划

## 目标

本文档规划 `GPT_SoVITS/rag` 下 Qdrant service 层未来应提供的公开接口与内部职责边界。

目标不是直接写实现代码，而是明确：

- service 层应该管理什么
- 对外暴露哪些 typed 接口
- 哪些逻辑应该属于 service 层，哪些不应该
- Qdrant 作为向量数据库时，与普通关系型数据库接口设计的关键差异

---

## 一、设计前提

### 1. 当前项目的 embedding 模型来源

参考 [qdrant_test.py](/Users/liyanxiao/Documents/Python%20Files/D_sakiko/qdrant_test.py)，当前项目已经明确：

- 使用 `sentence_transformers.SentenceTransformer`
- 当前测试模型路径为 `GPT_SoVITS/pretrained_models/multilingual-e5-small`
- 向量维度通过 `get_sentence_embedding_dimension()` 获取
- 向量距离使用 `Distance.COSINE`

因此，service 层需要同时管理：

- `QdrantClient`
- `SentenceTransformer` 模型
- 由模型维度推导出的 collection 向量配置

### 2. 当前 schema 来源

service 层需要围绕 [models.py](/Users/liyanxiao/Documents/Python%20Files/D_sakiko/GPT_SoVITS/rag/models.py) 工作，至少支持：

- `StoryEventDocument`
- `CharacterRelationDocument`
- `LoreEntryDocument`

并使用其中定义的：

- `CollectionName`
- `SeriesId`
- `SeasonId`
- `CanonBranch`
- `ScopeType`
- `CharacterId`
- 各 query context dataclass

### 3. `models.py` 中的文档对象不包含 point id

这是一个关键约束。

当前 dataclass 描述的是 payload schema，不是完整的 Qdrant point。  
而 Qdrant 的写入、更新、删除都依赖 point id。

因此 service 层应当明确采用包装对象方案：

- `PointRecord[TDocument]`

建议字段：

- `point_id: str | None`
- `document: TDocument`

结论：

- point id 是必要字段
- point id 不应回写进 `models.py` 的文档 dataclass
- Qdrant 底层不会替我们自动生成 point id
- 但 service 层可以在调用方未提供时自动生成默认 point id

进一步建议：

- 如果调用方有稳定外部 id，优先显式传入
- 如果调用方只想快速导入数据，service 可自动补齐 point id
- 自动生成 id 适合初始化导入或追加写入
- 显式 id 更适合后续精确更新、覆盖写入和按 id 删除

---

## 二、Qdrant service 不应照搬关系型数据库 service

### 1. 不能用“表 + SQL + join”思路设计

Qdrant 不是关系型数据库，因此 service 层设计应体现以下差异：

- collection 间不会 join
- 过滤逻辑依赖 payload filter，而不是 SQL where
- 检索核心不是“按主键/索引查表”，而是“向量检索或关键词触发 + filter”
- tag / 关键词触发本质上是 payload 匹配，不是 SQL 式全文索引

结论：

- service 层应以“collection + retrieval context + query mode”为中心
- 不应抽象成传统 CRUD Repository 的 SQL 风格接口

### 2. embedding 模型必须被视为 service 的一部分

虽然 embedding 模型不是数据库本体，但对 Qdrant 来说：

- 写入前要把 `retrieval_text` 编码成向量
- 查询前要把 query text 编码成向量

因此 embedding 模型在工程上必须由 service 层统一管理，否则：

- 写入与查询可能使用不同模型
- vector 维度可能不一致
- 无法统一缓存、预热和批量 encode 策略

### 3. collection schema 需要显式注册

Qdrant 本身对 payload 没有像关系型数据库那样强 schema 约束，但工程上仍然需要在 service 层维护一份显式 collection 注册表，描述：

- collection 名称
- 对应文档类型
- 需要建立 payload index 的字段
- 向量字段来源

否则 service 会很快演化成大量硬编码字符串。

---

## 三、建议的模块职责拆分

我建议后续实现时，将 service 层拆为三层：

### 1. `EmbeddingProvider`

职责：

- 加载 `SentenceTransformer`
- 返回 embedding 维度
- 提供单条 / 批量 encode
- 负责模型懒加载与复用

这是“向量化能力提供者”，不直接碰 Qdrant。

### 2. `CollectionRegistry`

职责：

- 维护三张 collection 的静态定义
- 维护各 collection 的 payload index 字段
- 维护 document type 与 collection name 的映射

这是“schema 元信息注册表”，不直接做数据库 IO。

### 3. `QdrantRagService`

职责：

- 管理 `QdrantClient`
- 管理 `EmbeddingProvider`
- 负责建库、建 collection、建 payload index
- 负责批量 upsert、delete、query
- 对外暴露 typed 公共接口

这是最终对业务层公开的 façade。

---

## 四、建议新增的内部类型

为了让 service 接口保持清晰，建议补充以下辅助 dataclass / enum。

### 1. `RagServiceConfig`

用途：

- 统一描述 service 层的初始化配置

建议字段：

- `qdrant_location: str`
- `embedding_model_path: str`
- `distance: str | Distance`
- `prefer_grpc: bool = False`
- `default_batch_size: int = 64`
- `default_top_k: int = 5`

说明：

- `qdrant_location` 需要支持 `":memory:"`、本地 path、或远端 url
- 默认模型路径可指向 `GPT_SoVITS/pretrained_models/multilingual-e5-small`

### 2. `PointRecord[TDocument]`

用途：

- 用于把 `point_id` 和文档 payload 绑定在一起

建议字段：

- `point_id: str | None`
- `document: TDocument`

说明：

- `point_id` 允许为空
- 当 `point_id is None` 时，由 service 层自动生成
- 若业务方已经有稳定主键，仍然建议显式传入

### 3. `RagDatasetBundle`

用途：

- 用于一次性初始化整个数据库

建议字段：

- `story_events: list[PointRecord[StoryEventDocument]]`
- `character_relations: list[PointRecord[CharacterRelationDocument]]`
- `lore_entries: list[PointRecord[LoreEntryDocument]]`

### 4. `RetrievalMode`

用途：

- 显式指定当前查询采用哪种触发模式

建议取值：

- `VECTOR`
- `KEYWORD`
- `DIRECT`

说明：

- `VECTOR` 表示标准向量检索
- `KEYWORD` 表示纯关键词 / tag 匹配
- `DIRECT` 只适用于 `character_relations` 的小剧场直取模式

### 5. `QueryHit[TDocument]`

用途：

- 统一表示一条命中结果

建议字段：

- `point_id: str`
- `document: TDocument`
- `score: float | None`
- `source: str`

其中 `source` 可取：

- `vector`
- `keyword`
- `direct`

### 6. `UpsertReport` / `DeleteReport`

用途：

- 返回批量写入 / 删除的统计信息

建议字段：

- 成功数量
- 失败数量
- 失败 point id 列表
- 目标 collection 名

---

## 五、建议的公开接口

下面的接口是 service 层建议对外暴露的核心能力。

## 1. 初始化与生命周期

### `__init__(config: RagServiceConfig) -> None`

职责：

- 保存配置
- 延迟初始化 client 与 embedding 模型

说明：

- 构造函数不建议直接重型加载模型
- 保持轻量，真正初始化由 `initialize()` 或首次使用触发

### `initialize() -> None`

职责：

- 初始化 `QdrantClient`
- 加载 embedding 模型
- 获取向量维度
- 准备 collection registry

### `close() -> None`

职责：

- 释放底层 client / 模型引用

### `health_check() -> dict`

职责：

- 返回当前 service 是否可用
- 返回模型是否已加载
- 返回 qdrant 是否可连通
- 返回当前向量维度

这个接口建议保留，工程上很有用。

---

## 2. 全新建库

### `create_database(bundle: RagDatasetBundle, drop_existing: bool = True) -> UpsertReport`

这是最重要的初始化接口，对应你的第一个需求。

职责：

1. 确保 embedding 模型已加载
2. 获取向量维度
3. 为三张表创建或重建 collection
4. 建立 payload index
5. 批量计算各文档的 `retrieval_text` 向量
6. 批量插入三张表的数据

建议行为：

- 默认 `drop_existing=True`
- 若某 collection 已存在则删除后重建
- 批量 encode 与批量 upsert 分批执行
- 对未提供 `point_id` 的记录，由 service 自动生成 point id

为什么接口名叫 `create_database`：

- 它就是对外“创建全新数据库”的主入口
- 即使内部实现可能包含“已存在则删后重建”，对调用方来说也更直观

### `ensure_collections_exist() -> None`

职责：

- 若 collection 不存在则创建
- 若存在则不删除

这个接口适合增量部署，不适合全量建库。

---

## 3. collection 级增量写入接口

### `upsert_story_events(records: list[PointRecord[StoryEventDocument]]) -> UpsertReport`

职责：

- 对 `story_events` collection 批量写入或更新数据
- 自动基于 `retrieval_text` 计算向量
- 若某条记录未提供 `point_id`，则自动生成

### `upsert_character_relations(records: list[PointRecord[CharacterRelationDocument]]) -> UpsertReport`

职责：

- 对 `character_relations` collection 批量写入或更新数据
- 自动计算向量
- 若某条记录未提供 `point_id`，则自动生成

### `upsert_lore_entries(records: list[PointRecord[LoreEntryDocument]]) -> UpsertReport`

职责：

- 对 `lore_entries` collection 批量写入或更新数据
- 自动计算向量
- 若某条记录未提供 `point_id`，则自动生成

### `upsert_documents(collection_name, records) -> UpsertReport`

这是一个可选的通用底层接口。

建议：

- 公开层优先提供三个 typed 接口
- 内部再复用一个泛型 `upsert_documents(...)`

这样既兼顾类型安全，也不重复实现逻辑。

---

## 4. 删除接口

删除接口保持 collection 级通用设计即可，不需要再拆成三个 typed 删除接口。

### `delete_by_point_ids(collection_name: CollectionName, point_ids: list[str]) -> DeleteReport`

职责：

- 根据 point id 批量删除数据

适用场景：

- 已知外部业务 id
- 需要精准删除单条或多条记录

### `delete_by_filter(collection_name: CollectionName, delete_filter) -> DeleteReport`

职责：

- 根据 filter 条件批量删除

适用场景：

- 删除某个系列 / 某个时间线 / 某个角色相关数据
- 删除一次导入批次中的全部数据

结论：

- 删除接口不必再拆成 `delete_story_events(...)` 这类 typed 形式
- `collection_name` 已经足够说明删除目标

---

## 5. 三个公开查询接口

这是 service 层的核心业务能力。

这里有一个关键约定：

- 不做“关键词补充向量检索”的混合模式
- 由调用方显式指定查询模式

原因：

- 某些场景只希望看纯关键词触发结果
- 某些场景只希望看纯向量检索结果
- 混合模式会让返回来源不够可控，也容易干扰上层策略

### `query_story_events(query_text: str | None, context: RetrievalContext, options: StoryEventQuery, query_mode: RetrievalMode = RetrievalMode.VECTOR, tag_keywords: list[str] | None = None, top_k: int | None = None) -> list[QueryHit[StoryEventDocument]]`

职责：

- 查询 `story_events`
- `VECTOR` 模式下走向量检索
- `KEYWORD` 模式下走纯关键词 / tag 匹配
- 自动叠加时间 / 角色 / 系列 / 时间线 / 分支过滤

参数约束：

- `VECTOR` 模式要求提供 `query_text`
- `KEYWORD` 模式要求提供 `tag_keywords`

### `query_character_relations(query_text: str | None, context: RetrievalContext, options: CharacterRelationQuery, query_mode: RetrievalMode = RetrievalMode.VECTOR, tag_keywords: list[str] | None = None, top_k: int | None = None) -> list[QueryHit[CharacterRelationDocument]]`

职责：

- 查询 `character_relations`
- `VECTOR` 模式下走向量检索 + filter
- `KEYWORD` 模式下走纯关键词 / tag 匹配 + filter
- `DIRECT` 模式下走小剧场双角色直取，不做 embedding

参数约束：

- `VECTOR` 模式要求提供 `query_text`
- `KEYWORD` 模式要求提供 `tag_keywords`
- `DIRECT` 模式要求 `CharacterRelationQuery` 中已提供双角色直取参数

### `query_lore_entries(query_text: str | None, context: RetrievalContext, options: LoreQuery, query_mode: RetrievalMode = RetrievalMode.VECTOR, tag_keywords: list[str] | None = None, top_k: int | None = None) -> list[QueryHit[LoreEntryDocument]]`

职责：

- 查询 `lore_entries`
- `VECTOR` 模式下走向量检索
- `KEYWORD` 模式下走纯关键词 / tag 匹配
- 自动叠加作品范围与可选时间过滤

参数约束：

- `VECTOR` 模式要求提供 `query_text`
- `KEYWORD` 模式要求提供 `tag_keywords`

### 为什么 `tag_keywords` 仍然作为参数存在？

因为关键词提取属于上游 NLP / prompt orchestration 逻辑，不是数据库 service 的核心职责。  
service 负责“如何根据关键词匹配”，而不是“如何从用户输入中抽词”。

---

## 6. 聚合查询接口

### `query_all(query_text: str | None, context: RetrievalContext, tag_keywords: list[str] | None = None, query_modes: dict[CollectionName, RetrievalMode] | None = None, top_k_per_collection: int = 5) -> dict`

职责：

- 一次性查询三张表
- 返回按 collection 分组的结果

建议：

- 将这个接口作为正式可选公开接口保留
- 由调用方为每个 collection 显式指定 query mode
- 未指定时使用 service 默认策略

这个接口对 prompt 拼装很方便，因为业务上很可能每轮对话都会同时查：

- `character_relations`
- `story_events`
- `lore_entries`

---

## 六、建议的内部实现方式

## 1. collection registry

建议在 service 内部维护一个静态注册表，例如：

- `story_events -> StoryEventDocument + index_fields`
- `character_relations -> CharacterRelationDocument + index_fields`
- `lore_entries -> LoreEntryDocument + index_fields`

注册表至少应包含：

- collection name
- document type
- payload index 字段列表
- 向量字段来源固定为 `retrieval_text`

这样建库、校验、写入、查询都可以复用一套逻辑。

## 2. 批量 encode，而不是逐条 encode

service 层在 upsert 时应优先批量处理：

1. 从文档列表中提取 `retrieval_text`
2. `model.encode(texts, batch_size=...)`
3. 再组装 `PointStruct`

不要逐条 encode，否则性能会明显变差。

## 3. Qdrant point 组装应是私有辅助逻辑

建议内部提供私有方法，例如：

- `_build_points(records)`
- `_ensure_point_ids(records)`

职责：

- 从 `PointRecord[TDocument]` 构造 `PointStruct`
- 统一处理：
  - point id
  - vector
  - payload

建议：

- 在真正组装 `PointStruct` 之前，先补齐缺失的 point id
- point id 生成逻辑应集中在 service 内部，不要分散到上层调用方

## 4. filter builder 也应内聚在 service 或相邻模块

建议不要把 Qdrant SDK 的 filter 构造写进 `models.py`。  
更合理的归属是：

- `services.py` 中的私有方法
- 或未来单独拆出 `filter_builders.py`

例如：

- `_build_story_event_filter(context, options)`
- `_build_character_relation_filter(context, options)`
- `_build_lore_filter(context, options)`

## 5. 关键词匹配不应由 service 层负责分词

Qdrant 不像关系型数据库那样天然适合复杂文本关键词处理。  
因此 service 层应当：

1. 接收上层已经整理好的 `tag_keywords`
2. 使用 payload filter / scroll 做匹配
3. 返回该模式下的命中结果

这里不要在 service 层偷偷做复杂分词，也不要把分词逻辑塞进数据库模块。

---

## 七、与关系型数据库设计不同的关键点

### 1. 没有跨 collection 事务

`create_database()` 虽然会写三张表，但对 Qdrant 来说这不是一个 SQL 式事务。  
所以实现时要考虑：

- 中间失败后的错误报告
- 是否重试
- 是否允许部分成功

建议：

- 返回结构化 `UpsertReport`
- 明确记录每张 collection 的成功 / 失败数量

### 2. 查询返回值应保留 score

关系型数据库常返回“记录列表”。  
向量数据库查询结果还天然带有相关度 score，因此返回值最好保留：

- `document`
- `score`
- `source`

否则上层无法做后续裁剪和排序。

### 3. 写入必须和模型维度绑定

关系型数据库写入与模型无关。  
向量数据库写入前必须知道：

- 当前 embedding 模型是什么
- 向量维度是多少
- collection 的向量配置是否匹配

所以 service 初始化时必须做这一层一致性检查。

### 4. payload index 是性能配置，不是强 schema

关系型数据库里字段定义通常是强约束。  
Qdrant 中 payload index 更像性能优化配置，因此 service 需要显式维护这份信息，不能依赖数据库“自动知道”。

### 5. 查询模式是显式策略，而不是隐式混合

关系型数据库里很少需要显式区分“检索模式”。  
但在向量数据库场景中：

- 向量检索
- 关键词触发
- 小剧场 direct 命中

本质上是三种不同策略，因此应由 service 接口显式暴露出来，而不是在内部偷偷混用。

---

## 八、建议补充的额外接口

除了你列出的必需接口，我建议额外补这些：

### `count_points(collection_name: CollectionName) -> int`

用途：

- 快速查看某 collection 当前数据量

### `list_collections() -> list[str]`

用途：

- 调试和运维排查

### `get_collection_info(collection_name: CollectionName) -> dict`

用途：

- 查看向量维度
- 查看索引状态
- 查看 collection 是否存在

### `warm_up_embedding_model() -> None`

用途：

- 启动时主动加载模型
- 避免第一次写入 / 查询时冷启动卡顿

这些接口都建议保留。

---

## 九、建议的最终公开 façade

如果只保留最核心的一组公共接口，我建议最终 façade 大致如下：

### 生命周期

- `initialize()`
- `close()`
- `health_check()`
- `warm_up_embedding_model()`

### 建库

- `create_database(bundle, drop_existing=True)`
- `ensure_collections_exist()`

### 写入

- `upsert_story_events(records)`
- `upsert_character_relations(records)`
- `upsert_lore_entries(records)`

### 删除

- `delete_by_point_ids(collection_name, point_ids)`
- `delete_by_filter(collection_name, delete_filter)`

### 查询

- `query_story_events(query_text, context, options, query_mode, tag_keywords=None, top_k=None)`
- `query_character_relations(query_text, context, options, query_mode, tag_keywords=None, top_k=None)`
- `query_lore_entries(query_text, context, options, query_mode, tag_keywords=None, top_k=None)`
- `query_all(query_text, context, tag_keywords=None, query_modes=None, top_k_per_collection=5)`

### 调试 / 运维

- `count_points(collection_name)`
- `list_collections()`
- `get_collection_info(collection_name)`

---

## 十、后续实现建议

后续正式写代码时，我建议优先按这个顺序实现：

1. `RagServiceConfig`
2. `PointRecord` / `RagDatasetBundle` / `RetrievalMode` / `QueryHit`
3. `EmbeddingProvider`
4. `CollectionRegistry`
5. `QdrantRagService.initialize()`
6. `ensure_collections_exist()` / `create_database()`
7. 三个 `upsert_*`
8. 三个 `query_*`
9. `query_all()`
10. 删除接口
11. 调试与运维接口

这样做的好处是：

- 先把基础设施和类型打稳
- 再实现写入
- 最后再实现最复杂的查询策略切换逻辑

---

## 十一、结论

Qdrant service 层不应被设计成一个普通“数据库 CRUD 层”，而应被设计成一个同时管理：

- Qdrant client
- embedding 模型
- collection schema 注册表
- typed query / write façade

的统一入口。

从对外接口上看，它至少应提供：

- 全量建库
- 三张表的 typed 查询
- 三张表的增量写入
- collection 级删除
- embedding 模型生命周期管理

从内部实现上看，它需要额外处理普通关系型数据库通常不需要处理的问题：

- 向量维度一致性
- embedding 批量编码
- score 保留
- 显式查询模式切换
- point id 与 payload schema 的分离
- point id 的自动补齐策略

这就是我建议的 service 层规划方向。
