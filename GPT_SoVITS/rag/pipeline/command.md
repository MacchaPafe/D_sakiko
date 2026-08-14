# 世界书数据集标注与发布指南

本文是日常标注的操作入口。正文按实际工作顺序说明每一步要做什么、由谁完成，以及如何开始。模型标注步骤只提供
可直接发送给 Codex 的提示词；正常使用时，不需要手动运行底层 Prompt Package 命令。

文中的作品名、集数和路径都是示例。开始前请替换成当前任务的实际值，并始终从项目根目录运行命令。

[toc]

## 一、总体流程

拿到一集后，按下面的顺序完成：

```text
第 0 步（可选）：没有可靠字幕时，生成并人工校对字幕
第 1 步：Codex 标注说话人，人工逐句复核
第 2A 步：Codex 提取剧情事件、人物关系线索和作品设定
第 2B 步：Codex 提取关键事实和人物观点
第 3 步：生成本集审核数据，人工审核剧情事件和作品设定
```

每一集都要完成第 1、2A、2B、3 步；有可靠 ASS 字幕时跳过第 0 步。第 3 步的数据按集生成，人工审核既可以
随每集完成，也可以等所有集数准备好后在统一工作台集中完成。

全部集数完成后，再继续：

```text
第 4 步：综合全季数据，整理并审核长期人物关系
第 5 步：综合全季数据，整理并审核人物观点变化
第 6 步：处理全季重复的作品设定
第 7 步：检查世界书是否完整
第 8 步：正式发布世界书
```

不要只用部分集数制作正式的长期人物关系或人物观点。加入新集数后，应重新基于扩展后的全量数据整理，而不是把
新一集的结果手工拼接到旧结果中。

## 二、第 0 步：生成并校对字幕（可选）

### 作用

从只有画面内嵌中文字幕的视频中得到可靠的 ASS 字幕。已有可靠 ASS 时直接进入第 1 步。

本步骤由程序先识别字幕，再由人工校对。未经复核的 OCR 结果不能进入后续标注。

### 怎么做

先扫描当前集视频：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline extract-video-subtitles \
  --video '/path/to/episode01.mp4' \
  --series-id yume_mita \
  --episode 1 \
  --output-dir GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita
```

然后打开字幕复核工作台：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-ocr-subtitles \
  --input 'GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita/yume_mita[01].review.json'
```

在工作台中：

- 修改识别错误的正文和时间；
- 必要时新增、拆分、合并或删除字幕；
- 删除 OP、ED 和插入歌歌词等不参与剧情标注的内容；
- 把所有 `pending` 项处理完；
- 点击“发布 ASS”。

完成后会得到正式的 `yume_mita[01].ass`。详细操作见
[字幕 OCR 指南](annotation_guides/subtitle_ocr.md)。

### 完成标准

- 工作台中的 `pending` 数量为零；
- 已发布正式 ASS；
- 正式 ASS 的台词和时间轴抽查无明显错误。

## 三、第 1 步：确认每句台词的说话人

### 作用

确定每句台词是谁说的、在对谁说、提到了谁、是否为内心独白，以及大致情绪。后续剧情、关系和人物观点都会依赖
这些信息，因此模型完成初标后必须人工复核。

### 先让 Codex 完成初标

把下面的提示词中的集数、字幕路径、作品和时间线替换成实际值，然后发送给 Codex：

> 请按照 `GPT_SoVITS/rag/pipeline/annotation_guides/stage1_speaker.md` 完成第 01 集的 Stage 1 标注。字幕文件是
> `/path/to/ep01.ass`，`series_id` 使用 `its_mygo`，`timeline_id` 使用 `bang_dream_original`，能够确认的
> `story_year` 为 3。请自行完成字幕预处理、创建新的 Prompt Package、填写全部任务、组装正式结果，并生成供人工
> 复核的 `GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json`。不要使用其他集数或旧 Package
> 的数据。完成后请报告输出文件位置、场景数量和失败场景；如有失败，请继续修复直至组装成功。

如果剧情学年无法确认，把提示词中的 `story_year` 改为“未知，不要猜测”。世界连续性尚未确认的新作品应使用独立、
稳定的 `timeline_id`。

