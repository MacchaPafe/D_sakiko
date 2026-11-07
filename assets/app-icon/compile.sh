#!/usr/bin/env bash
set -euo pipefail

# 只编译图标资源，输入路径独立于 Electron 源码和调用者的工作目录。
app_icon_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_icon_source="$app_icon_directory/app.icon"
app_icon_output="${1:-$app_icon_directory/../../dist/app-icon}"

if [[ "$(uname -s)" != Darwin ]]; then
  echo '图标编译需要 macOS 和 Xcode 26 或更高版本。' >&2
  exit 1
fi

test -f "$app_icon_source/icon.json"
xcodebuild -version
xcrun actool --version
mkdir -p "$app_icon_output"
app_icon_output="$(cd "$app_icon_output" && pwd)"
app_icon_temporary="$(mktemp -d "${TMPDIR:-/tmp}/dsakiko-icon.XXXXXX")"
trap 'rm -rf "$app_icon_temporary"' EXIT

# 固定编译名称，后续应用应使用 CFBundleIconName=Icon 引用 Assets.car。
cp -R "$app_icon_source" "$app_icon_temporary/Icon.icon"
xcrun actool "$app_icon_temporary/Icon.icon" \
  --compile "$app_icon_output" \
  --output-format human-readable-text \
  --notices --warnings \
  --platform macosx \
  --target-device mac \
  --minimum-deployment-target 26.0 \
  --app-icon Icon \
  --include-all-app-icons \
  --output-partial-info-plist "$app_icon_output/assetcatalog_generated_info.plist"

test -s "$app_icon_output/Assets.car"
test -s "$app_icon_output/Icon.icns"
# 归档原生工程，下载后仍可直接用 Icon Composer 打开并继续编辑。
ditto -c -k --keepParent "$app_icon_source" "$app_icon_output/app.icon.zip"
echo "图标资源已输出到：$app_icon_output"
