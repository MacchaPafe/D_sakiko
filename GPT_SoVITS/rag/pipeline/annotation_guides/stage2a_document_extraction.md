# Stage 2A：剧情、关系观察与名词提取指南（Codex）

## 你的任务

根据单个场景中已经带说话人的字幕，提取三类候选：

- `story_events`：本场景中相对完整、可检索的剧情单位；
- `relation_observations`：一个角色对另一个角色在本场景表现出的有向局部关系证据；
- `lore_entries`：字幕明确支持且具有检索价值的作品内名词或设定。

只处理当前场景。除 Prompt 本身提供的材料外，不使用其他集数或自己记忆中的剧情补全。

## 输入与输出文件

为当前集统一使用 `epXX`：

- 输入：`annotations_stage2/epXX_stage2_input.json`
- Prompt Package：建议使用 `prompt_packages/epXX_stage2a/`
- 正式输出：`annotations_stage2/epXX_pass2_raw.json`

输入必须是同一集已确认说话人后的 Stage 2 Input。不要直接拿 Stage 1 原始 response 或 `.ass` 字幕代替。
新一轮渲染必须使用新的 Package 目录。

如果 Stage 2 Input 尚不存在，使用同集的两个 Stage 1 产物生成：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage2-input \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_prepared.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_pass1_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_stage2_input.json
```

## 第一步：确认输入

抽查 `epXX_stage2_input.json`：

- `metadata.episode` 与文件编号一致；
- `metadata.timeline_id` 与当前作品预期的剧情时间线一致；`metadata.story_year` 可以为 `null`，不能把它当作季度或排序字段；
- `scenes` 非空；
- 台词包含 `speaker_name` 和真实 `u_id`；
- 说话人明显错误时先暂停，不要试图在本阶段偷偷纠正并继续抽取。

## 第二步：渲染静态 Prompt Package

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_stage2_input.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage2a
```

试标可使用 `--max-scenes`，返工指定场景可使用 `--scene-id`。全量正式输出需要覆盖整集所有场景。

## 第三步：填写 responses

请你逐项读取 `manifest.json` 中每个任务的 `prompt_file`，把严格 JSON 写到对应 `response_file`。不得修改 Prompt
或 manifest，不得输出 Markdown 围栏。自己完成标注时，不要再运行模型 API 请求命令。

## 第四步：校验并组装正式产物

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage2a/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_pass2_raw.json \
  --model-label codex-workspace
```

若存在失败场景，查看输出的 `results[].error`，只修复对应 response，再重新组装。不要用 `--allow-stale`
绕过发生变化的 Stage 2 Input 或 Prompt。

## Story Event 标注原则

- 按完整行为、冲突、决定、信息揭示、情绪转折或结果组织事件，不要逐句拆事件。
- 同一个目标和冲突连续发展时通常合并为一条；只有场景中出现清晰的新行动或转折才拆第二条。
- `summary` 必须是字幕支持的事实，不要补充角色没说出的动机、后续结果或场外原因。
- `participants` 只包含实际参与该事件的人；仅被提到、只是在场但未参与的人不应机械加入。
- `retrieval_text` 要能脱离标题独立理解，写成简洁自然的检索摘要，而不是字段拼接。
- `importance` 数字越小越重要，但不要把每个普通场景都标成最高重要度。

## Relation Observation 标注原则

这部分之后会按有向角色对跨场景聚合为长期关系状态，因此当前阶段应保留真实的局部证据，但不能过度拆分。

- 关系必须有方向：`subject_character_name -> object_character_name`。反向关系需要独立证据，不能自动镜像。
- 同一主体对同一客体在一个场景中相互一致的态度、称呼和说话方式，合并为一条 observation。
- 不要为每句责备、关心、附和分别创建一条；通常一个场景、一个有向角色对只需一条综合观察。
- 只有同场景内确实出现彼此冲突、且不能由一条文字准确表达的关系表现时，才考虑拆成少量多条。
- 不要把共同经历、同处一室、普通问答或“提到了某人”自动当成角色关系。
- observation 描述本场景表现，不要直接宣称这是永久性格或长期关系。
- 短暂愤怒可以记录为局部观察，但应在 `ambiguity_notes` 指出它可能只针对当前事件。
- `speech_hint` 与 `object_character_nickname` 只记录当前场景有证据的内容；没有就使用空字符串。
- 称呼优先记录中文字幕实际文本；中文没有对应文本时才保留日文原写法，不自行转写读音。

## Lore Entry 标注原则

- 优先提取乐队、学校、地点、组织、社团、歌曲等稳定名词。
- 当前场景必须提供足以说明它是什么的内容；只出现一个名字但完全无法解释时不要勉强生成。
- 不要把普通剧情句、一次性短语、情绪或角色关系包装成 Lore。
- 同一场景重复出现的同一名词只生成一条。
- `content` 和 `retrieval_text` 不得使用当前场景没有提供的作品设定知识。

## 证据与 JSON 约束

- 所有 `evidence_u_ids` 必须来自当前 Prompt 的台词；所有 `evidence_s_ids` 必须来自当前 Prompt 的屏幕字。
- 证据应支持完整命题，而不只是与条目主题略有关联。
- 三类数组都允许为空；不要为了让输出显得丰富而制造低价值条目。
- 每类 local ID 在当前场景内必须唯一，并使用 Prompt 示例要求的稳定格式。
- 角色名必须使用输入中的标准角色名，不能输出角色 ID 或自创别名。
- 低置信或存在多种解释时，应降低 `confidence` 并如实填写歧义，而不是补全答案。

## 完成前检查

- 没有把一个连续事件拆成逐句事件；
- 没有把一个场景内同一有向角色对的相近表现拆成大量 observation；
- relation 的方向、主体、客体均有字幕证据；
- Lore 都是可稳定检索的作品内名词；
- 每个证据 ID 真实存在且支持条目；
- response 是纯 JSON，`scene_id` 原样回填；
- 组装命令成功，输出中没有 `results[].error`。
