# 在 JS 中使用 multilingual e5 small

使用 `@huggingface/transformers` 在 Node.js 或 Electron 的 Node 进程中运行本地模型，输出 384 维文本向量。模型加载后可离线使用，运行时不需要 Python。

## 模型文件

导出的模型位于项目根目录下的 `GPT_SoVITS/pretrained_models/multilingual-e5-small-onnx/`：

```text
multilingual-e5-small-onnx/
├── config.json
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── export_metadata.json
└── onnx/
    └── model.onnx
```

部署时需要配置、tokenizer 文件及 `onnx/model.onnx`；`export_metadata.json` 用于记录权重来源。原始 `model.safetensors` 不需要随 JS 模型分发。模型文件不在 Git 中，分发时需单独复制。

## 运行示例

需要 Node.js 22.12 或更高版本。在项目根目录执行：

```bash
cd dsakiko-electron/scripts/demos/e5-embedding
npm ci
npm run demo -- '祥子为什么离开了乐队？'
```

依赖固定为 Transformers.js 3.8.1。示例会读取上述模型目录，计算查询和三条正文的相似度，并按相关性输出结果。自定义模型位置可设置 `E5_MODEL_PATH` 为模型目录的绝对路径。

## 在代码中调用

将下面的代码放在本目录的 `.mjs` 文件中：

```js
import { fileURLToPath } from 'node:url'
import { createE5Encoder, similarity } from './e5-embedding.mjs'

const modelDirectory = fileURLToPath(
  new URL('../../../../GPT_SoVITS/pretrained_models/multilingual-e5-small-onnx/', import.meta.url)
)
const encoder = await createE5Encoder(modelDirectory)

try {
  const [query] = await encoder.encodeQueries(['祥子为什么离开乐队？'])
  const [passage] = await encoder.encodePassages(['祥子退出了原来的乐队。'])
  console.log(similarity(query, passage))
} finally {
  await encoder.close()
}
```

`encodeQueries()` 自动添加 `query: `，`encodePassages()` 自动添加 `passage: `，调用时传入正文即可。两者均接受字符串数组并返回向量数组。知识正文可提前编码并保存，查询时只需编码用户输入，再计算余弦相似度或交给向量数据库检索。

编码器使用 FP32、最多 512 tokens、带 attention mask 的平均池化和 L2 归一化，并处理长文本截断后的结束符。相似度越大越相关；是否返回条目仍需结合业务阈值、角色视角和剧情进度等过滤规则。

每个工作进程加载一次编码器并复用，退出时再调用 `close()`。Electron 中建议放在独立的 Node Worker 或 utility process，避免占用界面进程。可通过 `createE5Encoder(modelDirectory, { threads: 4, batchSize: 32 })` 设置 CPU 线程数和批量大小。

## 配合 LanceDB 使用

本目录同时提供本地持久化示例，依赖固定为 `@lancedb/lancedb` 0.39.0 和 `apache-arrow` 18.1.0。不需要另启数据库服务，运行时不需要 Python。接口详情见 [LanceDB 官方 JS 文档](https://lancedb.github.io/lancedb/js/classes/VectorQuery/)。

在本目录执行，先导入当前 MyGO 官方世界书，再查询已保存的数据库：

```bash
npm ci
npm run lancedb -- build
npm run lancedb -- query 'CRYCHIC 是什么乐队？'
npm run lancedb -- query '灯以前参加过乐队吗？' --type character_thought --character anon --time 4049
npm run lancedb -- query '爱音和灯之间发生了什么？' --type story_event --character anon --time 4099
```

数据库默认保存在项目 `.scratch/e5-lancedb-demo/`，第二次 `build` 会拒绝覆盖已有表。可设置 `E5_LANCEDB_PATH` 为新的绝对目录来重新建库，设置 `E5_MODEL_PATH` 指定模型目录。`--time` 使用已有世界书时间编码：MyGO 第 1 集末为 `4049`，第 2 集末为 `4099`。

代码分为两步：

1. 导入：`loadOfficialWorldbook(packageDirectory)` 读取四类条目的 `retrieval_text`；`importWorldbook(db, encoder, rows)` 添加 `passage: ` 前缀、编码并保存 384 维 Float32 向量和过滤字段。
2. 查询：`encodeQueries()` 生成查询向量，`searchWorldbook()` 在允许的包、时间线、分支、剧情时间及角色范围内检索。

下面的代码可放在本目录的 `.mjs` 文件中，读取已建好的表：

```js
import * as lancedb from '@lancedb/lancedb'
import { createE5Encoder } from './e5-embedding.mjs'
import { searchWorldbook } from './lancedb-worldbook.mjs'

const db = await lancedb.connect('/可写目录/worldbook')
const table = await db.openTable('worldbook')
const encoder = await createE5Encoder('/模型目录/multilingual-e5-small-onnx')
try {
  const [vector] = await encoder.encodeQueries(['CRYCHIC 是什么乐队？'])
  const results = await searchWorldbook(table, vector, {
    packageIds: ['official.bang_dream.its_mygo'],
    timelineId: 'bang_dream_original', canonBranch: 'main',
    entryType: 'lore_entry', queryTime: 4099
  }, { limit: 3 })
  console.log(results)
} finally {
  table.close()
  db.close()
  await encoder.close()
}
```

`character_thought` 通过 `subjectCharacterId` 限定观点所属角色；`character_relation` 通过 `subjectCharacterId`、`objectCharacterId` 限定双方；`story_event` 通过 `knownByCharacterId` 限定知情角色。应由业务层填入约束，避免查询超出角色视角。

底层调用为 `table.vectorSearch(vector).distanceType('cosine').where(条件).limit(数量)`。`where` 默认在 Top-K 前过滤；示例使用精确扫描，没有创建 ANN 索引。LanceDB 返回的 `_distance` 越小越相关，转换为 `score = 1 - _distance` 后再应用 `minScore` 阈值；标题和标签加分在阈值之后计算。

示例只导入当前无依赖的官方 Schema v0 包；包依赖解析、用户覆盖及完整对话注入仍由业务层实现。模型或编码规则变化后需重建向量表。Electron 部署时将数据库放在可写的用户数据目录，并将 LanceDB、ONNX Runtime 的 `.node` 原生文件放在 ASAR 外。

## 重新导出

仅在原始权重更新时需要重新导出。Python 环境需包含 `torch`、`transformers` 和 `onnx`。从项目根目录运行：

```bash
.venv/bin/python dsakiko-electron/scripts/demos/e5-embedding/export_onnx.py \
  --output /新的模型输出目录
```

导出脚本只读取本地权重，不下载模型；输出目录必须位于原模型目录之外，已有 ONNX 文件不会被覆盖。
