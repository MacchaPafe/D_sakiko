# Python 语音调度接口设计

本文与 `interface.py` 描述正式目标契约；`contracts.py` 为可审查的数据声明，兼容 Python 3.9、仅依赖标准库。同目录已有旧快速原型调度器、HTTP 和 worker，不能据此认定符合本设计。2026-10-08 新增的采样字段和启动配置仅为声明，未修改 HTTP/worker 或推理行为。

2026-10-25：当前 HTTP/worker 已接收采样参数，并实现下述 GPT-SoVITS 显式文件启动契约；这不表示本文其余目标能力均已完成。

## GPT-SoVITS 文件启动契约

Electron 启动 `python -B -u -m speech.server --assets-root <资源库> --output <缓存输出> --concurrent N --resident N` 后，通过 stdin 写入一行 JSON 并关闭管道：

```json
{
  "architecture": "v2",
  "device": "cpu",
  "precision": "float32",
  "resources": {
    "bert": {"config": "/绝对路径/甲", "weights": "/另一目录/乙", "tokenizer": "/目录/丙"},
    "hubert": {"config": "/目录/丁", "weights": "/目录/戊", "featureExtractor": "/目录/己"},
    "g2pw": {
      "model": "/目录/庚", "config": "/目录/辛",
      "polyphonicCharacters": "/目录/壬", "monophonicCharacters": "/目录/癸",
      "bopomofoToPinyin": "/目录/子", "characterBopomofo": "/目录/丑"
    },
    "languageDetector": {"model": "/目录/寅"}
  }
}
```

所有路径必须指向已存在、可读的普通文件，名称和父目录可以任意。v4 还需 `resources.vocoder`，v2Pro/v2ProPlus 还需 `resources.speakerEncoder`；LoRA 在实际加载角色时要求 `resources.baseSovits`。角色 GPT、SoVITS 和参考音频仍按任务传入，HTTP 校验其解析后路径处于 `--assets-root` 内。启动清单来自可信宿主管道，不允许 HTTP 请求重配基础资源。

`gpt_sovits_resources.py` 负责文件契约和 BERT/HuBERT 装配，导入该模块不会加载推理依赖。BERT/HuBERT 当前支持单个 PyTorch 权重文件；BERT 分词器使用 tokenizer.json。配置与权重不匹配时失败，不寻找默认模型或自动下载。G2PW 配置仍是模型附带的受信任 Python 配置，其读取不依赖扩展名，也不生成相邻字节码。文本模块的导入期初始化使用 worker 一次性配置的资源快照，禁止在同一 worker 内切换资源。日志、运行缓存和合成结果使用单独的可写目录。

