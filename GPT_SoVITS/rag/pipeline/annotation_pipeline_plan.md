# Annotation Pipeline Plan

## 1. 目标

本方案用于从 `It's MyGO!!!!!` 动漫字幕中抽取三张 RAG 数据表所需的数据：

- `story_events`
- `character_relations`
- `lore_entries`

本方案只描述实施计划，不包含代码实现。

目标是建立一个可批量运行、可复核、可迭代优化的 Python 数据抽取流水线，核心思路为：

1. 使用 Python 预处理 `.ass` 字幕，清理并结构化台词与屏幕字。
2. 使用两阶段 LLM 标注：
   - 第一阶段：说话人、被提及角色、场景角色集合等基础标注。
   - 第二阶段：基于第一阶段结果，抽取三张表的结构化候选条目。
3. 使用 Python 做字段映射、时间字段生成、去重、合并、校验和最终导出。

---

## 2. 设计原则

### 2.1 不直接把原始字幕整文件喂给 LLM

原因：

- `.ass` 包含样式、位置、特效、阴影层、标题、注释、ED 歌词等噪声。
- 当前字幕 `Dialogue` 的 `Name` 字段为空，无法直接从文件确定说话人。
- 角色关系抽取依赖说话方向，必须先做结构化预处理。

因此，LLM 的输入必须是 Python 预处理后的“场景级结构化数据”。

### 2.2 两阶段 LLM 是必要的

第一阶段解决“谁在说、在说谁、现场有哪些角色、是否为内心独白”等基础问题。

第二阶段基于这些结果再抽：

- 事件摘要
- 定向角色关系
- 世界观条目

若直接一步抽三张表，`character_relations.subject_character_id` 与 `object_character_id` 容易反向或不稳定。

### 2.3 程序负责“确定性字段”，LLM 负责“语义字段”

凡是能从运行上下文、数据源配置或简单规则稳定生成的字段，都应由程序生成。

凡是需要理解剧情、总结语义、判断关系状态的字段，交给 LLM。

### 2.4 全流程保留证据链

每个候选条目都应保留其来源：

- 来自哪一集
- 哪个 scene
- 哪些 utterance
- 哪些 screen text
- LLM 原始输出

这样后续可以人工复核，也能做自动回溯和重新标注。

---

## 3. 适用范围与假设

### 3.1 当前数据源范围

当前计划以 `It's MyGO!!!!!` 动画字幕为主，系列字段固定映射到：

- `series_id = SeriesId.ITS_MYGO`
- `season_id = SeasonId.THREE`
- `canon_branch = CanonBranch.MAIN`

其中 `season_id` 不交给 LLM 判断，直接由程序固定写入 `SeasonId.THREE`。

### 3.2 当前抽取边界

可以抽取：

- 台词明确表现出的事件
- 角色之间明确表达或可稳定总结的关系状态
- 屏幕字、旁注、台词中明确出现的术语、组织、地点、团名、作品内名词

不建议直接抽取：

- 需要大量画面理解才能成立的信息
- 纯靠观众常识补全、字幕中没有显式证据的设定
- 需要跨多集高度综合、但当前只输入单 scene 无法稳定判断的关系终局

---

## 4. 建议技术栈

### 4.1 必需依赖

- `litellm`
  - 用于统一 LLM 调用接口
  - 项目已存在依赖

- `pydantic`
  - 用于中间结构和 LLM 输出的 schema 校验
  - 项目已存在依赖

### 4.2 建议新增依赖

- `pysubs2`
  - 用于解析 `.ass/.ssa` 字幕
  - 优点：成熟、轻量、直接处理 ASS 时间与文本字段，适合本项目
  - 本项目里很有必要使用，建议作为标准依赖引入

### 4.3 暂不建议依赖

- 专门的复杂 ASS 样式渲染库
  - 本任务不需要还原渲染效果，只需读取文本和时间

- 数据库
  - 当前阶段完全可以先落地为 JSONL / JSON 文件流水线

- `orjson`
  - 当前数据量不大，Python 自带 `json` 已足够

- `tenacity`
  - 当前阶段可以先用简单的手写重试逻辑，不急着额外引入依赖

---

## 5. 目录建议

建议在 `GPT_SoVITS/rag/pipeline/` 下组织如下：

