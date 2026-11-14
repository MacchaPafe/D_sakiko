# 应用图标与云端编译

## 打包与运行时资源

本地 macOS 打包使用 `build/macos-icon/` 中的预编译资源：`mac.icon`、`dmg.icon` 均为 `build/macos-icon/Icon.icns`，`mac.extraResources` 将 `Assets.car` 复制到应用的 `Contents/Resources/`，`mac.extendInfo` 设置 `CFBundleIconName: Icon`。electron-builder 另行设置传统图标的 `CFBundleIconFile: icon.icns`。资源和配置均在签名前写入应用。

本地继续使用 `pnpm build:mac`，图标处理不调用 actool，无需安装完整 Xcode。Windows 程序使用 `build/icon.ico`，Windows/Linux 窗口使用 `resources/icon.png`。`Assets.car` 仅随 macOS 应用发布，现有 Python 和内置角色资源规则保留。

macOS 正式包不会通过 `app.dock.setIcon()` 覆盖系统图标，以便系统选择浅色、深色外观。开发运行（`dev` / `start`）借用的是 Electron 应用包，因此在主进程启动时使用 `build/macos-icon/Dock.png` 设置 Dock 图标。该 PNG 从同次编译的 `Icon.icns` 导出，显示静态浅色外观；系统深浅色变体仍由正式包的 `Assets.car` 提供。Electron 的 `nativeImage` 不能直接读取此 ICNS，因此单独保留 PNG。

## 设计源文件

- `build/app.icon`：通过本机 Icon Composer 创建的可编辑工程，包含三个独立 PNG 前景图层。Default 使用浅色背景和深色 D 主体，Dark 使用深色背景和浅色 D 主体；蓝色竖条和粉色星星共享。Mono 使用系统派生，尚未单独调校。
- `build/icon-source/original/`：两份原始 SVG，供 Figma 使用与后续设计修改。
- `build/icon-source/composer/`、`preview.png`：本地中间导出和预览，不提交版本库，也不是构建依赖。

图层、配色和 Figma 缩放注意事项见 [素材说明](../build/icon-source/README.md)。旧的 `build/icon.icns` 和与窗口 PNG 重复的 `build/icon.png` 已移除。

## GitHub Actions 编译

图标专用工作流位于 fork 的 master：[Compile app icon](https://github.com/yan-xiaoo/D_sakiko/blob/master/.github/workflows/compile_app_icon.yml)。它只在 `yan-xiaoo/D_sakiko` 编译图标，不依赖 Electron 源码、Node.js、pnpm、Python 或角色模型。完整应用 CI 草稿仅保留在本地，不属于当前提交内容。

更新图标的步骤：

1. 在独立的 Icon Composer 中编辑并保存 `build/app.icon`。
2. 将工程同步到 fork 的 `assets/app-icon/app.icon` 并推送 master，或在 **Actions → Compile app icon → Run workflow** 手动运行。
3. 下载该次运行的 Artifact，将 `Assets.car`、`Icon.icns` 和 `assetcatalog_generated_info.plist` 一起替换到 `build/macos-icon/`。
   在 `dsakiko-electron` 目录执行 `sips -s format png build/macos-icon/Icon.icns --out build/macos-icon/Dock.png`，同步生成开发运行的图标。
4. 更新 [预编译资源来源记录](../build/macos-icon/README.md)，并将源工程与新编译文件一起提交。

修改源工程后必须重新编译，原有二进制不会自动更新。云端 Artifact 保留 14 天；打包依赖的编译文件保存在版本库中，不依赖 Artifact 长期可下载。

仅下载 ICNS 无法保留系统深浅色变体，系统还需要 `Assets.car` 与相应的 Info.plist 配置。本机继续编辑工程无需安装完整 Xcode，编译阶段的 Xcode 26+ / actool 由 GitHub 托管环境提供。

## 当前验证范围

2026-11-07 已在本机 Icon Composer 核验并保存 Default、Dark 两种外观。首次[云端编译成功](https://github.com/yan-xiaoo/D_sakiko/actions/runs/42368788113)，本地预编译资源来自该次产物，已检查 Aqua、DarkAqua 外观及三个前景图层。

本地验证了 electron-builder 配置、资源复制、Info.plist 生成及传统图标替换，没有构建整个应用。安装后的 Dock/Finder 外观仍待正式应用打包后验证。

## 官方资料

- [Apple Icon Composer](https://developer.apple.com/icon-composer/)
- [electron-builder 的图标格式与 Xcode 要求](https://www.electron.build/v26/docs/features/icons-and-images/)
- [GitHub 托管 macOS 环境](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
