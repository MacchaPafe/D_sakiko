# RAG 标注、审核与世界书发布命令

请在项目根目录运行命令，并设置 `PYTHONPATH=GPT_SoVITS`。路径均为示例，应替换成实际文件。

正常流程分为三层：逐集抽取、跨集长期知识聚合、审核后构建发布。正式发布不会直接使用旧逐集 Relation/Thought
产物，也不会把标注证据写进世界书包。

## 一、完成全部逐集输入

每集依次运行 Stage 1、Stage 2A、Stage 2B。需要 Codex 直接填写时，使用 render → 填写
`responses/` → assemble 的 Prompt Package 方式；也可以使用对应 `annotate-*` 一体化命令。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle ep01.ass \
  --output data/ep01_prepared.json \
  --timeline-id bang_dream_original \
  --story-year 3

PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage1-prompts \
  --prepared data/ep01_prepared.json \
  --output-dir data/prompts/ep01_stage1

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage1-responses \
  --manifest data/prompts/ep01_stage1/manifest.json \
  --output data/ep01_pass1_raw.json

PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage2-input \
  --prepared data/ep01_prepared.json \
  --annotation data/ep01_pass1_raw.json \
  --output data/ep01_stage2_input.json

PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2-prompts \
  --input data/ep01_stage2_input.json \
  --output-dir data/prompts/ep01_stage2a

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2-responses \
  --manifest data/prompts/ep01_stage2a/manifest.json \
  --output data/ep01_pass2_raw.json

PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2b-prompts \
  --input data/ep01_stage2_input.json \
  --stage2a-annotation data/ep01_pass2_raw.json \
  --output-dir data/prompts/ep01_stage2b

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2b-responses \
  --manifest data/prompts/ep01_stage2b/manifest.json \
  --output data/ep01_pass2b_raw.json
```

全部 01–13 集完成后，再进入跨集阶段。这样 Relation 与 Thought 能在完整时间范围内统一归一化和判断状态变化。

## 二、逐集生成并审核 Story Event / Lore

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag \
  --input data/ep01_stage2_input.json \
  --annotation data/ep01_pass2_raw.json \
  --output data/ep01_rag_review.json
```

重生成默认从现有输出迁移稳定 candidate ID 和未变化的人工审核。若已发布候选消失，命令不会覆盖旧 Review，
并会写出 `<output>.migration-report.json`。核对后可重复传入 `--allow-removed-id`，或使用
`--allow-all-removed` 允许本轮全部普通删除。

完成候选审核：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-item \
  --artifact data/ep01_rag_review.json \
  --item-id 'story_candidate:UUID' \
  --disposition publish
```

`reject` 和 `exclude` 必须提供合法 `--reason-code`；前者表示候选不成立，后者表示有依据但不属于目标包的长期知识。
完整内容修改使用 `edit-stage3-item --replacement replacement.json`，它会自动撤销旧审批；
`note-stage3-item` 只修改备注，不撤销审批。

## 三、全量 Relation 聚合与审核

按集数顺序重复输入参数。下面只展示两集；正式 MyGO 应提供 01–13 集。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-relation-prompts \
  --input data/ep01_stage2_input.json --annotation data/ep01_pass2_raw.json \
  --input data/ep02_stage2_input.json --annotation data/ep02_pass2_raw.json \
  --output-dir data/prompts/mygo_relations

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-relations \
  --manifest data/prompts/mygo_relations/manifest.json \
  --output data/mygo_relation_review.json
```

Relation 的审核单位是完整 Relation Type 状态序列，不是单个 State。使用 `review-stage3-item` 完成 Type；
未合并 Observation 只能人工 `reject` 或 `exclude`。状态序列修改仍使用 `edit-stage3-item`，replacement JSON
是完整 State 数组。

## 四、全量 Thought 聚合与审核

Thought Thread 按角色读取全部集数，由 LLM 协助完成跨集主题归一化、Thread 分组和状态变化判断；静态字符串算法
只负责校验覆盖、时间窗口和引用，不把文本相似度当作权威身份决定。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-thought-prompts \
  --input data/ep01_stage2_input.json \
  --stage2b-annotation data/ep01_pass2b_raw.json \
  --stage3-rag data/ep01_rag_review.json \
  --input data/ep02_stage2_input.json \
  --stage2b-annotation data/ep02_pass2b_raw.json \
  --stage3-rag data/ep02_rag_review.json \
  --output-dir data/prompts/mygo_thoughts

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-thought-responses \
  --manifest data/prompts/mygo_thoughts/manifest.json \
  --output data/mygo_thought_review.json
