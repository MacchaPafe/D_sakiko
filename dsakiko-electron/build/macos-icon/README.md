# 预编译 macOS 图标

此目录保存由 GitHub Actions 的 Xcode 26.6 编译生成的图标资源，本地打包直接使用这些文件，不编译 `build/app.icon`，也不调用 actool。

- `Assets.car`：包含 Aqua、DarkAqua 外观及三个前景图层的系统图标资源。
- `Icon.icns`：同次编译生成的传统图标，供应用回退和 DMG 卷图标使用。
- `assetcatalog_generated_info.plist`：编译器输出的参考配置，不复制进应用。

来源：[fork 提交 8def0d2](https://github.com/yan-xiaoo/D_sakiko/commit/8def0d270385b5b816bee39c8e93be0fc2d6caab)、[成功的编译运行](https://github.com/yan-xiaoo/D_sakiko/actions/runs/42368788113)。源工程为该提交中的 `assets/app-icon/app.icon`，已核对其 JSON 和图层 PNG 与本地 `build/app.icon` 一致。

`electron-builder.yml` 仅在 macOS 打包时把 `Assets.car` 复制到 `.app/Contents/Resources/Assets.car`，并设置 `CFBundleIconName: Icon`。传统 ICNS 由 electron-builder 复制为 `icon.icns` 并设置 `CFBundleIconFile`。这些操作均发生在应用签名前；Windows 不携带资产目录。

修改图标后，重新运行 fork 的 **Compile app icon**，下载产物并一起替换本目录的三个编译文件，同时更新来源记录。仅编辑本地 `.icon` 不会改变已经编译的资源。更完整的步骤见 [应用图标说明](../../docs/app-icons.md)。
