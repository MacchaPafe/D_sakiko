# Python 语音模块

当前工程不启动 Python，没有接入推理运行依赖。[speech/README.md](speech/README.md) 定义了调度职责、任务状态、通信和文件交接；同目录的 Python 文件只提供类型与接口原型，不是可用的调度实现。

Python 负责完整的语音任务调度、模型加载与复用、资源预算和 GPT-SoVITS 推理。接入推理时，在此维护独立的 pyproject.toml 和依赖锁定文件，代码兼容 Python 3.9。模型权重与虚拟环境放在受管理的外部位置，不复制到 JavaScript 源码目录。

main/processes 负责子进程生命周期；backend/speech 作为 Node 客户端转换输入与结果、传递控制并封装查询等待，不维护调度队列。解释器位置、服务地址及运行配置由宿主装配传入，避免依赖父仓库的相对路径。