### 再由人工复核

Codex 完成后，打开说话人复核编辑器：

```bash
PYTHONPATH=GPT_SoVITS python GPT_SoVITS/rag/pipeline/stage2_dataset_editor.py \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

逐句检查：

- 谁在说话；
- 是在对谁说；
- 话中提到了谁；
- 是否为内心独白；
- 情绪和置信度是否合理。

修改后点击右上角“保存”，或按 `Ctrl+S` / `Cmd+S`。外部标注者使用独立发布包时，构建和分发方式见
[`publish_pipeline/README.md`](../../../publish_pipeline/README.md)。

### 完成标准

- 当前集的所有字幕都已人工复核；
- 明显错误和低置信度项目已经处理；
- 修改后的 `ep01_stage2_input.json` 已保存。

## 四、第 2A 步：提取剧情、关系线索和作品设定

### 作用

从本集已经确认说话人的场景中提取：

- 相对完整、值得检索的剧情事件；
- 角色 A 对角色 B 在当前场景表现出的关系线索；
- 乐队、学校、地点、组织、歌曲等作品内稳定设定。

这里记录的是字幕能够支持的内容。连续发生的同一件事不应逐句拆分；一次性情绪也不应直接写成长期人物关系。
本步骤由 Codex 完成。

### 发送给 Codex

> 请按照 `GPT_SoVITS/rag/pipeline/annotation_guides/stage2a_document_extraction.md` 完成第 01 集的 Stage 2A
> 标注。输入文件是
> `GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json`。请创建新的 Prompt Package，完成全部
> 场景任务，组装正式输出
> `GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json`，并验证没有失败场景。不要修改已经人工复核
> 的输入文件；如果发现明显的说话人错误，请停止相关场景并报告，应先返回第 1 步修正。完成后请报告任务数量、
> 输出路径和校验结果。

详细判断标准见 [Stage 2A 标注指南](annotation_guides/stage2a_document_extraction.md)。

### 完成标准

- 全部场景都已处理；
- 正式输出文件已经生成；
- 组装结果中没有失败场景；
- 没有明显的逐句事件拆分或人物关系方向错误。

## 五、第 2B 步：提取关键事实和人物观点

### 作用

根据本集字幕和第 2A 步提取的剧情，记录对人物认知有用的关键事实，以及角色知道、相信、怀疑、否认或重新理解的内容。

> 输入内容举例：
> `GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json`，同集 Stage 2A 结果 `GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json`。

这一步关注会影响角色后续判断和说话的观点，不记录普通行动、一时情绪、随口吐槽或没有证据的隐藏动机。本步骤由 Codex 完成。

### 发送给 Codex

> 请按照 `GPT_SoVITS/rag/pipeline/annotation_guides/stage2b_thought_extraction.md` 完成第 01 集的 Stage 2B 标注。请创建新的 Prompt Package，完成全部任务，组装正式输出，并验证没有缺失窗口或失败场景。完成后请报告任务数量、输出路径和校验结果。
>
> 请使用该项目的 uv 环境。

详细判断标准见 [Stage 2B 标注指南](annotation_guides/stage2b_thought_extraction.md)。

### 完成标准

- 当前集所有场景和窗口都已完成；
- 正式输出文件已经生成；
- 组装结果中没有失败场景；
- 每条人物观点都有当前场景中的可靠依据。

## 六、第 3 步：生成并审核本集剧情与设定

### 作用

把第 2A 步得到的剧情事件和作品设定整理成正式审核候选，再由人工决定哪些内容进入世界书。

数据按集生成。首次处理一个新世界书时，还需要准备 `worldbook_build.json`，让统一工作台知道各集文件位于哪里。

本步骤可以直接执行命令完成

### 生成审核数据

使用如下命令生成对应集的审核数据，这一步需要第 1 步的说话人标注和第 2 步的剧情、关系线索、组织名称信息：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json
```

> 如果需要生成其他集的内容，修改对应文件名即可，比如把 "ep01" 改成 "ep02"

### 完成 `worldbook_build.json` 的构建

