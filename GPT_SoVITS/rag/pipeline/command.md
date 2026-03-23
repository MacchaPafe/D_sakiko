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

如果你想在本地测试标注效果，请下载一个 embedding 模型。如果不在本地测试导入效果，只标注数据，那么这不是必须的。

https://huggingface.co/intfloat/multilingual-e5-small

将这个模型下载到 GPT_SoVITS/pretrained_models/multilingual-e5-small 文件夹下。下载示例代码如下：

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("intfloat/multilingual-e5-small")
model.save("GPT_SoVITS/pretrained_models/multilingual-e5-small")
```

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

最后，运行这条命令，让大模型从台词中提取剧情、关系等概括信息。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline annotate-stage2 \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --model deepseek/deepseek-reasoner \
  --temperature 1 \
  --prompts-dir GPT_SoVITS/rag/pipeline/data/annotations_stage2/prompts_ep01 \
  --stream
```

## 3. 插入数据库阶段

首先，运行这条命令把第二阶段 LLM 返回的原始数据转化为能直接入库的格式：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline normalize-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_pass2_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json
```

然后，如果你想仔细审查模型提取的剧情信息，请你启动这个数据集编辑脚本：

```bash
python GPT_SoVITS/rag/pipeline/stage3_dataset_editor.py \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json
```

最后，运行这条命令把文件中的数据插入到 qdrant 数据库中：

> 这个步骤并不必要；如果你只是在标注数据，把 ep01_rag_ready.json 或者其他名称的，你在上一步中审查的文件发送回来就好了。

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline import-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json \
  --qdrant-location knowledge_base/default_world_info \
  --qdrant-connect-type local \
  --embedding-model-path GPT_SoVITS/pretrained_models/multilingual-e5-small
```

## 4. 本地运行测试

> 这个阶段不是必要的

如果你想在本地测试标注数据的效果，那么请使用根目录下临时新增的 qdrant_test.py。你需要先执行上方”将文件数据插入到数据库“阶段的命令，把数据插入到数据库中，然后运行 qdrant_test.py。

qdrant_test.py 默认接受你的输入，然后从三个表（角色剧情、角色关系、名词解释）中各自检索一条最相关的信息并打印出来。
如果你想查看更多信息，请更改其中的 `top_k_per_collection=1` 这个参数。