```

Thought 的审核单位是完整 Thread。未归属 Update 只能 `reject`/`exclude`，若要发布必须先通过完整序列编辑归入
Thread。`retracted` 关闭旧 State 的有效期，不生成一个“撤回观点”的正式 State。

## 五、Lore 去重

Story/Lore 候选全部审核完成后，按集重复输入生成唯一全量 decisions：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage3-lore-decisions \
  --input data/ep01_rag_review.json \
  --input data/ep02_rag_review.json \
  --output data/mygo_lore_decisions.json
```

完全相同且来源已审核的组会自动合并。其他组使用：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-lore-decision \
  --artifact data/mygo_lore_decisions.json \
  --group-id 'lore_group:...' \
  --action keep_separate
```

可选动作是 `keep_separate`、`merge` 和 `drop`；`merge` 需要 `--primary-candidate-id`，若人工改写结果再提供
`--replacement`。

## 六、build spec 与纯审计

一个 `worldbook_build.json` 只描述一个包，所有路径相对配置文件解析：

```json
{
  "format_version": 0,
  "package_id": "official.bang_dream.its_mygo",
  "package_version": "0.1.0",
  "display_name": "BanG Dream! It's MyGO!!!!!",
  "package_type": "season",
  "series_id": "its_mygo",
  "timeline_id": "bang_dream_original",
  "canon_branch": "main",
  "story_year": 3,
  "dependencies": [],
  "episodes": [
    {
      "episode": 1,
      "stage2_input": "data/ep01_stage2_input.json",
      "stage2a_annotation": "data/ep01_pass2_raw.json",
      "stage2b_annotation": "data/ep01_pass2b_raw.json",
      "rag_artifact": "data/ep01_rag_review.json"
    }
  ],
  "relation_review": "data/mygo_relation_review.json",
  "thought_review": "data/mygo_thought_review.json",
  "lore_decisions": "data/mygo_lore_decisions.json",
  "id_map": "data/mygo_entry_ids.json",
  "official_root": "GPT_SoVITS/rag/worldbooks/official",
  "build_root": ".build/mygo",
  "build_report": ".build/mygo/build-report.json"
}
```

`season` 包的 episodes 必须连续。Relation/Thought coverage、直接来源 SHA、全部人工终态、身份歧义、正式
Schema、Thought Event 引用、全局 UUID 和依赖图都会在构建时检查。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline validate-worldbook-build \
  --build-spec path/to/worldbook_build.json
```

validate 使用进程内临时 UUID，不修改 ID map、正式包或索引；只覆盖 `.build` 中的单次报告。

## 七、正式发布与全量重建

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-worldbook \
  --build-spec path/to/worldbook_build.json
```

发布顺序是：审核输入 → 分配正式 UUID → 构建 staging → 审计完整包集合 → 整体替换正式包 → 全量重建四张
世界书 collection。官方 JSON 已替换但索引失败时不会回滚 JSON；按输出的 worker rebuild 命令重试即可。

active identity 消失必须用 `--allow-removed-id` 或 `--allow-all-removed` 确认。误删后同一个 identity key
重新出现时，可用 `--reactivate-id` 或 `--reactivate-all` 恢复原 UUID；retired 身份不能自动恢复。

多包共同发布使用只引用单包配置的批量文件：

```json
{
  "format_version": 0,
  "build_specs": ["mygo/worldbook_build.json", "mujica/worldbook_build.json"],
  "build_report": ".build/batch-build-report.json"
}
```

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-worldbooks \
  --batch-spec path/to/worldbooks_batch.json
```

全部 staging 会先共同审计；成功后依次替换，最后只重建一次索引。依赖必须在各单包 build spec 中人工声明，
例如 `{"package_id":"official.bang_dream.its_mygo","version_spec":">=1.0.0,<2.0.0"}`。

## 八、一次性旧数据升级

只用于旧 Story/Lore 三表 artifact 和旧 point ID map。默认 dry-run；确认后增加 `--apply`。旧逐集
Relation/Thought 不迁移，应重新执行上述全量聚合。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline upgrade-stage3-review-schema \
  --package-id official.bang_dream.its_mygo \
  --artifact data/ep01_rag_ready.json \
  --stage2-input data/ep01_stage2_input.json \
  --annotation data/ep01_pass2_raw.json \
  --old-id-map data/old_entry_ids.json \
  --new-id-map data/mygo_entry_ids.json
```