打开 `worldbook_build.json` 文件（通常位于 `GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json`，对不同的动漫，`its_mygo` 这部分可能不同）。

打开后，复制 episodes 下的单条数据，修改内容并将输入指向对应文件，比如：

```json
{
  "episode": 6,
  "stage2_input": "../../pipeline/data/annotations_stage2/ep06_stage2_input.json",
  "stage2a_annotation": "../../pipeline/data/annotations_stage2/ep06_pass2_raw.json",
  "stage2b_annotation": "../../pipeline/data/annotations_stage2/ep06_pass2b_raw.json",
  "rag_artifact": "reviews/ep06_rag_review.json"
},
```

### 再由人工审核

打开统一审核工作台：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-workbench \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

在当前集的 Story Events 和 Lore Entries 中逐条检查：

- 内容正确且值得收录：选择“通过”；
- 候选本身不成立：选择“不纳入世界书”，并选择相应错误原因；
- 内容可能正确，但太琐碎或当前不收录：选择“不纳入世界书”，并选择相应排除原因；
- Lore 显示“同名已收录”时：展开核对已收录内容；确认完全重复后，点击“将当前条目标记为重复”；
- 内容需要修改：先编辑完整内容，再重新通过；
- 暂时不能判断：标记“待跟进”。

完成后点击“保存全部”。修改内容会自动撤销旧审批，因此修改过的条目需要再次审核。

### 完成标准

- 本集 Story Events 和 Lore Entries 都已经处理；
- 没有遗留的待审核或待跟进项目；
- 所有修改都已保存。

完成当前集后，从第 0 或第 1 步开始处理下一集。所有集数都完成第 3 步后，再进入下面的全季流程。

## 七、第 4 步：整理并审核全季人物关系

### 作用

综合全部集数中的局部关系线索，整理出少量、相对持续的人物关系阶段。例如从戒备到认可，再到稳定信任，而不是把
每次争吵或关心都写成一个新的长期阶段。

Codex 先按有向角色对整理全季关系，人工再审核完整的关系发展线。

### 先让 Codex 整理

> 请按照 `GPT_SoVITS/rag/pipeline/annotation_guides/stage3_relation_aggregation.md`，使用当前作品已经完成的全部
> 集数整理长期人物关系。请从
> `GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json` 核对完整集数范围，按集数顺序使用每集的
> Stage 2 Input 和 Stage 2A 结果，创建新的全量 Prompt Package，完成所有有向角色对任务，并组装到
> `GPT_SoVITS/rag/annotated_data/its_mygo/mygo_relation_review.json`。请保留可以迁移的既有人工审核，不要只处理
> 新增集数，也不要使用部分结果冒充全量结果。完成后请报告覆盖集数、角色对数量、未归属关系线索、迁移结果和校验
> 问题。

详细判断标准见 [长期人物关系整理指南](annotation_guides/stage3_relation_aggregation.md)。

### 再由人工审核

如果工作台尚未打开，运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-workbench \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

进入 Relations，按完整关系发展线检查：

- 关系方向是否正确；
- 相似阶段是否应该合并；
- 是否把一次性情绪误判成长期变化；
- 新关系阶段的开始时间是否合理；
- 未归属的关系线索应归入现有关系，还是不纳入世界书。

必要时可以调整阶段、拆分或合并关系线。完成后重新通过并“保存全部”。

### 完成标准

- 全部有向角色对都已处理；
- 没有未归属且未决定的关系线索；
- 没有待审核或待跟进的关系；
- 所有修改都已保存。

## 八、第 5 步：整理并审核全季人物观点

### 作用

综合角色在不同集数中表达的想法，把围绕同一对象、同一方面的观点串成连续发展线，并判断观点何时形成、重复确认、
改变或撤回。

Codex 先按角色整理全季观点，人工再审核完整的观点发展线。

### 先让 Codex 整理

