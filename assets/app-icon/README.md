# macOS 应用图标

此目录是独立的 Icon Composer 工程和编译入口，不依赖 Electron 源码、Node.js、pnpm、Python 或角色模型。

`app.icon` 已在本机 Icon Composer 创建并核验，包含三个独立 PNG 前景图层。Default 使用浅色背景 `#EEF7FD` 和深色 D 主体；Dark 使用深色背景 `#141E2E` 和浅色 D 主体。蓝色竖条与粉色星星共享，透明镂空保留。前景的玻璃效果与组的高光、半透明和阴影关闭，Mono 使用系统派生。

## 在 GitHub Actions 编译

[Compile app icon](../../.github/workflows/compile_app_icon.yml) 使用 `macos-26` 托管环境中的 Xcode，只编译图标。工作流限定在 `yan-xiaoo/D_sakiko` 运行。

- master 上此目录或工作流的变更会触发编译。
- 也可以在仓库的 **Actions → Compile app icon → Run workflow** 手动运行。
- 下载该次运行的 `app-icon-macos-<commit>` Artifact，保留 14 天。

产物包含 `Assets.car`、`Icon.icns`、`assetcatalog_generated_info.plist` 和可继续编辑的 `app.icon.zip`，不包含应用安装包。

## 本地编译

仅在已安装 Xcode 26+ 的 Mac 上需要运行此命令；本机没有完整 Xcode 时直接使用 Actions 即可：

```sh
bash assets/app-icon/compile.sh /tmp/dsakiko-icon-output
```

继续设计时，在独立的 Icon Composer 中打开 `app.icon` 即可，无需安装完整 Xcode。

## 后续接入应用

`app.icon.zip` 解压后可以作为 electron-builder 的 `mac.icon` 输入，但该方式在构建应用时仍需要 actool。若本机只使用传统 ICNS，直接使用云端生成的 `Icon.icns` 即可。

要使用已经编译的深浅色图标资源，应在应用签名前将 `Assets.car` 放入 `.app/Contents/Resources/`，设置 `CFBundleIconName` 为 `Icon`，并保留传统 ICNS 与相应的 `CFBundleIconFile`。仅替换 ICNS 无法保留系统外观变体；集成时也应避免覆盖应用已有的其他资产目录。完整应用打包不属于此工作流的范围。

参考：[Apple Icon Composer](https://developer.apple.com/icon-composer/)、[electron-builder 图标格式说明](https://www.electron.build/v26/docs/features/icons-and-images/)。
