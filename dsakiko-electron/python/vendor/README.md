# 本地推理依赖

`gpt_sovits/` 是从本轮开始时本仓库 `GPT_SoVITS` 工作区复制的推理相关代码独立副本；原型开发基线为 `7265f2a`。上游为 https://github.com/RVC-Boss/GPT-SoVITS 。保留各文件原有来源和许可声明；本工程 `LICENSE` 复制父仓库许可证，AP-BWE 原许可证也一并保留。未复制 Qt、角色管理、全局配置、旧语音队列或训练权重。

适配改动限于运行边界：预训练权重由 `DSAKIKO_PRETRAINED_ROOT` 指定，中文读音模型由 `DSAKIKO_G2PW_ROOT` 指定；英文词典缓存由 `DSAKIKO_TEXT_CACHE_DIR` 指向独立 worker 目录。缺少语言检测/G2PW 模型时不自动写入资源根目录。`utils.py` 保留旧权重反序列化所需类型，将父项目日志调用改为标准 logging；`tools/__init__.py` 固定本地导入边界。第三方代码保留上游风格，新编写的 `speech/` 代码遵守项目 Python 3.9、类型与中文说明要求。

Live2D 的复制来源与许可位于 `src/renderer/public/cubism/core5/`；`live2d.min.js` 和 `performance/live2d-adapter.js` 来自旧 WebUI 的同名运行库及适配文件。后者仅接入底层模型能力，不导入旧页面或运行时。