> 请按照 `GPT_SoVITS/rag/pipeline/annotation_guides/stage3_thought_aggregation.md`，使用当前作品已经完成的全部
> 集数整理人物观点变化。请从
> `GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json` 核对完整集数范围，按集数顺序使用每集的
> Stage 2 Input、Stage 2B 结果和 Story/Lore Review，创建新的全量 Prompt Package，完成所有角色任务，并组装到
> `GPT_SoVITS/rag/annotated_data/its_mygo/mygo_thought_review.json`。请保留可以迁移的既有人工审核，不要只处理
> 新增集数，也不要使用部分结果冒充全量结果。完成后请报告覆盖集数、角色数量、未归属观点、迁移结果、引用和时间线
> 校验问题。

详细判断标准见 [人物观点整理指南](annotation_guides/stage3_thought_aggregation.md)。旧的逐集 Thought Linking
流程不再用于新正式数据。

### 再由人工审核

如果工作台尚未打开，运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-workbench \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

进入 Thoughts，按完整观点发展线检查：

- 不同说法是否其实在谈同一件事；
- 不同问题是否被错误合并；
- 角色的观点是重复表达，还是真的发生了变化；
- 变化开始和结束的时间是否有证据；
- 角色当时是否确实知道相关信息；
- 未归属观点应归入现有观点线，还是不纳入世界书。

必要时可以调整阶段、拆分或合并观点线。完成后重新通过并“保存全部”。

### 完成标准

- 所有角色的观点都已覆盖；
- 没有未归属且未决定的观点；
- 没有待审核或待跟进的观点线；
- 所有修改都已保存。

## 九、第 6 步：处理重复的作品设定

### 作用

同一个学校、乐队、地点或组织可能在多集中被重复提取。这一步扫描全季已经通过的 Lore，并由人工决定相似条目是否
属于同一个设定。

### 先进行去重

运行如下命令：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage3-lore-decisions \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep02_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep03_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep04_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep05_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep06_rag_review.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/mygo_lore_decisions.json
```

其中需要传入每一集的的 ep0x_rag_review.json 文件，传入所有内容后，可以生成去重的实体信息。

### 再由人工决定

如果工作台尚未打开，运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-workbench \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

进入“Lore 去重”，逐组选择：

- **分别保留**：名称或内容相似，但实际是不同设定；
- **合并**：确实是同一个设定，并确认作为主体的条目和合并后内容；
- **全部丢弃**：这一组都不应进入世界书。

完全相同且来源已经审核的条目可能自动合并，不需要逐组点击。完成后“保存全部”。

### 完成标准

- 所有需要人工处理的重复组都有明确决定；
- 没有待处理的 Lore 重复组；
- 所有决定都已保存。

## 十、第 7 步：检查世界书是否完整

### 作用

在发布前统一检查集数、人工审核状态、人物关系和观点覆盖、事件引用、时间范围、ID 与依赖关系。此操作只检查，不会
修改正式世界书和索引。

### 怎么做

先在统一工作台点击“保存全部”，然后运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline validate-worldbook-build \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

也可以在统一工作台中查看只读构建审计。出现问题时，根据报告回到对应的 Story、Lore、Relation、Thought 或去重
页面修正，不要跳过失败项。

### 完成标准

- 校验结果成功；
- 不存在未审核、来源过期、引用失效或时间冲突；
- 构建报告没有阻止发布的问题。

## 十一、第 8 步：正式发布世界书

### 作用

把审核完成的数据生成正式世界书包，并重建 Story、Lore、Relation、Thought 四类检索索引。

### 怎么做

确认第 7 步通过后运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-worldbook \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json
```

如果命令报告已发布候选消失、旧身份恢复或索引重建失败，不要直接增加绕过参数；先按报告核对原因，再参考附录中的
故障恢复说明。

### 完成标准

- 正式世界书包已经更新；
- 四类世界书索引重建成功；
- 发布报告没有遗留错误。

## 十二、附录：手动运行、维护与故障恢复

正常使用 Codex 标注时，不需要手动执行本节中的模型标注命令。本节用于离线手工操作 Prompt Package、自动化、
排查失败任务、处理来源变化、迁移旧数据或执行多包发布。

### 12.1 重要路径

