# 所有数据收集过程需要的命令

这里记录了数据收集过程中所有需要执行的命令，按顺序跑一遍基本就可以了。

请注意，以下所有的命令都硬编码了格式化后的输入输出路径；在实际执行中，请修改这些路径字符串，指向你的文件所在的地方。

请在项目的根目录下运行这些命令（即 D_Sakiko 这层，不要在 GPT_SoVITS 这层运行）。

> 建议：终端里面删除特定位置的字符串再打字比较麻烦，可以直接编辑这个文件，把命令中的路径改掉，再复制到终端执行。

## 安装依赖

请通过 pip 安装部分新增的依赖。

```bash
pip install jinja2 pysubs2 nicegui qdrant-client sentence-transformers
```

其中，只有 `qdrant-client` 和 `sentence-transformers` 是之后主程序执行时需要新增的依赖，其他依赖都只用于数据集的标注。

当前正式运行环境已经直接包含这两个世界书索引依赖。标注任务使用 `--timeline-id` 和可选 `--story-year`，不再使用旧 `SeasonId`。

审核后的 Stage 3 artifact 可通过 `publish-worldbook` 转换为不含证据字段的官方世界书包；新来源只有显式传入 `--allocate-new-ids` 才会写入开发侧稳定 ID map。

如果你想在本地测试标注效果，请下载一个 embedding 模型。如果不在本地测试导入效果，只标注数据，那么这不是必须的。

https://huggingface.co/intfloat/multilingual-e5-small

将这个模型下载到 GPT_SoVITS/pretrained_models/multilingual-e5-small 文件夹下。下载示例代码如下：

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("intfloat/multilingual-e5-small")
model.save("GPT_SoVITS/pretrained_models/multilingual-e5-small")
```

## Prompt Package 拆分模式

所有需要 LLM 的标注步骤现在都支持两种执行方式：

- 原有的一体化命令：例如 `annotate-stage2`，一次完成 Prompt 渲染、API 请求和结果组装。
- Prompt Package：先渲染静态 Prompt，再由 Codex 或 LiteLLM 生成回复，最后校验并组装正式 artifact。

让 Codex 直接参与标注时，请只向它提供当前小阶段对应的独立指南。指南索引位于
[annotation_guides/README.md](annotation_guides/README.md)。

Prompt Package 目录包含 `manifest.json`、`prompts/` 和 `responses/`。`manifest.json` 会记录输入文件、
模板和每个 Prompt 的 SHA-256；组装时默认拒绝输入或 Prompt 已变化的过期 Package。不要修改
`prompts/` 中的内容。让 Codex 标注时，可以让 agent 阅读 `manifest.json` 和 `prompts/`，并把每个任务的
纯 JSON 回复写到 manifest 声明的 `responses/` 路径。

如果仍想通过 API 请求已经渲染的 Package，可在渲染与组装命令之间运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline complete-prompt-package --stream \
  --manifest 你的_prompt_package目录/manifest.json \
  --model deepseek/deepseek-reasoner \
  --temperature 1
```

该命令默认跳过已经存在的非空回复，适合中断后继续；只有传入 `--overwrite` 才会覆盖它们。
模型请求失败不会删除已经完成的回复。若使用 Codex 直接填写 `responses/`，则不需要运行此命令。
使用 LiteLLM 请求后，组装命令应通过 `--model-label` 写入实际模型名；默认值
`codex-workspace` 是为 Codex 直接标注准备的。

五类 LLM 步骤对应的拆分命令如下：

| 标注内容 | 渲染 Prompt | 校验、组装正式结果 |
| --- | --- | --- |
| Stage 1 说话人 | `render-stage1-prompts` | `assemble-stage1-responses` |
| Stage 2A 剧情、关系观察、名词 | `render-stage2-prompts` | `assemble-stage2-responses` |
| Stage 2B Event Fact、角色观点更新 | `render-stage2b-prompts` | `assemble-stage2b-responses` |
| Stage 3 长期角色关系 | `render-stage3-relation-prompts` | `assemble-stage3-relations` |
| Stage 3 unresolved 观点链接 | `render-stage3-thought-link-prompts` | `assemble-stage3-thoughts` |

例如，Stage 2A 的完整拆分流程是：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline render-stage2-prompts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --output-dir GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2a

# 在这里让 Codex 填写 responses/，或运行 complete-prompt-package。

PYTHONPATH=GPT_SoVITS python -m rag.pipeline assemble-stage2-responses \
  --manifest GPT_SoVITS/rag/pipeline/data/prompt_packages/ep01_stage2a/manifest.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --model-label codex-workspace
```

其余阶段使用相同的目录约定。可以用各命令的 `--help` 查看输入参数。Stage 2B 会把长场景的每个窗口
作为独立任务，组装时仍复用原有的窗口合并逻辑；Stage 3 长期关系按有向角色对建立任务；Stage 3
观点链接只为确定性规则无法解决的 `unresolved` 更新建立任务。

## 1. 说话人标注阶段

请你首先获取一份动漫的 ass 字幕文件，放到一个你喜欢的地方，记住这个文件的路径，然后运行这条命令来提取所有对话：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle '你的字幕文件的路径' \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json
```

> 建议从字幕组的如下仓库中获取 MyGO 字幕：https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage/tree/master/BanG_Dream/MyGO
>
> 请使用 JPSC 后缀的字幕（即日文-简体中文）版本。

然后，你需要设置你的 API KEY 作为环境变量。例如，如果你使用官方 deepseek API，则应当设置 DEEPSEEK_API_KEY 这个环境变量。如果使用 OpenAI，
就设置 OPENAI_API_KEY。

