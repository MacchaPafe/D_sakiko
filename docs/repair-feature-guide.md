# D_sakiko 程序资源修复功能说明

> 如果你只需要生成或发布修复资产，请阅读第 1～10 节。第 11 节介绍客户端内部执行原理，排查客户端问题时再选择性阅读即可。

## 1. 功能概览

程序资源修复用于将用户当前安装版本中的官方程序文件恢复到该版本发布时的状态。客户端会检查受支持文件是否缺失或内容发生变化，只下载并替换异常文件，而不要求用户重新下载完整安装包。

它不是更新功能，也不能用来把旧版本升级为新版本。修复所依据的版本、平台和架构必须与用户当前安装包完全对应；跨版本文件增删、依赖调整和数据迁移仍由更新系统负责。

由于网络 CDN 容量限制，修复范围有意限制为可独立校验、下载和替换的小型程序资源，例如：

- Python 代码、更新器和修复器脚本；
- 启动脚本、版本元数据和小型 JSON 配置；
- 小型界面图片、图标和文本资源；
- profile 明确纳入的其他发布文件。

以下内容不会作为程序资源修复对象：

- Python 运行环境、虚拟环境、动态库和大型依赖；
- 预训练模型、语音模型等大型资源；
- 用户配置、聊天记录、自定义角色和用户 Live2D 资产；
- 日志、缓存、训练输出和其他运行时生成的数据。

修复检查只检查 manifest 列出的文件。它不会因为某个额外文件未出现在 manifest 中就将其删除。

## 2. 角色分工和总体流程

整个流程分为三个角色。实际协作中，一个人可以完成多个部分的工作。

| 角色 | 主要工作 | 主要入口 |
| --- | --- | --- |
| 修复资产制作者 | 从实际发布包选择文件，生成 manifest、objects 和审计报告 | `tools/release/build_repair_manifest.py` |
| 正式发布者 | 复核交付物，使用发布私钥签名并上传到 Cloudflare R2 | `tools/release/upload_repair_assets.py` |
| 最终用户 | 在应用界面中检查并修复当前安装版本 | GUI；内部由 `repair_checker.py`、`repair_launcher.py` 和 `apply_repair.py` 完成 |

主流程如下：

```text
实际发布包
    │
    ▼
生成 manifest、objects、audit-report.md 和交付 ZIP
    │
    ▼
人工阅读审计报告
    │
    ▼
发布者重新校验交付物并为 manifest 签名
    │
    ▼
上传 objects → manifest 签名 → manifest
    │
    ▼
用户按当前版本、平台和架构获取 manifest，检查并修复异常文件
```

与各阶段直接相关的代码如下：

| 阶段 | 相关文件 |
| --- | --- |
| 选择发布文件 | `tools/release/file_selection.py`、`tools/release/repair_asset_profiles.json` |
| 构建交付物 | `tools/release/build_repair_manifest.py` |
| 复核、签名和上传 | `tools/release/upload_repair_assets.py`、`tools/release/sign_patch_asset.py` |
| manifest 格式和路径安全 | `GPT_SoVITS/repair/repair_manifest.py`、`repair_security.py` |
| 客户端检查和下载 | `GPT_SoVITS/repair/repair_checker.py` |
| UI 后台任务 | `GPT_SoVITS/ui_main/threads/repair_controller.py` |
| 启动独立修复进程 | `GPT_SoVITS/repair/repair_launcher.py` |
| 事务替换、备份和回滚 | `tools/apply_repair.py` |

## 3. 为什么拆分为多个阶段

### 3.1 必须以实际发布包为准

源码工作区与用户收到的发布包并不完全相同：构建过程可能生成文件、排除开发文件，或者改变文件权限。因此，manifest 必须从最终发布包目录生成，不能直接把当前源码工作区当成修复基准。

构建工具会以“源码仓库中的 Git 追踪路径”和“实际发布包中存在的文件”为基础进行选择，再应用 profile 的纳入和排除规则。这样既避免把临时产物收入修复范围，也确保摘要来自用户实际安装的文件版本。

### 3.2 资产制作与正式发布分离

`build_repair_manifest.py` 不需要发布私钥或 R2 凭据。协作者可以生成完整的目录或 ZIP，并将它交给掌握发布权限的人。

`upload_repair_assets.py` 不会盲目信任交付物。它会重新解析 manifest，逐个验证 object 的大小和 SHA-256，然后才验签已有签名或使用私钥签名。这样可以缩小私钥暴露范围，也让正式发布前存在独立复核点。