```text
pipeline/
  annotation_pipeline_plan.md
  README.md                         # 后续可补
  schemas.py                        # 中间数据结构与 LLM 输出 schema
  subtitle_loader.py                # ass 解析与清洗
  scene_segmenter.py                # 场景切分
  llm_client.py                     # litellm 调用封装
  prompts/
    speaker_pass.md
    extraction_pass.md
  stages/
    stage1_speaker_annotation.py
    stage2_document_extraction.py
    normalize_documents.py
    deduplicate_documents.py
    export_documents.py
  data/
    raw/
    normalized/
    scenes/
    annotations_stage1/
    annotations_stage2/
    final/
```

本次只创建计划文档，不实现这些文件。

---

## 6. 流水线总览

整体流程如下：

1. 读取 `.ass` 字幕
2. 清理并对齐双语台词
3. 提取屏幕字与注释
4. 切分 scene
5. 第一阶段 LLM 标注 speaker / mentions / present characters
6. 导出第一阶段结果，进入人工复核与修正
7. 第二阶段基于“复核后的第一阶段结果”抽取三张表候选条目
8. Python 规范化、映射、时间字段生成、校验
9. 导出最终结构化 JSON
10. 可选：再导入 `models.py` 对应文档类进行强校验

---

## 7. 中间数据结构设计

以下数据结构建议使用 `pydantic` 建模。

## 7.1 原始台词单元 `RawSubtitleLine`

表示从 `.ass` 中读取的一条字幕记录。

建议字段：

- `source_path: str`
- `line_no: int`
- `layer: int`
- `start_ms: int`
- `end_ms: int`
- `style: str`
- `raw_text: str`
- `clean_text: str`

用途：

- 保留原始来源
- 便于后续 debug 和证据追踪

## 7.2 双语台词单元 `UtteranceUnit`

表示对齐后的中日双语台词。

建议字段：

- `u_id: str`
- `episode: int`
- `start_ms: int`
- `end_ms: int`
- `jp_text: str`
- `zh_text: str`
- `source_line_nos: list[int]`
- `notes: list[str]`

说明：

- `u_id` 建议格式为 `ep01_u0001`
- 若某句只有中文或只有日文，也允许另一侧为空字符串
- `notes` 可记录诸如“来自 Dial_JP2 / Dial_CH2”之类特殊情况

## 7.3 屏幕字单元 `ScreenTextUnit`

表示 `Screen` 或 `Cmt_CH` 提取出来的文本。

建议字段：

- `s_id: str`
- `episode: int`
- `start_ms: int`
- `end_ms: int`
- `kind: str`
  - 可取 `screen_text` / `commentary_note`
- `text: str`
- `source_line_nos: list[int]`

说明：

- `Cmt_CH` 常用于注解
- `Screen` 常用于地点、社团招募牌、标题内容、便签板等

## 7.4 场景对象 `SceneChunk`

表示一个可送入 LLM 的最小上下文块。

建议字段：

- `scene_id: str`
- `episode: int`
- `start_ms: int`
- `end_ms: int`
- `utterances: list[UtteranceUnit]`
- `screen_texts: list[ScreenTextUnit]`
- `candidate_characters: list[str]`
- `scene_summary_hint: str | None`

说明：

- `candidate_characters` 由程序根据剧集范围、已知人物表、屏幕字和显式称呼初步猜测
- 这里不要求绝对正确，只是给第一阶段 LLM 一个候选空间

### 7.4.1 `candidate_characters` 生成方案

`candidate_characters` 不建议只靠一个简单规则生成，建议采用“召回优先”的多信号合并策略。

可组合如下信号：

1. 剧集先验角色表
- 对于每一集，维护一个人工整理的“高概率出场角色名单”
- 第 1 集这种角色范围相对集中，先验名单很有效

2. 字幕显式称呼匹配
- 从 `utterances.jp_text` 和 `utterances.zh_text` 中做角色别名匹配
- 优先识别：
  - 全名
  - 简称
  - 昵称
  - 常见称呼，如“小祥”“小灯”“立希”

3. 屏幕字命中
- 若 `screen_texts` 中出现学校、乐队、场所、人物名相关信息，可追加对应候选角色

