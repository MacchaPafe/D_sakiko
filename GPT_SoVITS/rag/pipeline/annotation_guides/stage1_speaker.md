# Stage 1：字幕说话人标注指南（Codex）

## 你的任务

对一集字幕中的每句台词标注说话人、说话对象、被提及角色、是否为内心独白和简短情绪提示。
这是逐场景任务，不要总结剧情，也不要生成后续知识库条目。

## 输入与输出文件

为正在处理的集数选择一致的编号，例如第 5 集统一使用 `ep05`。

- 原始输入：该集的 `.ass` 字幕，优先使用日文—简体中文双语版本。
- 预处理输入：`annotations_stage1/epXX_prepared.json`
- Prompt Package：建议使用 `prompt_packages/epXX_stage1/`
- 正式输出：`annotations_stage1/epXX_pass1_raw.json`

不要把另一集的 `prepared.json`、Package 或输出路径混进当前任务。Prompt Package 必须使用一个尚未包含
`manifest.json` 的新目录；不要覆盖旧 Package，以免旧回复与新 Prompt 混用。

开始前还要确定两个时间字段：

- `timeline_id`：剧情时间轴标识。只有同一时间轴内的剧情顺序和有效区间才能直接比较。MyGO 与
  Ave Mujica 当前使用 `bang_dream_original`；世界连续性未确认的新作品应使用独立、稳定的时间线 ID。
- `story_year`：可空的作品内剧情学年，只在能够确认时填写。它不是动画季度，也不决定剧情顺序；
  不要为了沿用旧的三年级结构而猜测一个学年。

## 第一步：从字幕生成预处理输入

如果 `epXX_prepared.json` 尚不存在，运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle '该集字幕.ass' \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_prepared.json \
  --timeline-id bang_dream_original \
  --story-year 3
```

上例适用于剧情学年能够确认为三年级、且属于 BanG Dream 原作时间线的内容。标注其他作品时必须替换
`--timeline-id`；`story_year` 无法确认时应在产物中保持 `null`，不能用 `3` 充当默认答案。

学年未知时显式传入 `--story-year 0`。CLI 会把 `0` 或负数转换成 `None`，因此 prepared JSON 中保存
`null`，渲染后的 Prompt 中显示 `None`；正式产物不会保存 `0` 或负数。例如：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle '学年未知作品的字幕.ass' \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_prepared.json \
  --timeline-id independent_story_timeline \
  --story-year 0
```

生成后确认：

- `metadata.episode` 是当前集数；
- `metadata.subtitle_path` 指向预期字幕；
- `metadata.timeline_id` 是当前作品实际所属的剧情时间线；
- `metadata.story_year` 只在能够确认时为正整数，否则为 `null`；
- `scenes` 非空，且台词时间范围合理。

## 第二步：渲染静态 Prompt Package

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage1-prompts \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_prepared.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage1
```

试标时可以增加 `--max-scenes 3`；返工单个场景时可重复传入 `--scene-id scene_id`。正式全量标注时不要
保留这些筛选参数。

## 第三步：填写 responses

1. 打开 Package 中的 `manifest.json`。
2. 对 `tasks` 中的每个任务，读取它声明的 `prompt_file`。
3. 严格按照 Prompt 的 JSON 格式完成标注。
4. 把纯 JSON 写入同一任务声明的 `response_file`。
5. 不要修改 `manifest.json` 或 `prompts/`，不要在 JSON 外添加 Markdown 围栏、说明或评价。

在你直接标注时，不要运行 `complete-prompt-package`，因为你作为大语言模型已经标注了内容，不用再请求外部 LLM 标注。只有明确要求改用 LiteLLM API 时，才使用该命令
填充相同的 `responses/` 目录。

## 第四步：校验并组装正式产物

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage1-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage1/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/epXX_pass1_raw.json \
  --model-label codex-workspace
```

如果组装报告失败场景：打开输出 JSON 中对应 `results[].error`，修复那个任务的 response 后重新运行组装。
不要使用 `--allow-stale` 掩盖输入或 Prompt 已变化的问题；输入变化时应创建新 Package。只有为了查看尚未完成
任务的部分产物时才使用 `--allow-partial`。

## 标注时特别注意

- 每个 Prompt 中的所有 `u_id` 都必须原样、完整返回，不能漏句或创建新 ID。
- 角色名只能使用 Prompt 候选列表里的标准 `display_name`；不能输出别名或角色 ID。
- `speaker_name` 是谁在说，`addressee_candidates` 是在对谁说，`mentioned_characters` 是句中提到了谁；
  三者不能混为一谈。
- 优先联合使用中日文。中文有助于理解内容，日文称呼、句尾和语气常更利于确认说话人与对象。
- 多人连续对话中要检查问答、打断和说话人切换，不要因为连续字幕样式相同就默认同一人。
- 场外声音、回忆、旁白和内心独白要保守判断。`is_inner_monologue=true` 不表示角色正在对在场者说话。
- 不得仅凭自己熟悉剧情就补全缺少证据的说话人。确实不能判断时使用 `null` 并降低置信度。
- `present_characters` 只记录当前场景有可靠依据的角色，不要把所有候选角色照抄进去。
- `reason_brief` 应说明本场景证据，例如称呼、上下句回答关系或语气，而不是写“根据剧情可知”。

## 完成前检查

- manifest 中每个 task 都有非空 response；
- 每个 response 的 `scene_id`、`episode` 与 Prompt 一致；
- 每个场景输入的全部 `u_id` 恰好出现一次；
- 所有角色名来自候选列表；
- JSON 可以直接解析，且没有 Markdown 围栏；
- 组装命令返回成功，输出中没有 `results[].error`。