### 3.3 内容与索引分离

实际文件内容以 SHA-256 作为身份，保存为 content-addressed object。同一内容即使被多个路径或版本引用，也只需存储一次；客户端本地缓存和远端存储都可以安全复用它。

manifest 则负责描述某个确定版本、平台和架构应包含哪些文件。发布时先上传 objects，再上传签名，最后上传 manifest。这样客户端只有在全部内容就绪后才会看到新的 manifest。

### 3.4 检查、下载和替换分离

客户端先检查，再让用户确认，然后下载到 staging，最后退出主程序并由独立进程替换文件。这种设计提供了三个安全保证：

- 正式修改之前可以取消；
- 主程序退出后再替换自身代码，避免占用或运行中修改；
- 独立修复器会重新检查现场，失败时可以使用备份回滚。

## 4. 从实际发布包生成修复资产

### 4.1 前置条件

开始前应准备：

1. 已构建完成、可实际交付给用户的发布包目录；
2. 发布包根目录中的 `version.json`；
3. 与该发布包代码对应的源码仓库；
4. 正确的平台 profile，目前提供 `macos-arm64` 和 `windows-x64`。

建议从仓库根目录执行本文命令。以下示例使用 `uv run`；如果已经进入包含项目依赖的 Python 环境，也可以将 `uv run python` 替换为相应的 `python`。

### 4.2 文件选择规则

默认规则位于 `tools/release/repair_asset_profiles.json`，由 `defaults` 和指定平台的 `profile` 合并而成。

| 规则 | 含义 |
| --- | --- |
| `allow` | 允许从 Git 追踪文件中选择的路径模式，例如 Python、JSON 和小型界面资源 |
| `include` | 明确要求纳入的发布文件，可用于构建生成或未被 Git 追踪的必要资源 |
| `exclude` | 一般排除项，例如文档、测试和另一平台的启动脚本 |
| `hard_exclude` | 无论其他规则如何都不能纳入的内容，例如用户数据、模型、运行环境和缓存 |

平台 profile 还会加入平台专属规则。例如 macOS profile 纳入 `.command` 和 `install_brew.sh`、排除 `.bat`；Windows profile 则相反。

修改 profile 时应特别谨慎：`include` 可以强制纳入未追踪文件，但不能绕过 `hard_exclude`。生成后必须检查审计报告中的“强制纳入的未追踪文件”。

### 4.3 基本命令

macOS arm64 示例：

```bash
uv run python tools/release/build_repair_manifest.py \
  --profile macos-arm64 \
  --package-root "/path/to/D_sakiko-macos-arm64" \
  --version 1.2.3
```

Windows x64 示例：

```bash
uv run python tools/release/build_repair_manifest.py \
  --profile windows-x64 \
  --package-root "/path/to/D_sakiko-windows-x64" \
  --version 1.2.3
```

`--version` 必须与发布包内 `version.json` 的 `version` 完全一致，否则构建会终止。

默认输出到：

```text
dist/repair/D_sakiko_<版本>_<profile>_repair_assets/
dist/repair/D_sakiko_<版本>_<profile>_repair_assets.zip
```

### 4.4 输出结构

```text
D_sakiko_1.2.3_macos-arm64_repair_assets/
├── manifest.json
├── audit-report.md
└── objects/
    └── sha256/
        └── ab/
            └── cd/
                └── abcdef...完整的 64 位 SHA-256
```

- `manifest.json`：记录应用、渠道、版本、平台、架构、最低修复客户端版本及所有受管文件；
- `objects/`：按内容摘要存放实际文件，相同摘要只保存一次；
- `audit-report.md`：供资产制作者和发布者人工复核；
- `.zip`：默认生成的自包含协作交付包，内部布局与目录一致。

此阶段不会生成签名；签名由发布工具在权限受控的阶段完成。

### 4.5 参数说明

```text
--profile PROFILE
```

必填。选择 `repair_asset_profiles.json` 中的平台 profile，如 `macos-arm64`。

```text
--package-root PATH
```

必填。实际发布包的根目录，即包含 `version.json` 的目录。

```text
--version VERSION
```

必填。发布版本，必须与发布包中的版本一致。

```text
--source-root PATH
```

可选。用于读取 Git 追踪路径的源码仓库，默认是当前项目仓库根目录。处理另一份源码检出时才需要显式指定。

