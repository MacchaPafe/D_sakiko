# Qdrant Schema Models 实现计划

## 目标

在 `GPT_SoVITS/rag/models.py` 中实现一组面向 Qdrant 的 schema 数据类，用于承载以下 collection 的记录结构：

- `story_events`
- `character_relations`
- `lore_entries`

本轮只输出设计与实施计划，不开始编写 `models.py`。

---

## 一、建模原则

### 1. 这是 Qdrant schema，不是关系型数据库 schema

Qdrant 的核心是：

- `payload` 承载结构化字段
- `vector` 承载 embedding
- `filter` 做确定性筛选
- `search/query` 将向量检索与 payload 条件组合

因此，`models.py` 应优先保证：

- payload 字段平铺且稳定
- 字段名与 Qdrant payload key 尽量一一对应
- 序列化到 payload 时不需要复杂转换
- 后续构造 Qdrant filter 时字段路径清晰

结论：

- 文档实体类应直接反映一条 Qdrant 记录
- 通用逻辑可以抽到基类或 mixin，但最终 payload 字段仍保持扁平
- 不建议把 `visible_from/visible_to`、`series/season` 再包成深层嵌套对象

### 2. 文档实体和查询条件要分层

最佳实践不是把 Qdrant 的 `Filter/FieldCondition/Range/Match...` 直接塞进文档实体，而是分为两层：

- 文档实体 dataclass：描述一条 collection 记录长什么样
- 查询条件 dataclass：描述“当前要筛哪些条件”

建议：

- `models.py` 负责实体 dataclass、枚举、轻量验证、payload 序列化接口
- 真正把查询条件转换成 `qdrant_client.models.Filter` 的逻辑，放到后续 repository / adapter 层

### 3. 枚举只用于稳定常量

适合做 `Enum` 的：

- collection 名
- `series_id`
- `season_id`
- `canon_branch`
- `scope_type`
- `character_id`

不适合做 `Enum` 的：

- `episode`
- `time_order`
- `visible_from`
- `visible_to`
- `importance`

原因是后者属于普通业务数据，而不是有限、稳定、可枚举的常量集合。

### 4. Enum 的成员名和序列化值要分开设计

最佳实践是：

- 成员名给 Python 开发者读，要求通俗、稳定
- 成员值给 payload / 存储层用，要求稳定、可序列化

例如：

- `SeasonId.ONE`, `SeasonId.TWO`, `SeasonId.THREE`
- `SeriesId.BANG_DREAM_1`, `SeriesId.ITS_MYGO`, `SeriesId.AVE_MUJICA`
- `CharacterId.SAKIKO`, `CharacterId.ANON`, `CharacterId.TOMORI`

它们的 `.value` 应保存最终进入 payload 的稳定 id，而不是展示文案。

### 5. 角色枚举手写定义，不直接依赖 UI 运行时常量

`ui_constants.py` 中的 `char_info_json` 是角色来源，但 `models.py` 不应在运行时直接依赖它。

建议：

- 在 `models.py` 中显式手写 `CharacterId` 枚举
- 后续测试再检查它是否与 `char_info_json` 同步

这样可以避免：

- RAG 层依赖 UI 层
- UI 常量改动意外影响后端 schema

### 6. 向量不是 schema 主体，`retrieval_text` 才是

在业务 schema 层，真正稳定的是：

- `retrieval_text`
- payload 字段

embedding 向量是运行时生成结果，因此推荐：

- 实体 dataclass 中不把 `vector` 作为必填持久字段
- 在运行时基于 `retrieval_text` 动态生成 vector
- 如果后续需要构造 Qdrant point，使用独立通用函数处理，而不是为每个类分别加方法

### 7. dataclass 需要配合轻量验证

这些 schema 很适合用 dataclass，但不应只是“把字段列出来”，还应做最小必要验证，例如：

- `retrieval_text` 不能为空
- `title` / `summary` / `content` 等核心文本不能为空
- `visible_from <= visible_to`
- `participants` 至少一个角色
- `scope_type` 与 `series_ids/season_ids` 一致
- `subject_character_id != object_character_id`

建议 dataclass 选项：

- `slots=True`
- `kw_only=True`

第一版先不使用 `frozen=True`，保留导入阶段灵活性。

---

## 二、建议的 `models.py` 结构

### 1. 枚举层

建议至少定义这些枚举：

