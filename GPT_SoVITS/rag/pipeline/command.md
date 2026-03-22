# 所有数据收集过程需要的命令

这里记录了数据收集过程中所有需要执行的命令，按顺序跑一遍基本就可以了。

请注意，以下所有的命令都硬编码了格式化后的输入输出路径；在实际执行中，请修改这些路径字符串，指向你的文件所在的地方。

请在项目的根目录下运行这些命令（即 D_Sakiko 这层，不要在 GPT_SoVITS 这层运行）。

> 建议：终端里面删除特定位置的字符串再打字比较麻烦，可以直接编辑这个文件，把命令中的路径改掉，再复制到终端执行。

## 1. 说话人标注阶段

请你首先获取一份动漫的 ass 字幕文件，放到一个你喜欢的地方，记住这个文件的路径，然后运行这条命令来提取所有对话：

```bash
uv run python -m rag.pipeline prepare-stage1 \         
  --subtitle '你的字幕文件的路径' \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json 
```

然后，运行这条命令，让模型标注每段对话的说话人：

```bash
PYTHONPATH=GPT_SoVITS uv run python -m rag.pipeline annotate-stage1 --stream \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --model deepseek/deepseek-reasoner \
  --temperature 1 \
  --prompts-dir GPT_SoVITS/rag/pipeline/data/annotations_stage1/prompts_ep01
```

## 2. 剧情、关系、名词解释提取阶段

首先，你需要运行一条命令，将上一个阶段模型的原始输出转化为规范，好看的结构：

```bash
PYTHONPATH=GPT_SoVITS uv run python -m rag.pipeline build-stage2-input \
  --prepared GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --annotation GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_pass1_raw.json \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage2/ep01_stage2_input.json
```

在运行完上一条命令进行转换后，你可以可选的运行这条命令打开数据集编辑器，人工审查模型对说话人的标注是否正确：

```bash
python GPT_SoVITS/rag/pipeline/stage2_dataset_editor.py
```

最后，运行这条命令，让大模型从台词中提取剧情、关系等概括信息。

> 你需要先在终端设置你使用的 API KEY 的环境变量。例如，如果你使用官方 deepseek API，则应当设置 DEEPSEEK_API_KEY 这个
> 环境变量。

```bash
PYTHONPATH=GPT_SoVITS uv run python -m rag.pipeline annotate-stage2 \
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
PYTHONPATH=GPT_SoVITS uv run python -m rag.pipeline normalize-stage3-rag \
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

```bash
PYTHONPATH=GPT_SoVITS uv run python -m rag.pipeline import-stage3-rag \
  --input GPT_SoVITS/rag/pipeline/data/annotations_stage3/ep01_rag_ready.json \
  --qdrant-location knowledge_base/default_world_info \
  --qdrant-connect-type local \
  --embedding-model-path GPT_SoVITS/pretrained_models/multilingual-e5-small
```