```text
--profile-file PATH
```

可选。自定义 profile JSON，默认使用 `tools/release/repair_asset_profiles.json`。

```text
--output PATH
```

可选。指定交付目录；默认写入仓库的 `dist/repair/`。ZIP 路径是在完整目录名后追加 `.zip`。

```text
--no-zip
```

可选。不生成自包含 ZIP，只保留交付目录。

```text
--overwrite-output
```

可选。允许删除并重建已存在的输出目录及对应 ZIP。默认遇到同名输出就失败，以免无意覆盖已审核产物。

自定义输出并允许重建的示例：

```bash
uv run python tools/release/build_repair_manifest.py \
  --profile macos-arm64 \
  --package-root "/path/to/D_sakiko-macos-arm64" \
  --version 1.2.3 \
  --output dist/repair/review-1.2.3-macos-arm64 \
  --overwrite-output
```

## 5. 审阅 `audit-report.md`

构建成功并不代表应该立即上传。至少应检查以下内容：

| 报告项目 | 检查重点 |
| --- | --- |
| 应用、版本和平台 | 是否与实际发布包一致 |
| 纳入文件数量和总大小 | 是否与预期量级接近，是否突然大幅变化 |
| 去重后 objects 数量 | 与文件数的差异是否合理 |
| 强制纳入的未追踪文件 | 是否确实是发布必需文件，而非临时构建产物 |
| 规则排除摘要 | 是否有本应修复的文件被规则排除 |
| 发布包缺失的可维护候选 | 是否表示发布包漏打文件，或只是平台差异 |
| 大于等于 1 MiB 的文件 | 是否应继续作为小型修复资源发布 |
| 大小写不敏感路径冲突 | 必须为无；生成器也会对此进行校验 |

还可以抽查 `manifest.json` 中几个关键路径的 SHA-256，并确认 object 文件确实来自本次实际发布包。正式上传工具会执行完整的机器校验，但机器校验不能代替对“这些文件是否应该被纳入”的人工判断。

## 6. 复核、签名并上传修复资产

### 6.1 前置条件

正式发布者需要：

- 构建阶段产生的交付目录或 ZIP；
- Ed25519 发布私钥；
- Cloudflare R2 的 S3 endpoint、bucket 名称和访问凭据；
- 项目的 release 可选依赖，其中包含 `boto3`。

使用 uv 时，可以通过 `--extra release` 加载发布工具依赖：

```bash
uv run --extra release python tools/release/upload_repair_assets.py --help
```

R2 的访问密钥由 boto3 的标准凭据机制读取，例如环境中的 `AWS_ACCESS_KEY_ID` 和 `AWS_SECRET_ACCESS_KEY`。脚本参数只负责指定 endpoint 和 bucket。

### 6.2 先执行 dry-run

推荐先在交付目录上执行本地复核和签名，不连接 R2：

```bash
export UPDATE_ED25519_PRIVATE_KEY='<base64-raw-seed、base64-DER 或 PEM 私钥>'

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets \
  --dry-run
```

也可以直接复核协作者交付的 ZIP：

```bash
uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets.zip \
  --dry-run
```

工具会验证：

- ZIP 条目没有路径穿越、反斜杠、符号链接或固定布局之外的文件；
- manifest schema 和所有路径合法；
- manifest 引用的每个 object 都存在；
- 每个 object 的大小和 SHA-256 与 manifest 一致；
- 已有签名有效，或者新生成的签名能够通过项目公钥验证。

注意：当输入是 ZIP 且其中没有签名时，dry-run 生成的签名只存在于临时解包目录，命令结束后不会写回原 ZIP。正式上传可以再次从同一 ZIP 生成签名并立即上传；若希望保留签名文件，请对交付目录运行命令。

### 6.3 正式上传

```bash
export R2_BUCKET='your-r2-bucket'
export R2_ENDPOINT_URL='https://<account-id>.r2.cloudflarestorage.com'
export AWS_ACCESS_KEY_ID='<r2-access-key-id>'
export AWS_SECRET_ACCESS_KEY='<r2-secret-access-key>'
export UPDATE_ED25519_PRIVATE_KEY='<base64-raw-seed、base64-DER 或 PEM 私钥>'

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets.zip
```

默认远端前缀是 `repair`。脚本会跳过已经存在的内容 object，然后按以下顺序写入：

