# Stage 3 Thought：跨集角色观点聚合指南（Codex）

## 任务

对一个角色在完整集数范围内的全部 Thought Update 进行语义归一化，形成少量 Thought Thread。Thread 表示
“同一角色对同一规范化对象、同一观点方面的一条持续状态线”，不是文本相似句子的集合。

本阶段可以使用 LLM 判断跨集写法是否指向同一主题，但输出只是一份机器候选。Thread 身份、拆分/合并及最终
发布处置仍需人工确认。

## 输入与 Prompt Package

按集数顺序重复三类参数，每一位置必须属于同一集：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-thought-prompts \
  --input data/ep01_stage2_input.json \
  --stage2b-annotation data/ep01_pass2b_raw.json \
  --stage3-rag data/ep01_rag_review.json \
  --input data/ep02_stage2_input.json \
  --stage2b-annotation data/ep02_pass2b_raw.json \
  --stage3-rag data/ep02_rag_review.json \
  --output-dir data/prompts/mygo_thoughts
```

每个任务对应一个角色，并包含该角色在全部输入集数中的 Update、可引用 Story Event 和只供推理使用的 Event Fact。
逐个读取 `manifest.json` 指定的 Prompt，把纯 JSON 写入对应 response；不要修改 Prompt 或 manifest。

## Thread 归一化原则

- 先判断语义对象，再判断 aspect；措辞不同不等于不同 Thread。
- 同一事件的“是否发生”“原因”“责任”“未来影响”可以是不同 aspect。
- 不要因为 Update 来自不同集就拆 Thread，也不要只因关键词相同就强行合并。
- `canonical_subject` 应是稳定、自包含的对象描述；`thought_aspect` 应简短说明角色在谈对象的哪个方面。
- 一个 Update 必须且只能进入一个 Thread，或明确列入未归属决定。
- 长期目标、自我认知或价值判断可形成 standalone 主题；一次性安排、瞬时情绪和普通行动通常应排除。

## 状态与 Transition

- `acquired`：此前没有该 Thread 的有效观点，现在形成第一个 State。
- `reaffirmed`：只是再次表达同一状态，不新建 State。
- `revised`：观点内容或认知立场实质改变，关闭旧 State 并建立后继 State。
- `retracted`：角色明确不再持有旧观点，只关闭旧 State，不生成“已撤回”的正式观点条目。

不要把每次重述都写成 revised。State 的 `thought_text` 必须是可独立注入角色上下文的完整命题，不依赖 evidence
或 Transition 才能理解。

## 引用与知情边界

- Story Event 引用只能从 Prompt 提供的 candidate ID 中选择。
- Event Fact 只用于帮助判断主题和状态，不作为正式 entry 发布。
- 不得把旁观者、在场角色或作品观众知道的事实自动写成该角色知道。
- `knows`、`believes`、`suspects`、`uncertain`、`rejects` 描述角色自己的认知立场。

## 组装和审核

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-thought-responses \
  --manifest data/prompts/mygo_thoughts/manifest.json \
  --output data/mygo_thought_review.json
```

组装器会校验 Update 覆盖、引用、时间线和状态有效期。之后以完整 Thread 为单位人工审核；任何 State 内容、
时间、引用、拆分或合并修改都会撤销旧审批。未归属 Update 只能 reject/exclude，若要发布必须先归入 Thread。

完成前检查：

- 每个角色的全部 Update 都已覆盖；
- 同义跨集写法已经归一化，但不同 aspect 没有误合并；
- reaffirmed 没有制造新 State；
- revised/retracted 的时间边界有输入证据；
- 没有引入角色当时不可能知道的信息；
- response 是纯 JSON，组装输出没有结构问题。
