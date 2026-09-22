# Stage 2B：Event Fact 与角色观点标注指南（Codex）

## 你的任务

根据单个场景的带说话人字幕及该场景已有 Story Event 候选，按需提取：

- `event_facts`：为角色观点、知情差异或秘密信息服务的客观事实；
- `character_thought_updates`：角色在当前阶段持有或发生变化的认知、推测、否认和主观解释。

目标是少量、可靠、对角色扮演有用的认知信息，不是把所有台词转写成观点或事实。

## 输入与输出文件

当前集统一使用 `epXX`：

- 场景输入：`annotations_stage2/epXX_stage2_input.json`
- 同集 Story Event 输入：`annotations_stage2/epXX_pass2_raw.json`
- Prompt Package：建议使用 `prompt_packages/epXX_stage2b/`
- 正式输出：`annotations_stage2/epXX_pass2b_raw.json`

两个输入必须来自同一集、同一版场景划分。不要将另一集或旧版本的 `pass2_raw.json` 与当前
`stage2_input.json` 配对。新一轮渲染使用新的 Package 目录。

## 第一步：确认输入

抽查两个输入文件：

- `metadata.episode` 一致；
- 两份输入的 `metadata.timeline_id` 一致；可空的 `story_year` 在能够确认时也应一致；
- Stage 2A 的每个结果能按 `scene_id` 对应到 Stage 2 Input；
- 台词说话人已经确认；
- Story Event 的 local ID 在各自场景内唯一。

Stage 2A 某场景失败或缺失时，不要凭剧情记忆替它补事件；先修复 Stage 2A，或接受该场景只标注真正
独立的 standalone 观点。

## 第二步：渲染静态 Prompt Package

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2b-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_stage2_input.json \
  --stage2a-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_pass2_raw.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage2b
```

通常一个场景对应一个任务。如果设置了 `--max-prompt-chars`，长场景可能被拆成多个重叠窗口，
manifest 中会出现类似 `scene_id__w001` 的任务。必须完成该场景的全部窗口，组装器才会合并去重。
正式标注不要无故调整 `--window-utterances` 和 `--window-overlap`。

## 第三步：填写 responses

请你按 `manifest.json` 的任务顺序读取每个 `prompt_file`，将纯 JSON 写入对应 `response_file`。不要跨 Prompt
补剧情，不要修改 manifest 或 Prompt。即使多个窗口属于同一场景，也应分别依据各自可见输入作答；
不要手工合并窗口。

## 第四步：校验并组装正式产物

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2b-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/epXX_stage2b/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/epXX_pass2b_raw.json \
  --model-label codex-workspace
```

场景任一窗口缺失或无效时，该场景会标为失败。查看输出中的 `results[].error`，修复对应窗口 response 后
重新组装。不要用 `--allow-partial` 把半个场景当作完成结果，也不要用 `--allow-stale` 混用不同输入版本。

## Event Fact 标注原则

Event Fact 不是 Story Event 的细粒度复刻。它只在能够支撑角色认知或知情隔离时存在。

适合生成 Fact 的情况：

- 当前角色观点明确引用了这个客观事实；
- 不同角色对该信息的知情范围明显不同；
- 涉及秘密、身份、原因、计划、责任等容易向角色泄漏的信息。

通常不要生成 Fact 的情况：

- 只是把一个 Story Event 拆成许多动作或逐句事实；
- 普通日程、临时动作、当场感受或无后续认知价值的细节；
- “某人认为、怀疑、希望……”这类主观内容；
- 仅凭沉默、离场、表情或 `present_characters` 推断出的所谓事实。

Fact 必须能独立判断真假，并有直接字幕证据。让 Event Fact 为 Character Thought 服务；如果没有观点链接、
知情差异或泄漏风险，就不要无谓拆分。

## Character Thought Update 标注原则

- 观点持有者必须由说话、内心独白或可靠的局部听见/目击证据支持。
- 明确台词通常只证明说话人持有该观点；不能因为其他角色列在 `present_characters` 中，就假定所有人都知道。
- 内心独白只属于该角色，不能传播给在场角色。
- 允许 `inferred`：例如角色明确听见一项信息后合理获得认知；但要在 `inference_note` 中写清推断链。
- 不推断字幕未表达的隐藏动机、真实原因或他人内心。
- 提取相对持续、会影响后续判断或说话的观点；删除后角色未来回答基本不变的琐碎内容通常不值得保存。
- 一次性计划、当场安排、指令和马上执行的承诺不属于 Character Thought；不要改写成“角色知道自己会做某事”。
- 不记录角色对自己刚完成、宣布或同意的行动所具有的显然自知，例如“自己刚邀请了某人”或“自己答应加入”。行动本身交给 Story Event。
- 只有跨场景持续的核心目标可以保留；应抽象成稳定的 `standalone_topic`，不要绑定到每次出现该目标的临时事件。
- 临时疼痛、疲劳、随口吐槽、单句辱骂和一时生气通常不属于 Character Thought。
- 瞬时人际态度通常属于关系观察；只有明确涉及事件意义、责任归属或原因判断时，才作为观点。

## 链接字段与认知状态

- `subject_kind=event`：必须引用当前 Prompt 中的 `about_event_local_id`。
- `subject_kind=event_fact`：必须引用当前 response 创建的 `about_fact_local_id`，必要时同时指向所属事件。
- `subject_kind=standalone_topic`：只用于不依赖某个具体剧情事实的持续自我认知、价值判断或长期目标。
- `subject_kind=uncertain`：确实谈论某个剧情对象、但当前场景无法可靠确定具体 ID 时使用；不要编造 ID。

`epistemic_status` 表示角色自己的确信程度：

- `knows`：把客观信息视为确定事实；
- `believes`：主观解释、评价或预测；
- `suspects`：怀疑、猜测、可能如此；
- `uncertain`：明确不知道或摇摆；
- `rejects`：明确否认一个命题。

`provisional_update_type` 只是单场景提示。明确刚得知才用 `acquired`，明确透露早已持有才用
`disclosed_existing`；无法从本场景判断观点是否变化时应使用 `unspecified`，不要擅自构造跨场景变化。
刚形成一个行动计划不等于获得认知，不要因此使用 `acquired`；`knows` 也不能用于编码愿望、计划或承诺。

## 证据与文本质量

- 每条记录的 evidence ID 必须来自当前 Prompt，并支持完整命题。
- `thought_text` 应是从该角色视角可直接注入的自包含命题，角色、对象和否定关系必须准确。
- 涉及角色自己时可以写“自己”，不要错误换成另一角色姓名。
- 同一事实或同一角色的近似观点不要重复输出。
- 可以输出空数组。宁可缺少低价值观点，也不要为了数量编造。
- 置信度只反映当前字幕证据，不得因自己熟悉作品剧情而提高。

## 完成前检查

- Fact 数量克制，没有把 Story Event 逐句拆开；
- 每条 Thought 的持有者有局部证据，未由“在场”直接推定知情；
- `subject_kind` 和本地引用 ID 严格匹配；
- standalone 确实不依赖具体剧情对象；
- 没有瞬时情绪、普通行动或重复记录；
- 多窗口场景的所有 response 均已完成；
- 组装成功，输出中没有 `results[].error`。