1. 尚不存在的 objects；
2. `manifest.json.sig` 对应的远端签名；
3. manifest。

最后上传 manifest 是发布完整性的组成部分，不要通过其他工具改变该顺序。

### 6.4 参数说明

```text
--input PATH
```

必填。`build_repair_manifest.py` 生成的交付目录或 ZIP。

```text
--bucket NAME
--endpoint-url URL
```

正式上传必需。也可以分别通过 `R2_BUCKET` 和 `R2_ENDPOINT_URL` 设置。dry-run 不需要它们。

```text
--prefix PREFIX
```

可选。R2 bucket 内的资源根前缀，默认 `repair`。它应与客户端配置的修复 base URL 对应。

```text
--private-key-env NAME
```

可选。指定保存发布私钥的环境变量名，默认 `UPDATE_ED25519_PRIVATE_KEY`。

私钥值支持三种格式：32 字节 raw seed 的 Base64、DER 私钥的 Base64，或未加密的 PEM Ed25519 私钥。

```text
--private-key-file PATH
```

可选。从本地文件读取 Ed25519 私钥，而不是从环境变量读取。例如：

```bash
uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets.zip \
  --private-key-file "/secure/path/update-ed25519-private-key.txt" \
  --dry-run
```

不要将私钥文件放入仓库或交付 ZIP。

```text
--resign-manifest
```

可选。如果输入目录已经包含 `manifest.json.sig`，默认行为是验证并复用该签名。只有明确需要使用当前私钥重新签名时才添加此参数。

```text
--overwrite-manifest
```

可选。远端同版本、平台和架构的 manifest 已存在时，脚本默认拒绝覆盖。只有在确认需要纠正错误发布、并了解已经有客户端可能读取旧 manifest 时才使用。

```text
--dry-run
```

可选。完成本地复核和签名，但不创建 R2 客户端，也不上传任何内容。

## 7. 完整发布命令示范

下面以 `1.2.3` 的 macOS arm64 发布包为例。路径仅为示范，请替换成实际位置。

### 7.1 资产制作者：生成交付物

```bash
uv run python tools/release/build_repair_manifest.py \
  --profile macos-arm64 \
  --package-root "/releases/1.2.3/D_sakiko-macos-arm64" \
  --version 1.2.3
```

### 7.2 资产制作者：阅读审计报告

打开：

```text
dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets/audit-report.md
```

确认版本、平台、纳入范围、未追踪文件和大文件均符合预期后，将整个目录或对应 ZIP 交给正式发布者。

### 7.3 正式发布者：本地复核

```bash
export UPDATE_ED25519_PRIVATE_KEY='<private-key>'

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets.zip \
  --dry-run
```

### 7.4 正式发布者：上传 R2

```bash
export R2_BUCKET='d-sakiko-assets'
export R2_ENDPOINT_URL='https://<account-id>.r2.cloudflarestorage.com'
export AWS_ACCESS_KEY_ID='<access-key-id>'
export AWS_SECRET_ACCESS_KEY='<secret-access-key>'

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_macos-arm64_repair_assets.zip \
  --prefix repair
```

### 7.5 Windows x64 的差异

只需使用 Windows 实际发布包和 `windows-x64` profile，随后对生成的 Windows 交付物重复审计、dry-run 和上传步骤：

```bash
uv run python tools/release/build_repair_manifest.py \
  --profile windows-x64 \
  --package-root "/releases/1.2.3/D_sakiko-windows-x64" \
  --version 1.2.3

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_windows-x64_repair_assets.zip \
  --dry-run

uv run --extra release python tools/release/upload_repair_assets.py \
  --input dist/repair/D_sakiko_1.2.3_windows-x64_repair_assets.zip \
  --prefix repair
```

## 8. 远端布局和版本匹配规则

默认 `--prefix repair` 下的布局为：

```text
repair/
├── manifests/
│   └── D_sakiko/
│       └── stable/
│           └── 1.2.3/
│               ├── macos-arm64.json
│               ├── macos-arm64.json.sig
│               ├── windows-x64.json
│               └── windows-x64.json.sig
└── objects/
    └── sha256/
        └── ab/
            └── cd/
                └── abcdef...完整 SHA-256
```

manifest 的远端路径同时包含：

- `app_id`，当前为 `D_sakiko`；
- channel，当前为 `stable`；
- 精确版本号；
- 平台和架构。

