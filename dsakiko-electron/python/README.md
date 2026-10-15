# Python 语音运行时

`speech/server.py` 是仅绑定回环地址的 HTTP 服务，由 Electron 自动选择空闲端口并注入随机访问凭据。`scheduler.py` 独占队列、优先级、执行槽及驻留模型；`worker.py` 为每个模型实例建立独立进程，使用 `vendor/gpt_sovits/` 内的复制代码。

正常使用不需要手动启动服务。通过 `DSAKIKO_PYTHON` 指定解释器绝对路径；当前 React 原型设置页尚未接入新接口。服务退出后任务 ID 失效，宿主不会自动重新提交。取消已经运行的任务会立即产生取消终态，但底层推理完成前仍占用执行槽。

## 环境

新增运行时代码兼容 Python 3.9。`pyproject.toml` 与 `uv.lock` 独立锁定依赖，支持解析 3.9–3.11；本轮实际验证的是当前机器的 Python 3.11、macOS ARM64、CPU。Python 3.9 的完整模型推理、CUDA/MPS 和 Windows 未做实机验证。

可以复用已有可运行 GPT-SoVITS 的解释器，也可以在本目录创建独立环境：

```sh
uv sync --python 3.11 --frozen
```

系统需要可执行的 `ffmpeg`。还需要提前准备 PyOpenJTalk 日语词典，以及资源根目录中的预训练权重。首次安装 PyOpenJTalk 后，可在准备环境时显式运行一次 `python -c "import pyopenjtalk; pyopenjtalk.g2p('こんにちは')"` 下载所需字典。worker 检查本地字典，缺失时失败，不在推理过程中隐式下载。中文 G2PW 与语言检测模型同样必须预先存在，资源根目录不作为下载目的地。

`uv.lock` 不包含权重、词典数据和系统二进制。新环境还需准备 NLTK/读音库需要的资源；建议首轮先使用现有 Python 环境验证完整流程，再单独验证环境迁移。

## 资源与临时目录

宿主从 `resourceLibraryRoot/models/GPT_SoVITS/pretrained_models/` 读取基础权重，从相邻的 `GPT_SoVITS/text/G2PWModel/` 读取 G2PW 资源。基础运行资源仍需事先准备，本阶段不下载或自动迁移。角色权重和参考音频通过统一资源模块导入资源库，Python 只接受宿主授权目录内的文件。

语音输出放在 `cacheRoot/speech-temp/`，日志放在 `cacheRoot/logs/`。成功音频导入 `resourceLibraryRoot/audio/` 后再交给业务保存。Python 代码在 `.app` 外层 Resources/python 中发布，运行工作目录位于缓存，启动时关闭字节码写入，避免写入安装包。解释器和第三方依赖仍需外部准备。

## 测试

从 Electron 工程目录执行：

```sh
python3 -m unittest discover -s python/tests -v
```

测试无需第三方推理包，覆盖调度器状态、预算和 HTTP 协议，使用临时数据目录，不修改角色资源。旧 `verify-speech.mjs` 依赖原型角色发现逻辑，已随旧业务后端移除。新的 Node 语音适配回归位于 `tests/core/infrastructure.test.js`，不加载真实权重，不能替代真实推理验收。

接口设计仍见 [调度接口](speech/README.md)。`speech/interface.py` 保留为设计审查文件，实际入口是 `server.py`；Node 的 `backend/speech/python-transport.js` 负责协议转换、控制和持久音频交接，`backend/speech/interface.js` 统一轮询与等待取消，不维护第二份调度队列。