4. 上下场景继承
- 若当前 scene 与上一 scene 时间连续、地点相近、对话延续明显，则可以继承上一 scene 的 `candidate_characters`

5. 低成本初筛规则
- 若某些角色仅在 very weak alias 命中一次，且没有其他证据，可不给候选，避免候选集过大

建议具体实现为打分制：

- 剧集先验命中：`+2`
- 明确别名命中：`+3`
- 屏幕字相关命中：`+2`
- 上一 scene 继承：`+1`

最终保留分数达到阈值的角色，再按分数排序截断到一个上限，比如前 8 到 12 人。

这个字段的目标不是“绝对正确”，而是：

- 尽量不要漏掉 scene 中的重要角色
- 同时避免把全角色表一股脑丢给第一阶段 LLM

## 7.5 第一阶段输出 `SpeakerAnnotation`

表示单句或单个台词单元的 speaker 标注结果。

建议字段：

- `u_id: str`
- `speaker_name: str | null`
- `speaker_confidence: float`
- `is_inner_monologue: bool`
- `addressee_candidates: list[str]`
- `mentioned_characters: list[str]`
- `emotion_hint: str | null`
- `reason_brief: str`

说明：

- `speaker_name` 使用角色中文名或规范展示名，不直接填 `CharacterId`
- `speaker_confidence` 用于后续筛选和人工复核

## 7.6 场景级第一阶段结果 `SceneAnnotationPass1`

建议字段：

- `scene_id: str`
- `episode: int`
- `present_characters: list[str]`
- `utterance_annotations: list[SpeakerAnnotation]`
- `global_notes: list[str]`

用途：

- 供第二阶段抽取使用

## 7.7 第二阶段条目候选

建议对三张表分别定义候选结构，保留证据与置信度。

### `StoryEventCandidate`

- `scene_id: str`
- `event_local_id: str`
- `title: str`
- `summary: str`
- `participants: list[str]`
- `importance: int`
- `tags: list[str]`
- `evidence_u_ids: list[str]`
- `evidence_s_ids: list[str]`
- `confidence: float`

### `CharacterRelationCandidate`

- `scene_id: str`
- `relation_local_id: str`
- `subject_character_name: str`
- `object_character_name: str`
- `relation_label: str`
- `state_summary: str`
- `speech_hint: str`
- `object_character_nickname: str`
- `tags: list[str]`
- `evidence_u_ids: list[str]`
- `confidence: float`

### `LoreEntryCandidate`

- `scene_id: str`
- `lore_local_id: str`
- `title: str`
- `content: str`
- `tags: list[str]`
- `evidence_u_ids: list[str]`
- `evidence_s_ids: list[str]`
- `confidence: float`
- `is_spoiler_sensitive: bool`

## 7.8 第二阶段场景输出 `SceneAnnotationPass2`

- `scene_id: str`
- `story_events: list[StoryEventCandidate]`
- `character_relations: list[CharacterRelationCandidate]`
- `lore_entries: list[LoreEntryCandidate]`
- `global_notes: list[str]`

## 7.9 最终规范化结果

在导出前，将候选结构映射到接近 `models.py` 的最终结构。

建议分别定义：

- `NormalizedStoryEvent`
- `NormalizedCharacterRelation`
- `NormalizedLoreEntry`

这些结构将直接对应 `StoryEventDocument`、`CharacterRelationDocument`、`LoreEntryDocument` 所需字段。

---

## 8. 字幕预处理方案

## 8.1 字幕读取

使用 `pysubs2` 读取 `.ass`。

需保留：

- `Dial_JP`
- `Dial_JP2`
- `Dial_CH`
- `Dial_CH2`
- `Screen`
- `Cmt_CH`

需丢弃：

- `Title`
- `Staff`
- `ED_JP`
- `ED_CH`

## 8.2 文本清洗

对文本做如下处理：

- 去除 ASS override tag，如 `{\...}`
- 将 `\N` 转为换行或空格
- 去掉多余空白
- 对屏幕字进行多层去重
  - 同一时间范围内多个 `Screen` 阴影层文本相同，只保留一份

## 8.3 双语对齐

根据以下特征对齐 `Dial_JP` 和 `Dial_CH`：

- 起止时间接近
- 行序接近
- 样式成对

规则建议：