客户端还会检查 manifest 内部身份，不能仅靠伪造 URL 路径让另一版本或平台的 manifest 通过。manifest 的 `min_repair_client_version` 也可以阻止过旧修复组件处理其不理解的格式或规则。

平台必须完全匹配。架构通常也必须匹配，但 manifest 使用 `universal` 时可以被当前平台的具体架构接受。目前内置构建 profile 生成的是 `macos-arm64` 和 `windows-x64`，不是 `universal`。

objects 不含版本字段，因为摘要已经唯一描述内容。相同 object 可以被多个 manifest 安全引用。

## 9. 常见问题与故障排查

### 9.1 “发布包版本与参数版本不一致”

检查 `--package-root/version.json`，确认它确实属于正在发布的版本。不要只为绕过检查而修改 `--version`；应先确认是否选错发布包。

### 9.2 “输出已存在”

默认拒绝覆盖是为了保护已审核产物。可以更换 `--output`，或在确认旧输出不再需要时添加 `--overwrite-output`。

### 9.3 profile 不存在或平台选错

检查 `tools/release/repair_asset_profiles.json` 的 `profiles`。profile 名称是 `macos-arm64`、`windows-x64` 等配置键，不是任意平台字符串。

### 9.4 审计报告出现强制纳入的未追踪文件

逐项确认它们是否为构建生成但发布必需的文件。若是临时文件，应修改 profile 或构建流程，而不是直接上传。

### 9.5 交付物缺少 object、大小不符或 SHA-256 不符

交付目录可能被修改、复制不完整，或者 manifest 与 objects 来自不同构建。重新从未经修改的实际发布包生成整套交付物，不要单独手工补文件。

### 9.6 “缺少发布工具依赖 boto3”

使用 release extra 运行上传工具：

```bash
uv run --extra release python tools/release/upload_repair_assets.py --help
```

### 9.7 找不到发布私钥

确认默认环境变量 `UPDATE_ED25519_PRIVATE_KEY` 已设置，或显式使用 `--private-key-env` / `--private-key-file`。不要把私钥提交到仓库。

### 9.8 “远端 manifest 已存在”

同一版本和平台的 manifest 被视为已发布的不可随意变更索引。先确认是否重复执行了上传、是否选错版本，或远端是否已经存在正确资源。只有明确进行发布纠错时才使用 `--overwrite-manifest`。

已存在的 object 被跳过是正常行为，因为 object key 由内容摘要决定；这与 manifest 覆盖保护不同。

### 9.9 用户提示当前版本不支持修复

如果所有配置的资源源都对该版本 manifest 返回 404，客户端会认为当前版本没有修复资产。确认对应版本、平台和架构的 manifest 与 `.sig` 已上传，且客户端 base URL 与 `--prefix` 指向同一资源根。

### 9.10 用户提示签名、版本或平台不匹配

不要关闭校验。检查是否上传了错误交付物、使用了不匹配的发布私钥，或者 CDN/R2 中的 manifest 和签名不是同一份内容。

### 9.11 修复执行失败

客户端日志位于应用根目录的 `logs/repair/`，最近一次机器可读结果为 `logs/repair/last_repair_result.json`。结果会记录是否执行回滚、回滚是否成功、备份目录和日志路径。客户端内部目录还包括修复 object 缓存、staging、plans 和 backups；具体位置由更新根目录下的 `repair/` 管理。

## 10. 发布检查清单

### 资产制作者

- [ ] 使用的是最终实际发布包，而不是源码工作区或中间构建目录。
- [ ] `--version` 与发布包 `version.json` 完全一致。
- [ ] profile 与目标平台、架构一致。
- [ ] 阅读了完整的 `audit-report.md`。
- [ ] 核实了强制纳入的未追踪文件。
- [ ] 核实了缺失候选和大文件。
- [ ] 没有手工修改生成后的 manifest 或 objects。
- [ ] 将完整目录或自包含 ZIP 交给正式发布者。

### 正式发布者

- [ ] 确认交付物的版本、平台和来源。
- [ ] `--dry-run` 已通过。
- [ ] 发布私钥未进入仓库、日志或交付物。
- [ ] R2 bucket、endpoint 和 prefix 指向正确环境。
- [ ] 已确认远端是否存在同身份 manifest。
- [ ] 正式上传成功，manifest 在 objects 和签名之后发布。
- [ ] macOS 与 Windows 等各平台分别生成和发布了对应资产。
- [ ] 保留了审计报告和本次发布使用的交付物。

