# 已验证的 Figma 变量映射

来源：D_Sakiko Design / Designs / Welcome `5046:2048`，2026-11-14。
按变量语义核对欢迎页实际渲染；Figma 的强调色对应 shadcn 的主操作色，不使用模糊名称匹配得到的浅色 hover 背景。原 demo 的色值已随欢迎页替换，首版仅浅色。

| FigmaName | ref |
| --- | --- |
| color/sidebar | var(--card) |
| color/stage | var(--muted) |
| color/surface | var(--background) |
| color/text | var(--foreground) |
| color/muted | var(--muted-foreground) |
| color/accent | var(--primary) |
| color/selected | var(--secondary) |
| color/border | var(--border) |
| color/glass-toolbar | var(--glass-toolbar) |
| color/glass-highlight | var(--glass-highlight) |

欢迎背景来自画板 IMAGE fill，保存为 `src/renderer/src/assets/welcome/background.png`；图标和文字标识分别来自 `5054:968`／`5054:960` 的 SVG 导出，同目录下命名为 `logo.svg`／`wordmark.svg`。角色主体始终由后端短期 URL 加载 Live2D，不使用截图替代。

按用户确认，三个原型占位头像替换为 Python 版 `GPT_SoVITS/assets/char_headprof/` 中的祥子、爱音、素世 PNG，原样复制到 `src/renderer/src/assets/characters/`。这些小图随 Vite 构建进入前端产物，不依赖 Python 安装目录，不需要解压模型，也不改变内置角色归档格式。

欢迎页没有 Figma Motion 动画。鼠标追踪经欢迎页体验后延续至资源选择与下载页，整个引导共用舞台并保留角色；页面参考宽度变化只调整构图。追踪参数为：水平输入限制在 ±0.55、垂直输入限制在 ±0.4，由 SDK 平滑插值；调整集中在 `Live2DPreview.lookAt()`。后续可依据实际观感缩小或取消，无需改变页面和后端接口。

资源选择 `5054:2534` 与下载 `5066:1026` 继续复用上述主题变量。新增 `--resource-inset` 来自角色语音区域的 `#FAF8F4`，`--selected-border` 来自两阶段标识的 `#D1B4BD`，`--success`／`--success-background` 来自已就绪状态的 `#4A7859`／`#E7EFE8`；新增值集中保存在主题定义中。该两页的 Motion 检查覆盖完整，均无动画轨道。

通高内容区按 Figma 使用 40/48 px 内边距、32 px 区块间隔；在小窗口压缩为 24 px，中间滚动而底部操作固定。下载视图按实际任务显示选中项目，不照搬示例中的已就绪 ASR 和示例百分比。角色语音的单项容量只显示自身待下载文件，不包含单列展示的公共引擎；底部总下载量包含所选项目及依赖，始终使用后端去重值。

底部容量固定标签与 112 px 数值区域，数值右对齐、使用等宽数字和两位小数；更新标记预留 14 px，仅核对超过 300 ms 后显示。核对期间保留上次结果，不以加载文案替换整行；失败时数值显示“核对失败”，并禁止按旧估计开始下载，刷新成功后恢复。