- 时间差在某个阈值内视为候选配对
- 优先一对一匹配
- 未匹配行也保留，但在 `UtteranceUnit` 中标记为空侧

## 8.4 Scene 切分

建议采用规则切分，而非一开始交给 LLM 切分。

触发切分的信号：

- 较长时间空隙
- 标题卡
- 明显地点屏幕字
- 大段画外场景变换

scene 切得略小比切得过大更安全，因为：

- 第一阶段 speaker 判断更稳定
- 第二阶段事件与关系提炼也更聚焦

---

## 9. 第一阶段 LLM：Speaker Annotation

## 9.1 目标

对每个 `SceneChunk` 标注：

- 每句是谁说的
- 提到了谁
- 可能在对谁说
- 场景中出现了哪些角色
- 是否为内心独白

## 9.2 输入

输入给 LLM 的内容建议包括：

- scene 基本信息
- `utterances`
  - 每条含 `u_id`、中文、日文、时间
- `screen_texts`
- `candidate_characters`
- 明确的角色名单与名字映射提示

## 9.3 输出格式

要求 LLM 返回严格 JSON。

建议强制：

- 不允许输出额外解释性自然语言
- 角色名必须从给定候选列表中选
- 如果无法判断 speaker，则返回 `null`

## 9.4 Prompt 重点

Prompt 中要强调：

- 优先依据称呼、自称、接续语义、场景上下文判断
- 不要凭外部记忆补全未出现人物
- 如果一句台词无法高置信识别 speaker，要允许返回未知
- `mentioned_characters` 与 `speaker_name` 必须分开判断

## 9.5 第一阶段后处理

Python 对第一阶段结果做：

- 角色名规范化
- 低置信 speaker 标记
- 连续台词 speaker 平滑
  - 例如相邻多句若 speaker 相同且中间无转折，可自动提高置信度
- 生成 `present_characters`

---

## 10. 第二阶段 LLM：三张表抽取

第二阶段的输入必须包含：

- `SceneChunk`
- 第一阶段标注结果

不建议只给原始台词。

## 10.1 `story_events` 抽取

### 抽取原则

- 一个 scene 可对应 1 到多个事件
- 事件应是“可检索的剧情单位”，而不是每句台词都生成一个事件
- 事件要包含行为、冲突、结果或状态变化

### 应交给 LLM 生成的字段

- `title`
- `summary`
- `participants`
- `importance`
- `tags`
- `retrieval_text`

### 应由程序生成的字段

- `season_id`
- `series_id`
- `episode`
- `time_order`
- `visible_from`
- `visible_to`
- `canon_branch`

### 程序生成策略

- `time_order`
  - 不使用简单的 episode 内从 1 递增
  - 应遵循项目既定编号规则
  - `It's MyGO!!!!!` 从 `4000` 起编号
  - 每集预留 `50` 个事件编号
  - 因此第 `n` 集的编号区间为：
    - `4000 + (n - 1) * 50`
    - 到
    - `4049 + (n - 1) * 50`
  - 例如：
    - 第 1 集事件编号范围：`4000-4049`
    - 第 2 集事件编号范围：`4050-4099`
  - scene 内事件再按出现顺序依次占用该区间中的编号
- `visible_from`
  - 直接设置为 `time_order`
- `visible_to`
  - 初版统一设置为 `999999`
  - 后续若人工发现需要更精细控制，再手动修改或追加规则

## 10.2 `character_relations` 抽取

### 抽取原则

- 必须是有方向的：
  - `subject_character_id -> object_character_id`
- 只抽“主体角色当前对客体的关系状态”
- 不要把纯客观共同经历直接当成关系条目

### 应交给 LLM 生成的字段

- `subject_character_name`
- `object_character_name`
- `relation_label`
- `state_summary`
- `speech_hint`
- `object_character_nickname`
- `tags`
- `retrieval_text`

### 应由程序生成的字段

- `season_id`
- `series_id`
- `visible_from`
- `visible_to`
- `canon_branch`

### 程序生成策略

- `visible_from`
  - 该关系状态首次在 scene 中被确认的时点
- `visible_to`
  - 初版可设为时间线末端占位值
  - 当后续出现同一 `subject -> object` 的更新关系时，再回填上一条的 `visible_to`
  - 自动设置 `visible_from/visible_to` 是这一张表里很重要的程序职责

