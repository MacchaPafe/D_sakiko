# 本地推理依赖

`gpt_sovits/` 是从本轮开始时本仓库 `GPT_SoVITS` 工作区复制的推理相关代码独立副本；原型开发基线为 `7265f2a`。上游为 https://github.com/RVC-Boss/GPT-SoVITS 。保留各文件原有来源和许可声明；本工程 `LICENSE` 复制父仓库许可证，AP-BWE 原许可证也一并保留。未复制 Qt、角色管理、全局配置、旧语音队列或训练权重。

运行资源由 `speech/gpt_sovits_resources.py` 的显式文件快照提供，不再使用统一模型根目录或 `DSAKIKO_PRETRAINED_ROOT`、`DSAKIKO_G2PW_ROOT`。BERT/HuBERT 分别装配配置、单文件权重和分词器/特征提取器；G2PW、语言检测、SV、声码器和 LoRA 底模按用途读取各自路径，不通过文件名推断用途。TTS 不再读写独立 YAML 或回退默认模型。英文词典运行缓存仍由 `DSAKIKO_TEXT_CACHE_DIR` 指向独立 worker 目录；G2PW 使用包内已有的静态 `polyphonic.pickle`，不重建安装目录中的缓存。缺少外部模型时直接报错，不自动下载。`utils.py` 保留旧权重反序列化所需类型，将父项目日志调用改为标准 logging；`tools/__init__.py` 固定本地导入边界。第三方代码保留上游风格，新编写的 `speech/` 代码遵守项目 Python 3.9、类型与中文说明要求。

Live2D 的复制来源与许可位于 `src/renderer/public/cubism/core5/`；`live2d.min.js` 和 `performance/live2d-adapter.js` 来自旧 WebUI 的同名运行库及适配文件。后者仅接入底层模型能力，不导入旧页面或运行时。

AR/modules/lr_schedulers.py 的 Matplotlib 导入只在独立绘图演示中执行，正常推理不再通过该模块初始化绘图依赖。第三方 TorchMetrics 仍可能间接初始化 Matplotlib，其 worker 字体缓存由语音运行目录统一回收。
