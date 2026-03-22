# Qdrant Collection 最终字段清单与查询说明

该文件描述了动漫模拟对话系统的 RAG 方案中，如何在向量数据库 qdrant 中存储数据与检索数据，即世界书数据具体存储使用的数据结构。

---

## 一、总体原则

### 1. collection 划分
共四个 collection：

1. `story_events`
2. `character_relations`
3. `style_samples`
4. `lore_entries`

### 2. 两种查询方式
系统支持两种插入来源：

#### A. RAG 向量检索
- 使用 `retrieval_text` 做 embedding
- 用用户最近消息或当前任务描述作为 query
- 在向量检索前后，都要叠加 filter 筛选

#### B. tag 关键字直接匹配
- 从当前消息中匹配 `tags`
- 命中后，将该条目作为候选插入
- 同样必须经过 filter 筛选，不能直接无条件插入

### 3. 通用 filter 原则

#### 时间筛选
四个表统一做时间筛选：

- `visible_from <= 当前时间`
- `visible_to >= 当前时间`

#### 角色筛选
- `story_events`：筛选 `participants` 或 `known_people` 包含当前角色的内容
- `character_relations`：
  - 普通模式：不强制角色筛选，只做 RAG + 时间筛选
  - 小剧场模式：直接插入 `subject_character_id / object_character_id` 符合两名角色的条目，不走 RAG
- `style_samples`：按当前角色筛选
- `lore_entries`：不做角色筛选

#### 作品范围筛选
- `lore_entries` 需要额外筛选 `series_ids` 和 `season_ids`
- 即使某些 lore 条目没有有效的时间信息，也需要保证属于当前动漫范围

### 4. 字段用途区分
每张表中的字段分为三类：

- **主数据字段**：给程序和模型使用
- **filter 字段**：用于确定性筛选
- **向量字段**：用于 embedding 检索

---

## 二、story_events

## 用途
存放剧情事件、角色经历、关键事件片段。

## 最终字段清单

| 字段名 | 类型 | 必填 | 用途 |
|---|---|---:|---|
| `season_id` | int | 是 | 事件所在季 |
| `series_id` | string | 是 | 动漫系列内部 id |
| `episode` | int | 是 | 所在集数 |
| `time_order` | int | 是 | 事件内部时间 id |
| `visible_from` | int | 是 | 从哪个时间点开始可见 |
| `visible_to` | int | 是 | 到哪个时间点为止可见 |
| `canon_branch` | string | 是 | 剧情分支，如 `main` / `game` |
| `title` | string | 是 | 事件标题 |
| `summary` | string | 是 | 事件摘要 |
| `participants` | string[] | 是 | 参与事件的角色 id |
| `importance` | int | 是 | 事件重要度，建议 1 为最高 |
| `tags` | string[] | 是 | tag 关键词匹配用 |
| `retrieval_text` | string | 是 | 向量检索文本 |

## filter 字段
- `series_id`
- `season_id`
- `episode`
- `time_order`
- `visible_from`
- `visible_to`
- `canon_branch`
- `participants`

## 向量化字段
- **只向量化 `retrieval_text`**
- 不建议直接对 `summary` 单独向量化
- `title` / `summary` / `participants` / `tags` 的信息应先整理进 `retrieval_text`

## retrieval_text 编写规范
建议包含：

1. 事件标题
2. 参与角色
3. 事件内容
4. 角色情绪或态度变化
5. 事件结果
6. tags 中最重要的几个概念

## retrieval_text 示例
```text
天台对话。参与角色：御坂美琴、上条当麻。事件内容：美琴第一次间接承认自己害怕失控，表面仍然强硬。结果：她对当麻的信任上升。标签：天台、坦白、信任变化。
```

## 查询方式

### RAG 向量检索
- query：用户最近消息 / 当前任务描述
- 检索字段：`retrieval_text`
- filter：
  - `visible_from <= 当前时间 <= visible_to`
  - `participants` 包含当前角色
  - 可选叠加 `series_id / season_id / canon_branch`

### tag 匹配
- 从消息中匹配 `tags`
- 命中后仍需做时间筛选
- 如有角色筛选需求，仍需检查 `participants`

---

## 三、character_relations