### 关键约束

- 若主体与客体相同，程序直接丢弃
- 若 `speech_hint` 或 `object_character_nickname` 证据不足，可允许空字符串

## 10.3 `lore_entries` 抽取

### 抽取原则

- 只抽字幕里明确出现且可解释的作品内名词
- 优先抽：
  - 乐队名
  - 学校名
  - 地点名
  - 社团名
  - 组织名
  - 歌曲名
- 谨慎抽：
  - 纯一次性描述
  - 不具备稳定检索价值的偶然短语

### 应交给 LLM 生成的字段

- `title`
- `content`
- `tags`
- 可选中间字段：`is_spoiler_sensitive`
- `retrieval_text`

### 应由程序生成的字段

- `scope_type`
- `series_ids`
- `season_ids`
- `visible_from`
- `visible_to`
- `canon_branch`

### 程序生成策略

- `scope_type`
  - 当前字幕数据默认生成 `ScopeType.SERIES`
- `series_ids`
  - 固定为 `[SeriesId.ITS_MYGO]`
- `season_ids`
  - 固定为当前系列对应 season
- `visible_from/visible_to`
  - 初版统一设置为 `None`
  - 后续若人工需要做剧透控制，再单独补充时间窗口

---

## 11. 最终字段映射汇总

## 11.1 `story_events`

LLM 负责：

- `title`
- `summary`
- `participants`
- `importance`
- `tags`
- `retrieval_text`

程序负责：

- `season_id`
- `series_id`
- `episode`
- `time_order`
- `visible_from`
- `visible_to`
- `canon_branch`

## 11.2 `character_relations`

LLM 负责：

- `subject_character_id` 对应角色名
- `object_character_id` 对应角色名
- `relation_label`
- `state_summary`
- `speech_hint`
- `object_character_nickname`
- `tags`
- `retrieval_text`

程序负责：

- `season_id`
- `series_id`
- `visible_from`
- `visible_to`
- `canon_branch`

## 11.3 `lore_entries`

LLM 负责：

- `title`
- `content`
- `tags`
- `retrieval_text`

程序负责：

- `scope_type`
- `series_ids`
- `season_ids`
- `visible_from`
- `visible_to`
- `canon_branch`

---

## 12. 规范化与 ID 映射

## 12.1 角色名映射

建立一个角色别名字典，将以下形式统一映射到 `CharacterId`：

- 全名
- 常见简称
- 昵称
- 中日文写法

例如：

- `高松灯` / `灯` / `Tomori`
- `千早爱音` / `爱音`
- `长崎素世` / `素世` / `小素世`
- `丰川祥子` / `祥子` / `小祥`

程序应只在最终导出阶段将角色名转换为 `CharacterId`。

## 12.2 名词条目标题规范化

`lore_entries.title` 应做标题统一，如：

- `CRYCHIC`
- `sumimi`
- `月之森女子学园`
- `CiRCLE`

避免同一条目因大小写或翻译差异产生重复。

---

## 13. 去重、合并与时间窗口回填

## 13.1 `story_events`

第一版建议不做自动去重。

原因：

- 事件条目数量不会特别夸张
- 自动去重规则容易误合并相近但不同的剧情节点
- 人工筛选更稳，也更适合前期摸清数据分布

因此第一版只保留 evidence 和来源信息，去重交给人工完成。

## 13.2 `character_relations`

以 `(subject, object)` 为主键维度做时间序列管理。

同一时间段若出现多条关系候选：

- 优先保留证据更多、摘要更具体的一条
- 或合并成更完整的 `state_summary`

当后续 scene 抽到新的关系状态时：

- 将旧关系的 `visible_to` 截断到新关系开始前
- 新关系的 `visible_from` 设为新 scene 首次证据时点

这一部分建议保留自动处理，因为它直接影响 `character_relations` 的检索有效性。

## 13.3 `lore_entries`

第一版同样建议不做自动去重。

原因与 `story_events` 类似：

- 设定条目总量有限
- 很多条目标题相近，但解释角度未必相同
- 前期人工筛选更稳妥

---

## 14. 质量控制与人工复核

建议设置以下复核触发条件：

