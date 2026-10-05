# Python 语音运行时

`speech/server.py` 是仅绑定回环地址的 HTTP 服务，由 Electron 自动选择空闲端口并注入随机访问凭据。`scheduler.py` 独占队列、优先级、执行槽及驻留模型；`worker.py` 为每个模型实例建立独立进程，使用 `vendor/gpt_sovits/` 内的复制代码。

正常使用不需要手动启动服务。在 Electron 设置中填写 Python 解释器绝对路径即可。服务退出后任务 ID 失效，宿主不会自动重新提交。取消已经运行的任务会立即产生取消终态，但底层推理完成前仍占用执行槽。

## 环境

新增运行时代码兼容 Python 3.9。`pyproject.toml` 与 `uv.lock` 独立锁定依赖，支持解析 3.9–3.11；本轮实际验证的是当前机器的 Python 3.11、macOS ARM64、CPU。Python 3.9 的完整模型推理、CUDA/MPS 和 Windows 未做实机验证。

可以复用已有可运行 GPT-SoVITS 的解释器，也可以在本目录创建独立环境：

```sh
uv sync --python 3.11 --frozen
```

系统需要可执行的 `ffmpeg`。还需要提前准备 PyOpenJTalk 日语词典，以及资源根目录中的预训练权重。首次安装 PyOpenJTalk 后，可在准备环境时显式运行一次 `python -c "import pyopenjtalk; pyopenjtalk.g2p('こんにちは')"` 下载所需字典。原型 worker 检查本地字典，缺失时失败，不在推理过程中隐式下载。中文 G2PW 与语言检测模型同样必须预先存在，资源根目录不作为下载目的地。

`uv.lock` 不包含权重、词典数据和系统二进制。新环境还需准备 NLTK/读音库需要的资源；建议首轮先使用现有 Python 环境验证完整流程，再单独验证环境迁移。

## 测试

从 Electron 工程目录执行：

```sh
python3 -m unittest discover -s python/tests -v
node scripts/verify-speech.mjs sakiko anon
```

第一条无需第三方推理包，覆盖调度器状态和预算；第二条读取 `.env.local`，实际启动两个隔离模型并验证输出，会占用 CPU/内存。测试使用临时数据目录，不修改角色资源。

接口设计仍见 [调度接口](speech/README.md)。`speech/interface.py` 保留为设计审查文件，实际入口是 `server.py`；Node 的 `backend/speech/client.js` 负责材料转换、轮询、控制和持久音频导入，不维护第二份调度队列。