- `GPT_SoVITS/rag/pipeline/data/subtitle_ocr/<系列>/`：OCR 工作数据和有限截图缓存；
- `GPT_SoVITS/rag/pipeline/data/annotations_stage1/`：逐集说话人标注工作数据；
- `GPT_SoVITS/rag/pipeline/data/annotations_stage2/`：逐集 Stage 2A、2B 工作数据；
- `GPT_SoVITS/rag/pipeline/data/prompt_packages/`：可以重新生成的模型任务包；
- `GPT_SoVITS/rag/annotated_data/<世界书>/`：最终审核数据、ID map 和构建配置，应纳入 Git；
- `GPT_SoVITS/rag/pipeline/data/annotations_stage3/`：旧流程或临时文件，不应保存唯一的正式审核结果。

`pipeline/data` 已被 `.gitignore` 忽略。正式审核结果必须保存在 `annotated_data/<世界书>/`，不能只留在
`pipeline/data`。

### 12.2 手动完成第 1 步

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle ep01.ass \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --series-id its_mygo \
  --timeline-id bang_dream_original \
  --story-year 3

PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage1-prompts \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage1

# 按 manifest.json 填写全部 responses/ 后执行：
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage1-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage1/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --model-label codex-workspace

PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage2-input \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

学年未知时省略 `--story-year`。不要使用动画季度代替剧情学年。

### 12.3 手动完成第 2A、2B 步

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2a

# 填写全部 responses/ 后执行：
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2a/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --model-label codex-workspace

PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2b-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --stage2a-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2b

# 填写全部 responses/ 后执行：
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2b-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2b/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2b_raw.json \
  --model-label codex-workspace
```

每次重新渲染都应使用新的 Package 目录。不要用 `--allow-stale` 混合新输入和旧 Prompt；`--allow-partial` 只适合
排查未完成任务，不能作为正式全量结果。

### 12.4 手动生成逐集 Story/Lore Review

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep06_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep06_pass2_raw.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep06_rag_review.json
```

重新生成会尽量迁移稳定身份和未变化的人工审核。如果已发布候选消失，命令不会覆盖旧 Review，而会生成
`<output>.migration-report.json`。核对后才可使用 `--allow-removed-id <ID>`；不要把
`--allow-all-removed` 当作常规选项。

### 12.5 手动完成全季 Relation

每一个 `--input` 必须与同位置的 `--annotation` 属于同一集，并按集数顺序提供全部集数：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-relation-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_pass2_raw.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_relations

# 填写全部 responses/ 后执行：
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-relations \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_relations/manifest.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/mygo_relation_review.json \
  --model-label codex-workspace
```

### 12.6 手动完成全季 Thought

三类重复参数必须按位置属于同一集，并覆盖完整集数：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage3-thought-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --stage2b-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2b_raw.json \
  --stage3-rag GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_stage2_input.json \
  --stage2b-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep02_pass2b_raw.json \
  --stage3-rag GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep02_rag_review.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_thoughts

# 填写全部 responses/ 后执行：
PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage3-thought-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/mygo_thoughts/manifest.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/mygo_thought_review.json \
  --model-label codex-workspace
```

### 12.7 手动生成 Lore 去重数据

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage3-lore-decisions \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json \
  --input GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep02_rag_review.json \
  --output GPT_SoVITS/rag/annotated_data/its_mygo/mygo_lore_decisions.json
```

### 12.8 `worldbook_build.json` 示例

一个配置只描述一个世界书包，所有相对路径都从配置文件所在目录解析：

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
      "stage2_input": "../../pipeline/data/annotations_stage2/ep01_stage2_input.json",
      "stage2a_annotation": "../../pipeline/data/annotations_stage2/ep01_pass2_raw.json",
      "stage2b_annotation": "../../pipeline/data/annotations_stage2/ep01_pass2b_raw.json",
      "rag_artifact": "reviews/ep01_rag_review.json"
    }
  ],
  "relation_review": "mygo_relation_review.json",
  "thought_review": "mygo_thought_review.json",
  "lore_decisions": "mygo_lore_decisions.json",
  "id_map": "entry_ids.json",
  "official_root": "../../worldbooks/official",
  "build_root": "../../../../.build/worldbooks/its_mygo",
  "build_report": "../../../../.build/worldbooks/its_mygo/build-report.json"
}
```