- 第一阶段 speaker 置信度低
- 第二阶段关系主体或客体无法映射
- `participants` 为空或只有一人但又被抽为重大事件
- `lore_entries` 内容过于像剧情摘要而非设定解释
- 同一 scene 生成过多条目

这里建议把第一阶段和第二阶段明确拆成两个独立执行流程，而不是在脚本里默认串行连续执行。

推荐工作流：

1. 执行第一阶段，输出 `SceneAnnotationPass1`
2. 导出人工复核文件
3. 由人工修正 speaker、mentioned_characters、present_characters 等字段
4. 将修正后的第一阶段结果作为第二阶段输入
5. 再执行第二阶段抽取 `story_events`、`character_relations`、`lore_entries`

建议为第一阶段单独导出以下文件：

- `pass1_raw.json`
- `pass1_reviewed.json`

第二阶段只读取 `pass1_reviewed.json`，不直接读取第一阶段原始结果。

建议导出一份 `review_queue.json`，集中保存低置信度候选。

---

## 15. 最终导出格式

建议最终导出为三份 JSON 文件：

- `story_events.json`
- `character_relations.json`
- `lore_entries.json`

每份文件内容都应已经满足 `models.py` 的字段要求。

也可额外导出一份带证据的内部版本：

- `story_events.with_evidence.json`
- `character_relations.with_evidence.json`
- `lore_entries.with_evidence.json`

内部版本可包含：

- `scene_id`
- `evidence_u_ids`
- `evidence_s_ids`
- `confidence`
- `llm_raw_output`

最终入库版本则只保留 schema 所需字段。

---

## 16. 与 `models.py` 的对齐策略

最终导出前，建议用 `GPT_SoVITS/rag/models.py` 中的 dataclass 做一次强校验：

- `StoryEventDocument`
- `CharacterRelationDocument`
- `LoreEntryDocument`

校验目标：

- 枚举字段是否合法
- 文本字段非空
- 列表字段非空
- 时间窗口合法
- `character_relations` 的主客体不相同
- `lore_entries.scope_type == SERIES` 时 `series_ids` 非空

若实例化失败，应记录到错误报告中，而不是静默跳过。

---

## 17. LLM 调用建议

## 17.1 使用 `litellm`

建议封装统一方法：

- 输入 messages
- 指定 model
- 指定 temperature
- 指定 response schema
- 自动重试
- 记录请求与响应

## 17.2 第一阶段建议参数

- `temperature` 较低
- 目标是稳定判断 speaker，不是开放创作

## 17.3 第二阶段建议参数

- `temperature` 仍建议偏低
- 目标是结构化总结，不追求文风多样性

## 17.4 日文与中文都保留

Prompt 中同时给：

- 中文字幕
- 日文字幕

原因：

- 中文更利于总结语义
- 日文更利于识别称呼、语气与对象关系

---

## 18. 实施顺序建议

建议按以下顺序推进：

1. 完成字幕读取、清洗、双语对齐
2. 完成 scene 切分
3. 定义全部中间数据结构和 JSON schema
4. 完成第一阶段 speaker 标注链路
5. 导出第一阶段结果并进行人工复核
6. 基于复核后的第一阶段结果完成第二阶段三表抽取
7. 完成规范化、时间字段生成与导出
8. 最后接 `models.py` 强校验

不要一开始就做全量抽取。

建议先用第 1 集做样本验证，调通后再推广到全部集数。

---

## 19. 当前版本的现实判断

从现有字幕特征看：

- `story_events` 的抽取可行性最高
- `character_relations` 可行，但强依赖第一阶段 speaker 标注质量
- `lore_entries` 可行，但覆盖面一定比前两者小，且要严格控制“只抽可检索的设定名词”

因此，后续实现时建议优先保证：

1. 第一阶段 speaker 标注正确率
2. `character_relations` 的主客体方向稳定
3. `story_events` 粒度适中，不要过碎

---

## 20. 本文档之外，后续还需要补充的内容

后续实现前，建议再单独补三份文档或模板：

- 第一阶段 prompt 设计文档
- 第二阶段 prompt 设计文档
- `candidate_characters` 召回规则文档
- `character_relations` 时间窗口回填规则文档

这三部分一旦稳定，整条 annotation pipeline 就能开始编码实现。