- `CollectionName(str, Enum)`
  - `STORY_EVENTS = "story_events"`
  - `CHARACTER_RELATIONS = "character_relations"`
  - `STYLE_SAMPLES = "style_samples"`
  - `LORE_ENTRIES = "lore_entries"`

- `SeriesId(str, Enum)`
  - `BANG_DREAM_1 = "bang_dream_1"`
  - `BANG_DREAM_2 = "bang_dream_2"`
  - `BANG_DREAM_3 = "bang_dream_3"`
  - `ITS_MYGO = "its_mygo"`
  - `AVE_MUJICA = "ave_mujica"`

- `SeasonId(int, Enum)`
  - `ONE = 1`
  - `TWO = 2`
  - `THREE = 3`

- `CanonBranch(str, Enum)`
  - `MAIN = "main"`
  - `GAME = "game"`

- `ScopeType(str, Enum)`
  - `SERIES = "series"`
  - `GLOBAL = "global"`

- `CharacterId(str, Enum)`
  - 按 `GPT_SoVITS/ui_constants.py` 的 `char_info_json` 完整手写列出
  - 成员名尽可能统一为日语罗马音的大写形式
  - 成员值使用稳定角色 id，并尽可能统一到罗马音拼写

关于 `CharacterId` 的更具体原则：

- 优先采用项目中已有的 `romaji` 作为 `.value`
- 尽可能统一到罗马音拼写
- 如果某个角色在项目里本身长期采用官方艺名或英文写法，则优先沿用稳定官方写法，而不是再人为发明新 id

### 2. 通用基础 dataclass

建议只保留少量、低复杂度的基础类，避免过度继承。

可选方案：

- `BaseQdrantDocument`
  - 提供通用接口：
    - `collection_name`
    - `retrieval_text`
    - `tags`
    - `to_payload()`
    - `from_payload()`

- `TimedVisibilityMixin`
  - 适用于必须具备时间窗口的实体
  - 字段：
    - `visible_from`
    - `visible_to`
    - `canon_branch`
  - 提供验证：
    - `visible_from <= visible_to`

说明：

- `lore_entries` 的时间字段可选，因此不适合强行继承“时间必填”的基类
- mixin 应尽量轻，不要做太强的字段假设

### 3. 核心实体 dataclass

建议实现 3 个主实体：

- `StoryEventDocument`
- `CharacterRelationDocument`
- `LoreEntryDocument`

每个实体：

- 字段名尽量与 Qdrant payload 一致
- 角色 / 系列 / 时间线 / 分支相关字段使用枚举类型
- 列表字段在 Python 层直接保留为 `list[...]`
- `to_payload()` 时统一把 enum 转换为原始值

### 4. 查询上下文 dataclass

为了配合后续 filter 构造，建议定义运行时上下文对象，但不要和文档实体混为一谈。

建议的上下文对象：

- `RetrievalContext`
  - 当前时间点
  - 当前角色
  - 当前系列
  - 当前季
  - 当前分支

- `StoryEventQuery`
  - 是否要求按角色过滤
  - 是否限定系列 / 季 / 分支

- `CharacterRelationQuery`
  - 普通模式 / 小剧场模式
  - 小剧场双角色

- `LoreQuery`
  - 当前作品范围
  - 是否要求时间过滤

这些类只负责描述查询需求，不负责直接生成 Qdrant SDK 对象。

---

## 三、三个实体的具体设计建议

### 1. `StoryEventDocument`

字段建议：

- `season_id: SeasonId`
- `series_id: SeriesId`
- `episode: int`
- `time_order: int`
- `visible_from: int`
- `visible_to: int`
- `canon_branch: CanonBranch`
- `title: str`
- `summary: str`
- `participants: list[CharacterId]`
- `importance: int`
- `tags: list[str]`
- `retrieval_text: str`

设计重点：

- `participants` 直接保留为列表，方便映射到 Qdrant 的数组过滤
- 不建议单独建 `Participant` 子对象
- `title/summary/tags` 是业务字段，`retrieval_text` 是唯一向量检索文本

建议验证：

- `participants` 非空
- `importance >= 1`
- `title/summary/retrieval_text` 非空

### 2. `CharacterRelationDocument`

字段建议：

- `subject_character_id: CharacterId`
- `object_character_id: CharacterId`
- `season_id: SeasonId`
- `series_id: SeriesId`
- `visible_from: int`
- `visible_to: int`
- `canon_branch: CanonBranch`
- `relation_label: str`
- `state_summary: str`
- `speech_hint: str`
- `object_character_nickname: str`
- `tags: list[str]`
- `retrieval_text: str`

