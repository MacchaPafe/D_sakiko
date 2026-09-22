# Stage 3 Thought Link：角色观点语义链接指南（Codex）

## 你的任务

只处理确定性规则无法解决的 `unresolved` Character Thought Update：在 Prompt 给出的候选 Story Event 和
Event Fact 中选择语义对象，或判断该观点是独立主题，或继续保留 unresolved。

不要重新抽取观点，不要改写 `thought_text`，不要创建新的事件、事实或候选 ID。

## 输入与输出文件

当前集统一使用 `epXX`：

- 场景输入：`annotations_stage2/epXX_stage2_input.json`
- 观点抽取输入：`annotations_stage2/epXX_pass2b_raw.json`
- 已规范化剧情输入：`annotations_stage3/epXX_rag_ready.json`
- Prompt Package：建议使用 `prompt_packages/epXX_stage3_thought_links/`
- 正式输出：`annotations_stage3/epXX_thoughts_review.json`

三个输入必须来自同一集、同一系列范围和兼容的数据版本。`rag_ready.json` 必须包含 Stage 2A 生成并规范化后的
Story Event；不要拿已发布文件或另一轮去重后的旧文件随意替换。新一轮渲染使用新的 Package 目录。

## 第一步：确认输入

抽查：

- 三个文件的集数和剧情范围一致；
- 三个文件属于同一个 `timeline_id`；观点与候选事件的时间先后只能在这条时间线内比较；
- Stage 2B 中已有 `character_thought_updates`；
- `rag_ready.json` 中的 Story Event 时间不晚于被链接观点时，才可能成为合法候选；
- Stage 2B 的明显提取错误应返回 Stage 2B 修复，本阶段不能靠链接操作掩盖错误观点。

## 第二步：渲染静态 Prompt Package

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-thought-link-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_stage2_input.json \
  --stage2b-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_pass2b_raw.json \
  --stage3-rag GPT_SoVITS/rag/pipeline/data/annotations_stage3/epXX_rag_ready.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage3_thought_links
```

Package 只包含规则仍无法链接的观点。任务数为 0 是合法结果，表示所有观点均已被确定性规则处理；此时无需
编造 response，可直接运行组装命令。

## 第三步：填写 responses

请你逐个读取 Prompt，并只在它列出的候选集合中决策。把纯 JSON 写入 manifest 指定的 response 路径。

每个任务只输出一条链接决策。不得：

- 创建候选列表之外的 `target_id`；
- 从作品记忆中寻找 Prompt 没给出的事件；
- 修改 source local ID；
- 顺手重写观点、认知状态或证据；
- 为了减少 unresolved 数量而强行选择语义相似但并非同一对象的候选。

## 第四步：校验并组装正式产物

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-thoughts \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage3_thought_links/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage3/epXX_thoughts_review.json \
  --model-label codex-workspace
```

组装器会重新执行确定性链接、应用受限决策并聚合观点时间线。如果报告 response 问题，查看输出 `issues`，修复
对应 response 后重跑。允许一条决策保持 `unresolved`；这不等于组装失败。

## linked、standalone 与 unresolved

### linked

只有观点的语义对象与候选事件或事实明确是同一件事时使用：

- 谈论一个完整剧情事件的意义、结果或责任，可链接 `event`；
- 谈论事件中可独立判断的具体事实，可链接 `event_fact`；
- `target_id` 必须逐字来自对应候选集合；
- 仅有词语重叠、同一角色出现或主题相近，不足以证明是同一对象。

### standalone

观点本身是相对持续的自我认知、价值判断、长期目标或不依赖某个具体剧情事实的主题时使用
`standalone / standalone_topic`，并令 `target_id` 为 `null`。

不要因为找不到合适候选，就把明显谈论具体事件的观点改判为 standalone。

### unresolved

以下情况应保留 `unresolved / unresolved`：

- 多个候选都合理，无法可靠区分；
- 观点显然涉及具体剧情，但正确对象不在候选集合中；
- subject 文本过于模糊；
- 当前证据不足以判断它是事件、事实还是独立主题。

保留 unresolved 是安全结果，不是标注失败。

## thought_aspect

`thought_aspect` 用来区分同一语义对象上的不同认知方面，例如：

- 事件是否已经结束；
- 事件发生的原因；
- 对责任的归属判断；
- 对结果或未来影响的预期。

它应简短、稳定、描述“观点在谈对象的哪个方面”。不要把完整 thought_text 重抄一遍，也不要使用空泛的
“想法”“观点”。语义相同的方面尽量使用一致措辞，避免无谓制造多个观点线程。

## 置信度与理由

- `link_confidence` 评价当前候选链接是否可靠，不评价原观点本身是否正确。
- 明确同一对象时可以较高；只靠间接语义时应降低。
- `reason_brief` 简要说明为什么是同一事件/事实、为什么是独立主题，或为什么仍无法确定。
- 理由必须基于 Prompt 中提供的观点和候选，不引用场外剧情知识。

## 完成前检查

- 每个 `target_id` 都来自当前 Prompt 的正确候选集合；
- 没有因关键词重叠强行链接；
- standalone 没有被用作“找不到候选”的兜底；
- 不确定项诚实保留 unresolved；
- `thought_aspect` 简短且能区分认知方面；
- source local ID 原样返回，response 为纯 JSON；
- 组装命令成功，输出中没有 response 相关 issue。
