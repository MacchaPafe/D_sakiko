# 随应用发布的资源

此目录通过 electron-builder 的 `extraResources` 原样复制到安装包外部资源目录。

- `builtin-characters/`：三位预置角色、祥子的两个形态及四套 Live2D v3 模型，使用 Onboarding 的 `version: 1` 清单，不附带文件哈希。初始化流程负责复制到用户资源库；欢迎页预览使用内置只读副本。
- `icon.png`：初始化模板提供的占位图标。

用户存档、可变缓存、语音模型及语音环境不放在这里。内置包格式、重建命令与后端释放流程见 [内置角色资源说明](../docs/builtin-characters.md)。