设计重点：

- 必须显式区分 `subject` 与 `object`
- 这是关系的有向记录，不能抽象成无方向边

建议验证：

- 两个角色字段都必须存在
- `relation_label/state_summary/retrieval_text` 非空
- `subject_character_id != object_character_id`，相同则直接视为非法输入

### 3. `LoreEntryDocument`

字段建议：

- `scope_type: ScopeType`
- `series_ids: list[SeriesId] | None`
- `season_ids: list[SeasonId] | None`
- `visible_from: int | None`
- `visible_to: int | None`
- `canon_branch: CanonBranch`
- `title: str`
- `content: str`
- `retrieval_text: str`
- `tags: list[str]`

设计重点：

- `series_ids`、`season_ids`、时间窗口都可选，这是它和前两个实体最不同的地方
- 不适合强行继承“所有字段必填”的时间基类
- 必须围绕 `scope_type` 做一致性验证

建议验证：

- 当 `scope_type == ScopeType.GLOBAL` 时，`series_ids/season_ids` 可以为空
- 当 `scope_type == ScopeType.SERIES` 时，`series_ids` 至少一个
- 如果只给了 `visible_from` 或 `visible_to` 其中一个，允许存在，由 filter 构造层分别处理上下界

---

## 四、序列化与反序列化设计

### 1. `to_payload()`

每个文档类建议提供 `to_payload()`：

- 返回 `dict[str, Any]`
- 将所有枚举转成 `.value`
- 将 `list[Enum]` 转成原始值列表
- 跳过值为 `None` 的可选字段

好处：

- payload 结构清晰
- 与 Qdrant upsert 接口自然对接
- 测试时可以直接比较 dict

### 2. `from_payload()`

建议同时提供 `from_payload()` 或 `from_dict()`：

- 将原始 payload 恢复为强类型对象
- 便于调试、回读、以及离线数据校验

### 3. Point 构造逻辑暂不放进文档类

如果后续需要把文档对象转成 Qdrant point，建议提供独立通用函数，例如：

- `build_point_struct(document, point_id, vector)`

而不是在每个文档类中分别增加方法。

原因：

- `point_id` 是 Qdrant 存储层概念
- `vector` 是 embedding 层概念
- 两者都不是业务 schema 的主字段
- 通用函数更利于统一 point 构造和后续批量导入逻辑

---

## 五、与 Qdrant filter API 的对齐方式

Qdrant 查询常见会落到这些模式：

- 标量相等匹配
- 列表包含匹配
- 数值范围匹配
- 多条件 `must/should/must_not`

因此 schema 设计必须天然支持这些操作。

### 1. 字段名保持与 payload 一致

例如直接保留：

- `subject_character_id`
- `object_character_id`
- `series_id`
- `season_id`
- `visible_from`
- `visible_to`

这样后续写 filter 时不会出现额外映射层。

### 2. 列表字段保持原生列表

例如：

- `participants: list[CharacterId]`
- `series_ids: list[SeriesId]`
- `season_ids: list[SeasonId]`
- `tags: list[str]`

这样最容易适配 Qdrant 对数组字段的匹配方式。

### 3. 时间窗口不要封成对象字段

不要设计成：

- `visibility: TimeWindow(from=..., to=...)`

因为后续 Qdrant filter 仍然要针对 `visible_from`、`visible_to` 两个 payload key 写条件。

可以做的折中是：

- 保留扁平字段
- 额外提供只读辅助属性或校验函数

### 4. 小剧场模式要在查询层体现，而不是塞进实体层

“小剧场模式下 `character_relations` 直接插入，不走 RAG” 是查询策略，不是实体结构。因此：

- 不应在 `CharacterRelationDocument` 里增加模式字段
- 应由后续查询条件对象或 repository 决定走哪种查询路径

---

## 六、角色枚举的具体落地策略

### 1. `CharacterId` 的来源

来源明确为：

- `GPT_SoVITS/ui_constants.py`
- `char_info_json`

### 2. 成员命名原则

建议：

- `CharacterId.KASUMI = "kasumi"`
- `CharacterId.SAKIKO = "sakiko"`
- `CharacterId.TOMORI = "tomori"`
- `CharacterId.ANON = "anon"`
- `CharacterId.RANA = "rana"`