持久化设置见 [资源文档](../../docs/data-directories-and-resources.md#GPT-SoVITS-显式文件清单)。本次没有旧配置迁移、目录视图、导入向导、下载器或多引擎实现。

## 运行目录清理

`runtime.py` 管理 `<cacheRoot>/speech-temp/run-<UUID>/`。启动时先扫描残留，再创建本次目录和 `.owner.json`，记录宿主 PID、Python PID 及 POSIX 独立进程组；登记成功前不启动 worker。Electron 通过 `DSAKIKO_SPEECH_HOST_PID` 传入自身 PID，独立调试时默认使用 Python 的父 PID。

Electron 正常退出先拒绝新的语音提交和查询，等待在途请求及音频导入结束，此时仍保留宿主心跳接口。随后关闭业务与 Python。Python 收到 `/shutdown` 后，等待全部 worker 和执行任务结束，再删除本次目录。worker 淘汰或失败时独立清理自己的缓存子目录；任务失败后的临时 WAV 只在写入进程结束后删除。关闭确认失败时保留目录；删除失败记录日志，归属记录最后删除，方便重试。

下次语音启动只回收归属有效、宿主和 Python 均已退出且 POSIX 进程组也不存在的目录；存活、查询权限不足、损坏或缺失归属记录、旧无标记目录及运行目录符号链接均保留。不以时间长短推断是否仍在使用。Windows 只检查主进程，不增加完整进程树追踪。若 Python 因信号或心跳自行结束，但宿主仍存活且没有 `/shutdown` 的交接确认，则保留运行目录，防止删除仍在导入的音频，待后续启动回收。

清理仅覆盖本次输出和可重建缓存，不删除模型原件、资源库已导入音频或 `logs/speech.log`。Matplotlib 的直接导入已移入学习率调度器的独立绘图演示；当前第三方 TorchMetrics 仍会间接导入 Matplotlib，因此保留 worker 的缓存重定向，可能产生的字体缓存也在上述清理范围内。

test_`runtime.py`、`test_lifecycle.py` 和 Node 生命周期测试覆盖真实进程异常终止后的回收、正常 HTTP 关闭、活动目录保护、失败重试、worker 删除时机和音频导入与退出并发。

## 文件加载验证

- `python -m unittest discover -s python/tests -v`：标准库契约和生命周期测试；安装推理依赖时也运行真实小型 BERT/HuBERT 网络、分词器与 G2PW 文件装配测试。
- 可选真实推理测试：设置 `DSAKIKO_TEST_SPEECH_CONFIG` 为 JSON 文件路径，包含 `gptSovits`（上面的启动对象）和 `voice`（任务中的声音配置字段），执行 `python -B -m unittest discover -s python/tests -p test_gpt_sovits_inference.py -v`。

真实推理测试在临时目录中为每个输入文件创建独立的无后缀硬链接，再通过真实 worker 合成中、日文短句并验证 WAV 输出。测试模型与临时目录需位于支持硬链接的同一文件系统；该操作仅用于验证文件位置无关性，不是产品导入功能。Open JTalk 词典需预先安装，或通过 `OPEN_JTALK_DICT_DIR` 指定已有词典位置。

2026-10-25 在 macOS、Python 3.11、CPU/float32 环境下，v2ProPlus 与 v4 均通过上述真实合成测试，输出分别为 32 kHz 和 48 kHz 的非静音单声道 WAV。Python 3.9 完成语法解析检查；Windows、CUDA/MPS 和 LoRA 实际推理尚未验证。

- [contracts.py](contracts.py)：合成输入、任务快照、问题和启动配置。
- [interface.py](interface.py)：`SpeechScheduler` 的四个异步方法。
- [Node 客户端](../../src/backend/speech/interface.js)：相同任务能力，以及隐藏轮询的 `waitForResult`。

## 职责与数据

Python 独立持有任务队列、提交序号、执行槽、模型实例和结果记录，负责选择下一个任务、模型加载与复用、并发限制、输出文件及取消后的清理。HTTP 层不维护第二份队列；Node 不决定哪个模型何时加载或哪个任务进入执行槽。

Conversation 决定需要合成的文本、优先级和取消范围，并保存消息与任务 ID 的对应关系。Python 不接收 Chat、Turn、Message、角色身份、guide、播放进度、任务组或取消原因。

Node 客户端把 `AssetRef` 解析为 Python 可读取的受控本地绝对路径，把已解析的读音覆盖转换成最终文本。GPT 权重、SoVITS 权重、参考音频、参考文本及参考语言共同构成一次任务固定的声音配置。合成语言单独指定；语速、句间间隔和 `sampling` 可省略，Python 在受理时固定默认值。`sampling` 包含 `top_k`、`top_p`、`temperature`、`seed`、`sample_steps`，Node 与 Python 字段按 camelCase/snake_case 映射；不可用参数或权重不支持的采样步数在受理前拒绝。资源的本地访问与引用持有通过内部装配衔接，不向页面新增读盘入口。

Python 返回任务 ID；成功时返回 `audio_path` 和 `duration_ms`。Node 将完整文件导入资源模块，才向业务交付含 `AssetRef` 的成功结果。模型路径和输出路径均不进入聊天存档。

## 四个操作

| Python 方法                        | Node 方法                        | 语义                                     |
| ---------------------------------- | -------------------------------- | ---------------------------------------- |
| `submit(request)`                  | `submit(request)`                | 受理完整请求并返回任务 ID，不等待推理。  |
| `get_task(task_id)`                | `getTask(taskId)`                | 查询状态；成功快照同时包含结果。         |
| `set_priority(task_ids, priority)` | `setPriority(taskIds, priority)` | 批量调整仍在排队的任务，保留原提交顺序。 |
| `cancel(task_ids)`                 | `cancel(taskIds)`                | 批量取消未完成任务，不解释取消原因。     |

Node 的 `waitForResult(taskId, signal)` 只是查询轮询的本地封装，不增加 Python 操作。相同任务可共享查询与文件导入；一个等待者取消等待，不会取消合成，也不影响其他等待者。轮询间隔、退避和有限的通信重试属于客户端内部实现。

任务组不进入接口。调用方把需要同时控制的任务 ID 交给批量方法；后续新提交的任务始终显式携带当前优先级。Conversation 必须协调提交与切换操作：在优先级改变或停止之后才收到 ID 的提交，也要按最新意图调整或取消，不能遗漏在途请求。

两个批量操作都先去重并校验全部编号，然后与领取任务、写入终态串行确定生效顺序。编号未知或过期时整批拒绝，不留下部分修改；已知的运行中或终态任务按各方法约定处理。空数组无副作用。这个原子性由 Python 保证，不能由 Node 逐个调用并假设中间没有新任务开始。

## 任务状态与错误

正常状态为 `queued → running → succeeded / failed`，`queued` 与 `running` 都可进入 `cancelled`。`running` 从任务取得执行槽开始，包括为它加载模型的阶段。公开状态不再细分设备、模型或内部线程状态。

任务只能进入终态一次。完成和取消同时到达时，先在调度器内提交的终态生效：已经成功就保持成功；已经取消就丢弃后来的推理结果。取消后的实际推理可能继续收尾，在它退出之前仍占用执行槽和相关模型。不能因为公开状态已是 `cancelled` 就启动超额推理，也不能卸载仍被使用的模型。

提交前可检测的无效输入通过请求错误报告，不生成 ID；模型加载失败、运行中读取失败、推理失败及输出写入失败形成 `failed` 快照。只有完整文件关闭并发布到最终路径、实际时长已取得后，才允许提交 `succeeded`。半成品或取消后的输出由 Python 清理。

请求错误使用 `{ problem: { code, message, retryable } }`；稳定类别至少包括 `invalid_request`、`task_unavailable` 和 `service_unavailable`。未知、过期及旧进程的 ID 均视为 `task_unavailable`，不能永久轮询。通信故障与 Node 资源导入失败由客户端拒绝 Promise，不能改写 Python 已有的终态；原始堆栈仅用于内部日志。

## 调度与模型资源

每个请求受理时取得单调递增的提交序号。多个提交谁先被 Python 受理，谁就更早；调用方若要求句子严格按原顺序提交，应按顺序等待受理，不依赖并发 HTTP 请求的到达顺序。结果可以乱序完成，演出仍按 guide 顺序播放。

每次出现可用执行槽时，从当前仍在排队的任务中选择数字最小的优先级。0 级优先使用下一个可用执行槽，多个 0 级按提交序号 FIFO；即使需要切换模型，也不能为复用模型而先运行低优先级任务。已经领取并开始模型准备的任务不被后来到达的高优先级任务抢占。

其他同级任务默认也按 FIFO。`max_same_model_overtakes` 大于 0 时，可在相同优先级内有限度地优先使用已经加载的模型；每次越过较早任务，都累计该任务的被超越次数。达到上限的最早任务必须优先，调整优先级不能重置提交序号或被超越次数。这个限制防止模型复用导致同级饥饿；不同优先级之间仍遵守严格优先级，不承诺低优先级在持续高优先级负载下必然执行。

模型复用只能依据合成配置的兼容性，不能只按角色或某一个权重路径判断；参考音频等请求状态不能从上一条任务泄漏。一次推理期间不能切换它正在使用的权重。采用同进程公共模型池及独立推理实例的设计方向：兼容的 BERT、HuBERT、SV 按版本、设备和精度共享，GPT/SoVITS 角色网络、参考缓存、配置、取消和中间状态隔离。不能安全并行共享的实例必须独占；随机状态也不能跨任务污染。不以共享一个可变 TTS 对象代替隔离，也不承诺未经同条件测量的内存下降。模型准备与推理不能同步阻塞处理 HTTP 控制请求的事件循环。

`InferenceConfig` 的设备、精度和模型结构在启动时固定；`SchedulerConfig` 在 Python 启动时装配，并发上限与常驻模型上限独立于任务数量。模型预算包含正在加载、运行和缓存的实例；取消后仍在执行的实例也计算在内。需要切换模型时只卸载空闲实例，没有安全资源时等待释放，不能因此绕过已经确定的更高优先级任务。初版不公开模型加载、预热、卸载、执行槽或热调整预算的方法。

## 结果保留与文件交接

任务 ID 只在本次 Python 运行及保留期内有效，包含不可复用的运行身份。重启不会自动重放旧任务；客户端使旧 ID 失效，Conversation 决定是否另行提交。

排队和运行中的记录不会因结果保留期而过期。进入终态后保留 `result_retention_ms`；保留期内每次成功查询终态，都续期同样时长。过期记录及其临时输出由 Python 回收，已过期的查询不会复活任务。仍在收尾的取消任务，其内部资源必须等实际退出再清理。

Node 导入文件时保持结果有效；导入可能超过保留期时继续查询续期。资源模块必须取得独立于 Python 临时文件寿命的持久副本，不能仅引用原临时路径，也不能移动文件使其他查询者拿到失效路径。同一客户端针对同一任务复用导入过程和成功资源引用。导入失败可在结果有效期内重试，无需重新合成。

Node 为导入结果建立临时资源持有者，保留期与客户端最近一次交付关联。Conversation 在交付后及时建立存档或演出持有的引用；客户端缓存过期或失效时仅释放自身引用，不删除其他持有者使用的音频。Python 只清理自己的临时输出目录，不清理资源模块的持久音频。进程异常退出留下的临时目录由宿主确认旧进程已退出后回收。

## HTTP 映射

下面是待实现的固定操作，不是当前已经存在的路由；内部类型使用 snake_case，Node 客户端负责与 camelCase 数据转换。

| 请求                          | 请求体                                 | 成功响应                    |
| ----------------------------- | -------------------------------------- | --------------------------- |
| `POST /speech/tasks`          | `SpeechRequest`                        | `202`，`{ "task_id": "…" }` |
| `GET /speech/tasks/{task_id}` | 无                                     | `200`，`TaskSnapshot`       |
| `POST /speech/tasks/priority` | `{ "task_ids": ["…"], "priority": 1 }` | `204`                       |
| `POST /speech/tasks/cancel`   | `{ "task_ids": ["…"] }`                | `204`                       |

无效输入返回 `400`，未知或过期 ID 返回 `404`，不可用返回 `503`；已受理任务的合成失败仍通过 `200` 的 `failed` 快照交付。报文校验包括非空任务文本、支持的语言、路径输入、有限正语速、非负整数优先级和停顿时间；Python 的布尔值不能作为整数参数接受。

此协议由本机 Node 客户端访问，Python 的启动、地址与必要的访问凭据由宿主管理，不把本机路径入口直接开放给手机页面。任务提交尚不承诺幂等：受理响应丢失时客户端报告结果不确定，不能自动重交或声称原任务不存在。确定收到 ID 后，查询和相同控制操作可重试；旧优先级请求不得在较新的控制之后重发，客户端需保持冲突控制的发送顺序。