然后，运行这条命令，让模型标注每段对话的说话人：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline annotate-stage1 --stream \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --model deepseek/deepseek-reasoner \
  --temperature 1 \
  --prompts-dir GPT_SoVITS/rag/pipeline/data/annotations_stage1/prompts_ep01
```

> 请将命令中的 deepseek/deepseek-reasoner 替换为你实际的模型。代码采用 litellm，所以 openai 旗下的模型都需要写为 openai/gpt-5 这样的带前缀形式。
> 请注意不要照抄命令中的路径，确保这些路径是存在的，且 ep01_prepared.json 是上一个命令的输出文件。

## 2. 剧情、关系、名词解释提取阶段

首先，你需要运行一条命令，将上一个阶段模型的原始输出转化为规范，好看的结构：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline build-stage2-input \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

在运行完上一条命令进行转换后，你可以可选的运行这条命令打开数据集编辑器，人工审查模型对说话人的标注是否正确：

```bash
python GPT_SoVITS/rag/pipeline/stage2_dataset_editor.py
```

最后，运行这条命令，让大模型从台词中提取剧情、场景级关系观察等概括信息。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline annotate-stage2 \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --model deepseek/deepseek-reasoner \
  --temperature 1 \
  --prompts-dir GPT_SoVITS/rag/pipeline/data/annotations_stage2/prompts_ep01 \
  --stream
```

接着运行独立的 Stage 2B。它会读取同一份场景输入以及上一步的 Story Event 候选，按需提取
Event Fact 和角色观点更新；该步骤不会修改现有三张表，也不会直接写入 Qdrant。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline annotate-stage2b \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --stage2a-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2b_raw.json \
  --model deepseek/deepseek-v4-pro \
  --temperature 1 \
  --prompts-dir GPT_SoVITS/rag/pipeline/data/annotations_stage2/prompts_ep01_thoughts \
  --stream
```

Stage 2B 输出是跨场景观点聚合的输入。`Event Fact` 只为角色观点的证据和链接服务，
不会作为一张独立的 Qdrant 表导入。

Stage 2 输出的 `relation_observations` 还不是长期关系。使用下面的命令按系列、季度、剧情分支和
有向角色对进行一次全量 LLM 聚合，生成带证据链、有效时间和风险标记的关系 State：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline aggregate-stage3-relations \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_relation_states_review.json \
  --model deepseek/deepseek-v4-pro \
  --temperature 1
```

全季度重建时，按相同顺序重复传入每一集的 `--input` 与 `--annotation`。聚合器会先校验它们属于同一系列、季度和剧情分支，再按有向角色对统一调用 LLM。

## 3. 插入数据库阶段

首先，运行这条命令把第二阶段 LLM 返回的原始数据转化为能直接入库的格式：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --relation-aggregation GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_relation_states_review.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json
```

如果省略 `--relation-aggregation`，命令仍会兼容旧数据，按原有逐场景关系方式生成结果；新数据建议始终提供聚合产物。

然后，如果你想仔细审查模型提取的剧情信息，请你启动这个数据集编辑脚本：

```bash
python GPT_SoVITS/rag/pipeline/stage3_dataset_editor.py \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json
```

角色观点使用独立的 Stage 3 规范化命令。它会精确链接当前场景本地 ID、聚合 Thought Thread、
计算有效期，并为每条记录生成可解释的风险标记：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-thoughts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --stage2b-annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2b_raw.json \
  --stage3-rag GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json \
  --link-model deepseek/deepseek-v4-pro \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_thoughts_review.json
```

`--link-model` 只处理本地 ID 无法确定的跨场景链接，并且只能从时间兼容的少量候选中选择；
它也可以把“并非由某个具体剧情事实产生”的观点判为 `standalone`。省略该参数时，无法精确链接的
记录会安全地保留为 `unresolved`，等待人工处理。

使用风险优先查看器进行人工复核：

```bash
PYTHONPATH=GPT_SoVITS python GPT_SoVITS/rag/pipeline/stage3_thought_dataset_editor.py \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_thoughts_review.json \
  --stage2-input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

查看器允许浏览全部记录，也可以只筛选高风险内容。默认只有标记为 `approved` 或 `edited`
的合法记录会被导入；`unresolved` 和结构非法记录始终不会入库。

最后，运行这条命令把文件中的数据插入到 qdrant 数据库中：

> 这个步骤并不必要；如果你只是在标注数据，把 ep01_rag_ready.json 或者其他名称的，你在上一步中审查的文件发送回来就好了。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline import-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json \
  --qdrant-location knowledge_base/default_world_info \
  --qdrant-connect-type local \
  --embedding-model-path GPT_SoVITS/pretrained_models/multilingual-e5-small
```

最后将复核通过的角色观点写入第四张 collection：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline import-stage3-thoughts \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_thoughts_review.json \
  --qdrant-location knowledge_base/default_world_info \
  --qdrant-connect-type local \
  --embedding-model-path GPT_SoVITS/pretrained_models/multilingual-e5-small
```

## 4. 本地运行测试

> 这个阶段不是必要的

如果你想在本地测试标注数据的效果，那么请使用根目录下临时新增的 qdrant_test.py。你需要先执行上方”将文件数据插入到数据库“阶段的命令，把数据插入到数据库中，然后运行 qdrant_test.py。

qdrant_test.py 默认接受你的输入，然后从三个表（角色剧情、角色关系、名词解释）中各自检索一条最相关的信息并打印出来。
如果你想查看更多信息，请更改其中的 `top_k_per_collection=1` 这个参数。
