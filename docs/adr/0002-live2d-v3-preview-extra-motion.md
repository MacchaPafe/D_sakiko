# 使用 LoadExtraMotion 播放 Live2D V3 动作编辑器预览

Live2D V3 动作编辑器预览通过 live2d-py 的 `LoadExtraMotion(group, motion_path)` 动态加载外部 motion 文件，再用 `StartMotion(group, index, ...)` 播放，而不是在 viewer 启动前临时改写 `model3.json` 并加载后删除预览动作组。这样更符合“临时预览外部动作文件”的语义，避免污染模型 JSON，也避免启动时预加载目录下全部 `*.motion3.json`；旧的 `prepare_preview_motion_files` / `prepare_preview_motion_file` / `remove_preview_motion_group` JSON 注入 API 已直接删除，不保留兼容路径。

所有预览外部 motion 会加载到统一的内部 preview group 中。`LoadExtraMotion` 的返回值是新加载 motion 在目标 group 中的 index，应直接传给 `StartMotion`；adapter 需要缓存 motion 文件路径到 index 的对应关系，避免反复点击同一文件时重复追加加载。Python 层 `GetMotionGroups()` 读取的是原始 `model3.json` motion setting，不能用来推断动态加载 motion 的 index。

动作编辑器的队列协议回到 V2 已有的“路径即预览”语义：左侧 motion 文件和右侧动作组条目都解析为 motion 文件路径并发送给渲染进程，由 adapter 内部按 runtime 版本选择 `LoadMotion` 或 `LoadExtraMotion`。`live2d_viewer.py` 中为 JSON 注入预览组服务的 `{"type": "motion_group"}` 分支只在该文件内使用，已随本方案删除。

旧方案中用于“每个文件一个 preview group”和“清理写入 JSON 的 preview group”的 `preview_group_for_motion_file()` / `is_preview_motion_group()` 也已删除；保留统一的内部 `PREVIEW_MOTION_GROUP` 常量供 `LoadExtraMotion` 使用。