## 用途
存放“一个角色对另一个角色”的关系状态、主观看法、称呼与互动倾向。

## 最终字段清单

| 字段名 | 类型 | 必填 | 用途 |
|---|---|---:|---|
| `subject_character_id` | string | 是 | 持有想法方角色 id |
| `object_character_id` | string | 是 | 被 subject 想到的角色 id |
| `season_id` | int | 是 | 所在季 |
| `series_id` | string | 是 | 动漫系列内部 id |
| `visible_from` | int | 是 | 从哪个时间点开始可见 |
| `visible_to` | int | 是 | 到哪个时间点为止可见 |
| `canon_branch` | string | 是 | 剧情分支 |
| `relation_label` | string | 是 | 关系的简短标签 |
| `state_summary` | string | 是 | 当前关系状态摘要 |
| `speech_hint` | string | 是 | 提到对方时的说话方式提示 |
| `object_character_nickname` | string | 是 | 当前角色对目标角色的称呼 |
| `tags` | string[] | 是 | tag 关键词匹配用 |
| `retrieval_text` | string | 是 | 向量检索文本 |

## filter 字段
- `subject_character_id`
- `object_character_id`
- `series_id`
- `season_id`
- `visible_from`
- `visible_to`
- `canon_branch`

## 向量化字段
- **只向量化 `retrieval_text`**

## retrieval_text 编写规范
建议包含：

1. 主体角色名
2. 目标角色名
3. 当前关系状态
4. 互动方式
5. 说话风格提示
6. 常用称呼（若有）

## retrieval_text 示例
```text
御坂美琴对上条当麻的关系。当前状态：表面常顶嘴，实际已经认可对方可靠。说话特点：提到对方时先否认再松口。常用称呼：笨蛋。
```

## 查询方式

### 普通模式
- 直接做 RAG 向量检索
- 检索字段：`retrieval_text`
- filter：
  - `visible_from <= 当前时间 <= visible_to`
  - 不强制 subject/object 与当前角色匹配
  - 可按 `series_id / season_id / canon_branch` 缩小范围

### 小剧场模式
**不走 RAG，直接插入。**

直接取满足以下条件的条目：

- `subject_character_id` / `object_character_id` 与两位角色匹配
- `visible_from <= 当前时间 <= visible_to`
- `series_id / season_id / canon_branch` 满足要求

推荐匹配逻辑：

- `(subject=A AND object=B)`
- `(subject=B AND object=A)`

都作为候选插入。

### tag 匹配
- 普通模式下可作为补充
- 仍需时间筛选
- 小剧场模式一般不需要 tag 匹配

---

## 四、style_samples

> 暂时不在代码中具体实现。  
> 本节保留为后续测试和扩展用字段方案。

## 用途
存放角色在特定情绪 / 语气 / 场景下的典型表达方式，用于补充说话风格。

## 暂定字段清单

| 字段名 | 类型 | 必填 | 用途 |
|---|---|---:|---|
| `character_id` | string | 是 | 角色 id |
| `season_id` | int | 是 | 所在季 |
| `series_id` | string | 是 | 动漫系列内部 id |
| `visible_from` | int | 是 | 从哪个时间点开始可见 |
| `visible_to` | int | 是 | 到哪个时间点为止可见 |
| `canon_branch` | string | 是 | 剧情分支 |
| `emotion` | string | 是 | 情绪类型 |
| `style_features` | string[] | 是 | 风格特征 |
| `text` | string | 是 | 样本文本 |
| `tags` | string[] | 是 | tag 关键词匹配用 |
| `retrieval_text` | string | 是 | 向量检索文本 |

## filter 字段
- `character_id`
- `series_id`
- `season_id`
- `visible_from`
- `visible_to`
- `canon_branch`
- `emotion`

## 向量化字段
- **只向量化 `retrieval_text`**

## retrieval_text 编写规范
建议包含：

1. 角色名
2. 情绪
3. 风格特征
4. 示例台词
5. 使用场景说明

## retrieval_text 示例
```text
角色：御坂美琴。情绪：害羞。场景：日常。对象：熟人。意图：否认关心。风格特点：嘴硬、短句、先否认再表达在意。示例台词：哈？谁在担心你啊，我只是顺路而已。使用说明：适合在被关心时别扭回应。
```

