# Live2D runtime 窗口重建只统一底层事务

Live2D 单人、小剧场和动作编辑器都需要在 v2/v3 runtime 版本切换时重建 pygame OpenGL 窗口，但三处切换模型时要重置的业务状态不同。我们决定只抽取 `recreate_runtime_window` 来统一 release runtime、删除背景 texture、重启 pygame display、加载目标 runtime、初始化 OpenGL 和重建并绑定背景 texture；版本是否变化、模型释放、音频状态、overlay、caption/icon 和 frame clock 仍由调用方负责。这样共同模块覆盖最容易出错的底层重建顺序，同时避免把不同窗口的业务状态塞进一个过宽接口。
