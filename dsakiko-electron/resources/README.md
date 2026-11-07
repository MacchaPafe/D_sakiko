# 随应用发布的资源

此目录通过 electron-builder 的 `extraResources` 原样复制到安装包外部资源目录。

- `builtin-characters/`：三位预置角色、祥子的两个形态及四套 Live2D v3 模型，按角色保存为三份 tar.gz。使用 Onboarding 的 `version: 1` 清单与独立 `contentVersion`，不附带文件哈希。启动优先解压白祥到缓存；初始化流程按需解压并复制到用户资源库。
- `icon.png`：Windows/Linux 窗口使用的浅色 D-star 默认图标，1024px PNG；与 Windows 打包用的 `build/icon.ico` 使用同一份原始 SVG。macOS 使用 `build/macos-icon/` 的预编译资源，见 [应用图标说明](../docs/app-icons.md)。

用户存档、可变缓存、语音模型及语音环境不放在这里。内置包格式、重建命令与后端释放流程见 [内置角色资源说明](../docs/builtin-characters.md)。
