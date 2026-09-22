# Stage 3 Relation：长期角色关系聚合指南（Codex）

## 你的任务

把同一系列、剧情时间线和剧情分支中，一个有向角色对的全部场景级 Relation Observation 聚合为少量、具有明确
有效时间的长期 Character Relation State。

这里不是再次描述每个场景，也不是替每条 observation 生成一条 state。任务的核心就是跨场景合并、判断
哪些变化真正值得成为新的长期阶段。

## 输入与输出文件

本阶段应尽量使用当前标注范围内的全量集数，而不是只处理一集：

- 每集 Stage 2 Input：`annotations_stage2/epXX_stage2_input.json`
- 每集 Stage 2A 标注：`annotations_stage2/epXX_pass2_raw.json`
- Prompt Package：例如 `GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_relations/`
- 正式输出：例如 `GPT_SoVITS/rag/annotated_data/its_mygo/mygo_relation_review.json`

`reviews/` 子目录只保存逐集 Story/Lore Review。Relation 是跨集唯一产物，应直接放在对应世界书的
`annotated_data/<worldbook>/` 根目录下。

每一个 `--input` 必须与同位置的 `--annotation` 属于同一集。按集数顺序重复参数，例如 ep01、ep02、ep03。
不要只因为当前想检查某一集，就覆盖已经存在的全量关系产物；试验应使用单独 Package 和输出文件。

## 第一步：确认输入范围

在渲染前确认：

- `--input` 与 `--annotation` 数量相同，顺序一一对应；
- 所有文件属于同一 `series_id`、`timeline_id` 和 `canon_branch`；
- `story_year` 只是可空的辅助学年标签，不是聚合分组键；不得因为学年相同就跨 `timeline_id` 聚合，也不得因为学年为空就拒绝同时间线输入；
- 输入覆盖你希望关系 State 生效的完整时间范围；
- Stage 2A 中的 relation observations 已经完成基本复核，没有明显的方向错误或逐句过度拆分。

如果后续加入新集数，应基于扩展后的全量输入重新构建 Package，而不是只聚合新增一集再与旧 State 手工拼接。

## 第二步：渲染静态 Prompt Package

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-relation-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_pass2_raw.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_relations
```

每个任务对应一个有向角色对，例如“爱音 → 立希”。反方向会是另一个任务，不能互相代替。

## 第三步：填写 responses

请你逐个读取 manifest 声明的 Prompt，把纯 JSON 写入相应 response。一个 Prompt 已经包含该有向角色对在输入范围内
的全部 observation；必须整体阅读后再决定 State，不能边看边按 observation 逐条输出。

不要修改 observation ID、manifest 或 Prompt。每条输入 observation 必须被某个 State 引用，或进入
`unmerged_observations` 并给出原因。

## 第四步：校验并组装正式产物

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-relations \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_relations/manifest.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/mygo_relation_review.json \
  --model-label codex-workspace
```

若报告 response 问题，查看输出 `issues` 中对应角色对，修复 response 后重新组装。全量正式聚合不应使用
`--allow-partial`；否则缺失角色对会直接造成关系知识空洞。

## 真正聚合，而不是逐场景改写

- 默认合并。多条 observation 表达相近的信任、依赖、亲近、戒备或互动方式时，应共同支持一条 State。
- 不要保留“每个场景一种关系 State”的形态。场景时间连续、关系含义没有实质变化时，必须合并。
- 一次争吵、一句责备、短暂尴尬或针对单个事件的情绪，通常不足以开启新的长期 State。
- 只有新的相处模式已经明确出现，并且对之后角色扮演持续有用时，才开启新的阶段。
- State 的 `summary` 描述该阶段当前有效的关系，不写完整历史流水账，也不逐条复述 observation。
- 新阶段从首次明确体现变化的 observation 开始；不能根据后续结果倒推角色更早已经改变。

## 控制 relation_type_key 数量

`relation_type_key` 是同一角色对内部稳定的关系语义线索，不是情绪标签枚举。

- 默认优先使用一个综合关系线程，例如 `general_bond`。
- 态度、说话方式和称呼通常会一起变化，可以合并在同一个 State 中，不要机械拆成多个 key。
- 只有某种关系语义能够独立变化，且值得长期单独检索时，才创建第二个 key。
- 不要为“生气”“缓和”“担心”“称呼改变”等每个局部表现新建 key。
- key 应使用简短、稳定的英文 snake_case；跨阶段表达同一关系线索时继续使用同一个 key。
- 同一 observation 一般只支持一个 State；确实同时证明两个独立长期关系线索时才复用，通常不超过两个。

## speech_hint 与称呼

- 只把跨一段时间持续有效的说话方式和称呼写入 State。
- 单场景偶然使用一次的语气或称呼，不应自动升级成长期字段。
- 称呼发生稳定变化时，可以随新的关系阶段更新，但不要仅凭一次出现就断言已经永久改变。
- 没有足够证据时使用空字符串，不根据作品外知识补日文读音。

## unmerged_observations 的使用

以下 observation 可以不提升为长期 State：

- 只针对当前事件的一次性愤怒、惊讶或礼貌反应；
- 证据过弱、主体客体方向可疑；
- 与其他证据冲突且无法形成可靠状态；
- 只说明普通对话，没有持续关系意义。

不能为了减少 State 数量而丢弃证据。每条未采用 observation 都要列入 `unmerged_observations`，理由应说明
为什么它不构成长效关系，而不是只写“未采用”。

## 完成前检查

- 每个任务确实综合阅读了全部 observation；
- State 数量显著少于 observation 数量，不是逐场景一一对应；
- 没有因一次短暂情绪轻易开启新阶段；
- relation_type_key 少量、稳定、可跨阶段复用；
- summary 描述当前关系状态，而非完整变化历史；
- 每条 observation 要么支持 State，要么有明确 unmerged 理由；
- response 是纯 JSON，主体和客体方向与 Prompt 完全一致；
- 全量组装成功，输出中没有 response 相关 issue。