---

## 11. 附录：客户端修复原理（选择性阅读）

本节供排查客户端问题或理解安全设计时使用。仅负责生成和发布修复包的协作者可以跳过。

### 11.1 完整性检查

用户从 GUI 发起检查后，后台线程调用 `repair_checker.check_integrity()`：

1. 从当前安装包的 `version.json` 读取版本；
2. 检测当前平台和架构；
3. 根据版本、平台和架构拼接 manifest URL；
4. 从同一个资源源获取 manifest 及其 `.sig`；
5. 验证 Ed25519 签名；
6. 校验 `app_id`、channel、版本、平台、架构和最低修复客户端版本；
7. 对 manifest 中的本地文件逐一计算 SHA-256；
8. 将不存在的文件标为 `missing`，摘要不同的文件标为 `modified`。

检查不会比较文件 mode，也不会处理 manifest 之外的额外文件。

默认修复资源地址由 `DEFAULT_REPAIR_BASE_URLS` 提供。开发和故障切换时可通过逗号分隔的 `DSAKIKO_REPAIR_BASE_URLS` 配置多个 base URL；客户端会按顺序尝试，并要求 manifest 和签名来自同一个源。

### 11.2 下载、缓存、staging 和 repair plan

用户确认修复后，`prepare_repair()` 下载候选文件对应的 objects：

- 下载先写入 `.part`，完成后校验大小和 SHA-256；
- 已校验的 object 会进入本地内容寻址缓存，后续可以复用；
- 本次事务所需文件会复制到独立 staging 目录并再次校验；
- 最后原子写入 repair plan。

repair plan 记录 manifest 身份、本次 staging 目录，以及每个候选文件的：

- 目标路径、SHA-256、大小和 mode；
- 检查时是 `missing` 还是 `modified`；
- 修改文件在检查时的原始 SHA-256；
- 已下载 staging 文件的位置。

记录原始状态是为了防止“检查完成后、真正修复前”目标文件又发生变化。

### 11.3 退出主程序后的事务替换

下载完成后，`repair_launcher.py` 启动 detached 的 `tools/apply_repair.py`，然后主程序退出。独立修复器会：

1. 等待主程序 PID 退出；
2. 获取与更新操作共用的 operation lock；
3. 严格解析 repair plan；
4. 重新检查应用版本、平台、架构、staging 路径和所有文件摘要；
5. 整批预检目标文件现场，发现任何竞态变化则在修改前终止；
6. 备份原文件并逐一替换；
7. 恢复 manifest 指定的权限并复验结果；
8. 失败时回滚已经触达的文件；
9. 写入日志和 `last_repair_result.json`；
10. 成功后按主程序提供的命令重新启动应用。

结果记录与详细日志分离。结果记录只保存状态、计数、回滚结果和相关路径，供主程序下次启动时展示；详细过程保存在修复日志中。

### 11.4 `apply_repair.py` 内部命令

`tools/apply_repair.py` 是客户端内部执行器，不是日常发布命令，也不建议用户手工调用。正常情况下，plan、staging、日志路径、状态路径和重启命令都由客户端生成。

参数如下：

| 参数 | 含义 |
| --- | --- |
| `--app-root PATH` | 必填，应用根目录 |
| `--plan PATH` | 必填，客户端生成的 repair plan |
| `--wait-pid PID` | 可选，开始事务前等待退出的主程序 PID |
| `--restart-command JSON` | 可选，成功后执行的 JSON 命令数组 |
| `--log-file PATH` | 可选，修复日志路径；省略时自动生成 |
| `--status-file PATH` | 可选，修复结果记录路径；省略时使用默认位置 |

仅供理解其调用形式的示例：

```bash
python tools/apply_repair.py \
  --app-root "/path/to/D_sakiko" \
  --plan "/path/to/D_sakiko/.updates/repair/plans/repair_1.2.3_example.json" \
  --wait-pid 12345 \
  --restart-command '["/path/to/D_sakiko/start.command"]' \
  --log-file "/path/to/D_sakiko/logs/repair/repair_example.log" \
  --status-file "/path/to/D_sakiko/logs/repair/last_repair_result.json"
```

手工编写 plan 或替换 staging 文件通常会被严格校验拒绝。调试时也应尽量从正常 GUI 流程生成计划，再观察独立执行器日志。
