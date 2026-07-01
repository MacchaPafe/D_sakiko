# 使用 live2d_support 收纳项目自有 Live2D 支持模块

项目会将 Live2D 布局、模型规范化和 runtime 适配等自有支持模块收拢到 `GPT_SoVITS/live2d_support/`，而不是 `GPT_SoVITS/live2d/`，因为应用依赖第三方 `live2d` runtime 包（例如 `live2d.v2cpp` 和 `live2d.v3`），同名包会带来导入遮蔽风险。

迁移时仓库内调用点统一改为显式子模块导入，同时在直接导入 `live2d_support.*` 的入口文件前保留带 guard 的 `script_dir` 插入，以兼容 Windows embedding Python 发布环境不会自动把 `GPT_SoVITS` 加入 import path 的约束。
