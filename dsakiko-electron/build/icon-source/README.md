# D-star 图标素材

`original/` 保存用户提供的浅色、深色原始 SVG，画布为 512×512，viewBox 为 120×120，可直接导入 Figma。缩放时使用 Figma 的 Scale（K）工具，让图形和描边保持同比例。

## 可编辑的 Icon Composer 工程

直接打开 `../app.icon` 继续编辑。三个前景 PNG 已包含在工程的 `Assets/` 中，均使用 1024×1024 透明画布，保留原始位置及 D 主体的透明镂空，无需依赖 Downloads 或中间导出目录。

| 内容     | Default   | Dark      |
| -------- | --------- | --------- |
| 画布背景 | `#EEF7FD` | `#141E2E` |
| D 主体   | `#141E2E` | `#EEF7FD` |
| 蓝色竖条 | `#77BBDD` | `#77BBDD` |
| 粉色星星 | `#F06A8A` | `#F06A8A` |

工程已在本机 Icon Composer 核验 Default 和 Dark。前景关闭 Liquid Glass，组的高光、半透明和阴影关闭。Mono 使用系统派生，尚未单独调校。圆角外轮廓由系统遮罩处理，不重复添加原始 SVG 的底板边线。

`composer/` 和 `preview.png` 是制作时的本地分层导出与预览，已通过 `.gitignore` 排除，不是打包或继续编辑工程的依赖。

Windows 程序使用 `build/icon.ico`，窗口使用 `resources/icon.png`；macOS 使用 `build/macos-icon/` 中的预编译资源。编译与更新步骤见 [应用图标说明](../../docs/app-icons.md)。