## 查询方式
当前暂不实现。  
未来若启用，建议：

### RAG 向量检索
- 检索字段：`retrieval_text`
- filter：
  - `character_id == 当前角色`
  - `visible_from <= 当前时间 <= visible_to`

### tag 匹配
- 从消息中匹配 `tags`
- 命中后仍需做角色与时间筛选

---

## 五、lore_entries

## 用途
存放世界设定、术语解释、地点、组织等背景知识。

## 最终字段清单

| 字段名 | 类型 | 必填 | 用途 |
|---|---|---:|---|
| `scope_type` | string | 是 | 适用范围类型：`series` / `global` |
| `series_ids` | string[] | 否 | 适用的系列 id 列表 |
| `season_ids` | int[] | 否 | 适用的季 id 列表 |
| `visible_from` | int | 否 | 从哪个时间点开始可见 |
| `visible_to` | int | 否 | 到哪个时间点为止可见 |
| `canon_branch` | string | 是 | 剧情分支 |
| `title` | string | 是 | 条目标题 |
| `content` | string | 是 | 条目解释文本 |
| `retrieval_text` | string | 是 | 向量检索文本 |
| `tags` | string[] | 是 | tag 关键词匹配用 |

## filter 字段
- `scope_type`
- `series_ids`
- `season_ids`
- `visible_from`
- `visible_to`
- `canon_branch`

## 向量化字段
- **只向量化 `retrieval_text`**

## retrieval_text 编写规范
建议包含：

1. 术语标题
2. 别名（若以后补充字段可写入）
3. 内容解释
4. 所属类别或 tags

## retrieval_text 示例
```text
术语：学园都市。别名：Academy City。解释：以超能力开发为核心的封闭都市。类别：世界观、地点、能力开发。
```

## 查询方式

### RAG 向量检索
- 检索字段：`retrieval_text`
- filter：
  - `visible_from <= 当前时间 <= visible_to`
  - 且 `series_ids / season_ids` 与当前动漫范围匹配
- 若条目没有有效时间信息，也必须满足作品范围筛选

### tag 匹配
- 从消息中匹配 `tags`
- 命中后仍需做：
  - `series_ids / season_ids` 筛选
  - 如有时间信息，则再做时间筛选

---

## 六、统一查询流程建议

## 1. RAG 向量检索流程
1. 生成 query（当前消息、最近几轮消息或小剧场任务描述）
2. 针对每个 collection 选用 `retrieval_text` 检索
3. 为每个 collection 叠加对应 filter
4. 取 top-k
5. 将命中的条目压缩整理后插入 prompt

## 2. tag 关键字匹配流程
1. 从当前消息中提取词或短语
2. 与各 collection 的 `tags` 做精确或归一化匹配
3. 对命中条目应用 filter
4. 将通过筛选的条目作为补充插入

## 3. 推荐插入优先级
推荐优先级如下：

1. `character_relations`（普通模式命中）
2. `story_events`
3. `lore_entries`
4. `style_samples`（未来启用后）

小剧场模式下：

1. `character_relations`（直接插入）
2. `story_events`
3. `lore_entries`

---

## 七、建议建立 payload index 的字段

## `story_events`
- `series_id`
- `season_id`
- `episode`
- `time_order`
- `visible_from`
- `visible_to`
- `canon_branch`
- `participants`

## `character_relations`
- `subject_character_id`
- `object_character_id`
- `series_id`
- `season_id`
- `visible_from`
- `visible_to`
- `canon_branch`

## `style_samples`
- `character_id`
- `series_id`
- `season_id`
- `visible_from`
- `visible_to`
- `canon_branch`
- `emotion`

## `lore_entries`
- `scope_type`
- `series_ids`
- `season_ids`
- `visible_from`
- `visible_to`
- `canon_branch`

---

## 八、最终落地建议

### 已建议直接实现
- `story_events`
- `character_relations`
- `lore_entries`

### 暂缓实现
- `style_samples`

### 统一约束
- 所有 collection 都保留 `retrieval_text`
- 所有 collection 都通过 filter 补充确定性筛选
- 所有 tag 匹配都不能跳过 filter
- 小剧场模式下，`character_relations` 直接插入，不走 RAG