总体原则：

- 成员名尽可能统一为标准罗马音的大写形式
- `.value` 尽可能统一到罗马音拼写
- 对于项目中已长期采用官方艺名或英文写法的角色，优先采用稳定官方写法

例如特殊角色可以采用：

- `CharacterId.LAYER = "layer"`
- `CharacterId.MASKING = "masking"`
- `CharacterId.PAREO = "pareo"`
- `CharacterId.CHUCHU = "chuchu"`

### 3. 与 `char_info_json` 的同步方式

推荐补一个测试，而不是运行时 import：

- 读取 `char_info_json`
- 比较其中的 `romaji` 集合与 `CharacterId` 枚举值集合是否一致

这样既满足“角色来源来自 `ui_constants.py`”，又不把 RAG schema 绑死到 UI 模块。

---

## 七、实施步骤

### 第 1 步：梳理最终枚举集合

完成内容：

- 确认 `SeriesId` 的底层值
- 确认 `SeasonId` 的底层值
- 确认 `CanonBranch` 集合
- 确认 `ScopeType` 集合
- 从 `char_info_json` 整理完整 `CharacterId`

关键决策：

- `CharacterId` 使用正式版 id 作为 `.value`
- 不考虑历史兼容别名

### 第 2 步：实现基础类型和校验辅助函数

完成内容：

- `BaseQdrantDocument`
- 必要的验证辅助
- 通用的 enum / list enum 序列化辅助函数

目标：

- 避免 3 个实体重复写同样的 `to_payload()` 转换逻辑

### 第 3 步：实现三个主实体 dataclass

完成内容：

- `StoryEventDocument`
- `CharacterRelationDocument`
- `LoreEntryDocument`

要求：

- 使用 `@dataclass`
- 开启 `slots=True, kw_only=True`
- 在 `__post_init__` 中做基础合法性校验

### 第 4 步：实现反序列化接口

完成内容：

- `from_payload()` / `from_dict()`

目标：

- 支持离线数据校验
- 支持从 Qdrant 命中结果反构造成强类型对象

### 第 5 步：补查询上下文类

完成内容：

- `RetrievalContext`
- `StoryEventQuery`
- `CharacterRelationQuery`
- `LoreQuery`

目标：

- 为下一步 filter builder 做准备
- 避免 repository 层充斥一长串散装参数

### 第 6 步：补最小测试

建议测试内容：

- 枚举序列化正确
- `to_payload()` 输出字段名正确
- `from_payload()` 可逆
- 非法时间窗口抛错
- `LoreEntryDocument` 的 `scope_type` 一致性校验
- `CharacterRelationDocument` 在 `subject == object` 时抛错
- `CharacterId` 与 `char_info_json` 同步检查

---

## 八、暂不建议在第一版做的事情

为了让第一版 `models.py` 保持清晰，不建议现在就做：

- 不实现 `style_samples` 的完整实体类
- 不把 Qdrant SDK 的 `Filter` 对象直接写进实体方法
- 不把 embedding 向量做成实体必填字段
- 不做过深的继承体系
- 不为了“看起来统一”而把可选时间窗口硬塞进统一基类

---

## 九、第一版交付范围建议

`models.py` 第一版建议包含：

- 枚举：
  - `CollectionName`
  - `SeriesId`
  - `SeasonId`
  - `CanonBranch`
  - `ScopeType`
  - `CharacterId`

- 文档类：
  - `StoryEventDocument`
  - `CharacterRelationDocument`
  - `LoreEntryDocument`

- 通用方法：
  - `to_payload()`
  - `from_payload()`
  - 基础校验

- 轻量查询类：
  - `RetrievalContext`
  - 各 collection 的 query spec

不包含：

- `style_samples` 的正式实现
- repository
- embedding 逻辑
- qdrant client 的实际查询执行逻辑

---

## 十、最终结论

这套 schema 的最佳实践不是“把 Qdrant 当 SQL 表来建模”，而是：

- 用 `Enum` 固化稳定业务常量
- 用扁平 dataclass 表达 Qdrant payload 文档
- 用独立查询条件类表达检索上下文
- 用 adapter / repository 层去生成真正的 Qdrant filter 和 query

这样能同时满足：

- 和 Qdrant API 风格对齐
- Python 类型清晰
- 后续扩展空间明确
- 查询逻辑不和 schema 混杂
- UI 常量、RAG schema、Qdrant 查询三层职责清楚