`season` 类型的集数必须连续。作品之间存在依赖时，应在各自配置中显式声明，不能根据正文相似度推断。

### 12.9 CLI 审核备用入口

统一图形工作台是正常人工审核入口。以下命令只用于自动化或界面不可用时的故障恢复：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-stage3-item \
  --artifact GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json \
  --item-id 'story_candidate:UUID' \
  --disposition publish
```

- `review-stage3-item`：通过、不成立或排除一条审核项；
- `edit-stage3-item`：替换完整内容，且自动撤销旧审批；
- `note-stage3-item`：只修改备注；
- `followup-stage3-item`：标记待跟进；
- `restore-stage3-item`：删除人工修改并恢复机器版本；
- `review-lore-decision`：手工完成 Lore 合并、分别保留或丢弃；
- `clear-lore-decision`：把人工 Lore 决定恢复为待处理。

`reject` 表示候选不成立；`exclude` 表示内容可能有效，但当前不收录。CLI 使用两者时必须提供对应的合法
`--reason-code`。

### 12.10 来源变化与重新生成

上游字幕、说话人或 Stage 2 数据发生变化后，统一工作台会显示“来源过期”。正常做法是：

1. 先“保存全部”；
2. 查看来源变化；
3. 对内容有影响时显式重新生成对应 Review；
4. 重新检查被撤销或迁移的审批；
5. 只有确认变化不影响审核内容时，才在工作台接受当前来源。

不要用 `--fresh`、`--allow-stale` 或 `--allow-all-removed` 隐藏应当人工核对的变化。

### 12.11 已发布身份消失或重新出现

正式发布时，仍在使用的旧身份消失会阻止发布。应先核对迁移报告，再按精确 ID 确认：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-worldbook \
  --build-spec GPT_SoVITS/rag/annotated_data/its_mygo/worldbook_build.json \
  --allow-removed-id '<确认停用的身份 ID>'
```

误删后同一个身份重新出现时，可使用 `--reactivate-id '<身份 ID>'` 恢复原 UUID。批量参数
`--allow-all-removed` 和 `--reactivate-all` 只适合已经逐项核对的明确批处理。

如果正式 JSON 已经替换、但索引重建失败，JSON 不会自动回滚。应按照发布命令输出的 worker rebuild 命令重试，
不要重新修改审核数据。

### 12.12 多个世界书共同发布

批量配置只引用各单包的 `worldbook_build.json`：

```json
{
  "format_version": 0,
  "build_specs": ["mygo/worldbook_build.json", "mujica/worldbook_build.json"],
  "build_report": "../../../.build/worldbooks/batch-build-report.json"
}
```

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-worldbooks \
  --batch-spec GPT_SoVITS/rag/annotated_data/worldbooks_batch.json
```

系统会先共同审计全部 staging，成功后再依次替换正式包，最后只重建一次索引。

### 12.13 一次性升级旧数据

本节只用于旧 Story/Lore 三表文件和旧 point ID map。默认只预演，确认结果后才增加 `--apply`。旧的逐集
Relation/Thought 不迁移，应使用完整集数重新执行第 4、5 步。

如果旧文件仍在被忽略的 `pipeline/data/annotations_stage3/`，先复制到权威目录，再升级副本：

```bash
mkdir -p GPT_SoVITS/rag/annotated_data/its_mygo/reviews
cp GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json \
  GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json

PYTHONPATH=GPT_SoVITS python -m rag.pipeline upgrade-stage3-review-schema \
  --package-id official.bang_dream.its_mygo \
  --artifact GPT_SoVITS/rag/annotated_data/its_mygo/reviews/ep01_rag_review.json \
  --stage2-input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --old-id-map GPT_SoVITS/rag/annotated_data/its_mygo/ep01_entry_ids.json \
  --new-id-map GPT_SoVITS/rag/annotated_data/its_mygo/entry_ids.json
```

确认预演无误后，在同一升级命令末尾增加 `--apply`。
