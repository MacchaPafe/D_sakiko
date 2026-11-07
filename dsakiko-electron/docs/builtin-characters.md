# 内置角色资源包与形态元数据

更新日期：2026-11-02。

## 打包与初始化

沿用 electron-builder 的 `extraResources`：`resources/builtin-characters/` 随程序发布，位于 ASAR 外，包含可直接读取的 `manifest.json` 和 `archives/sakiko.tar.gz`、`archives/anon.tar.gz`、`archives/soyo.tar.gz`。按三位角色压缩，祥子包同时包含黑祥、白祥；不再附带原始 `models/`，构建配置也排除该目录。

Electron 在创建窗口后异步调用 `onboarding.prepareStartupPreview()`，与页面加载并行。尚未初始化时，只从祥子包释放 `previewFormId: white` 的模型文件；其余角色与黑祥在预览或正式导入时再释放。已初始化的日常启动不解压。欢迎页渲染与只读资源传输仍待前端接入，本次没有修改前端或 Figma。

统一使用已有 Onboarding 流程：快速开始调用 `onboarding.begin({ path: 'quick' })`；旧版导入后由 `onboarding.prepareOptionalResources()` 补齐缺失角色。模型按需解压后经 `MediaAssets.importAsset` 复制到用户资源库，再通过 `CharacterCatalog.save` 保存角色及资源引用。同 ID 或同名角色保持原样，不覆盖用户修改。不另设手动安装接口。

解压缓存位于 `<cacheRoot>/builtin-characters/<contentVersion>/<characterId>/<formId>/`。先解压到相邻暂存目录，成功后写完成标记并原子启用；同一缓存队列协调预热、导入和回收，失败可重试。缓存命中只检查内容版本和模型入口，不逐文件扫描。新内容版本清理旧缓存。仅供导入的临时副本用完即删；正在预览的副本保留到退出，完成初始化后退出或下次启动清理，避免永久保留双份模型。

内置包不计算或检查逐文件 SHA-256，也不执行安装前的整包完整性扫描。保留已有的清单格式识别、文件读取、资源导入和角色保存约束。远端可选下载资源的校验流程不受影响。

## 当前资源

| 角色 | 形态    | 模型                                      |
| ---- | ------- | ----------------------------------------- |
| 祥子 | black   | `adv_live2d_sakiko_010_live_01`，不戴面具 |
| 祥子 | white   | `adv_live2d_sakiko_010_casual_spring_01`  |
| 爱音 | default | `adv_live2d_anon_002_casual_spring_01`    |
| 素世 | default | `adv_live2d_soyo_004_casual_spring_01`    |

三位角色、四套模型，模型文件约 48 MiB；不包含面具版、头像和语音资源。人设来自旧资源目录的 `name.txt` 与 `character_description.txt`，祥子各形态附加对应黑祥／白祥语气说明。

保留原始模型内容及 `Default` 动作组。欢迎动作筛选、鼠标追踪、只读预览传输及页面按钮接入仍由前端接入阶段完成。

## 清单格式

顶层使用数字 `version: 1`、独立的 `contentVersion` 和 `characters`。每个形态直接提供包内 `directory` 和 `entry`。角色上的 `archive` 指向归档，`expandedBytes` 限制整个角色包允许的解压大小。

```json
{
  "version": 1,
  "contentVersion": "每次打包生成的UUID",
  "characters": [
    {
      "id": "sakiko",
      "name": "祥子",
      "description": "角色共用人设",
      "themeColor": null,
      "defaultFormId": "black",
      "previewFormId": "white",
      "archive": "archives/sakiko.tar.gz",
      "expandedBytes": 29085278,
      "forms": [
        {
          "id": "black",
          "displayName": "祥子",
          "description": "黑祥形态人设",
          "modelName": "演出服",
          "directory": "models/sakiko-black",
          "entry": "adv_live2d_sakiko_010_live_01.model3.json"
        }
      ]
    }
  ]
}
```

上例省略白祥及其他角色，完整清单见资源目录。

- `defaultFormId`：正式默认形态，不依赖形态排列顺序。旧清单省略时仍使用第一个形态，指向不存在形态时由既有角色保存约束拒绝。
- `previewFormId`：仅供欢迎页读取的预览元数据；祥子为 `white`。初始化不将它写入正式角色选择，也不因为预览创建角色。
- 形态的 `displayName`、`description`：提供时使用本形态的值，否则分别回退到角色名和共用人设，兼容旧清单。
- `modelName`：模型展示名，省略时沿用“默认”；模型 ID 继续使用远程实现的 `<formId>-model`。
- `contentVersion`：打包脚本生成的独立内容版本，用于缓存失效；不是哈希，也不改变清单格式版本。开发来源和既有测试仍可读取没有 `archive` 的目录清单。

祥子的清单顺序保持 `black`、`white`，同时明确 `defaultFormId: black`。欢迎页预览白祥与正式默认黑祥相互独立。

## 重新打包

在 Electron 工程目录执行（实际命令写在一行）：

```sh
node scripts/resources/pack-builtin-characters.mjs --models-root /path/to/downloaded-model-folders --characters-root /path/to/legacy/live2d_related --output resources/builtin-characters-next
```

脚本先准备指定四套模型入口及其引用的动作、表情、贴图、物理等文件，再压成三份角色归档；输出目录只保留清单和归档。不携带面具版、来源报告或临时文件。原始内容不改写，不生成哈希清单。完成后发布暂存目录；输出目录已存在时拒绝覆盖。核对新包后再替换正式目录。

也可以直接转换现有目录版资源，保留完整元数据：

```sh
node scripts/resources/pack-builtin-characters.mjs --source .local/builtin-characters-source-20261102 --output .local/builtin-characters-next
```

本次已将正式 `resources/builtin-characters/` 切换到压缩包；原始文件备份在 `.local/builtin-characters-source-20261102/`，不随发布携带。

`resources/builtin-characters/` 在 `.prettierignore` 中排除，避免批量格式化第三方资源。

## 验证

`tests/core/setup.test.js` 在既有初始化与旧版导入测试上增加真实四套 v3 模型能力解析、初始化前不创建角色、重复初始化不增加模型副本、默认形态不受排序影响、独立形态人设及旧格式回退覆盖。

`tests/core/builtin-resources.test.js` 覆盖三份角色归档、白祥优先释放、并发与重启缓存复用、完整初始化、预览文件保留和退出回收、破损包重试、暂存残留恢复、内容版本失效及拒绝覆盖来源。正式切换前还逐一比较了 407 个模型文件的解压结果与原始字节，全部一致。

## 压缩可行性实测（2026-11-02）

以下是最初评估的整包／按形态测量，实际发布已切换为三份角色包，最新测量见后文。

当前 Apple Silicon Mac 上，以 gzip level 6 生成 tar.gz，使用现有 `setup/installers.js` 的 `extractArchive` 解包。每个目标解压到全新的空目录，共测七轮，排除 `.DS_Store`；没有清空操作系统文件缓存，因此不能视为冷启动或 Windows 性能保证。时间包含现有解包器的路径/条目检查与文件写入，不包括 Live2D 初始化、贴图解码和 GPU 上传。

| 范围                 |  原始大小 |    压缩后 | 解压中位数 |   七轮范围 |
| -------------------- | --------: | --------: | ---------: | ---------: |
| 整包                 | 48.21 MiB | 22.64 MiB |     375 ms | 344–702 ms |
| 白祥（默认欢迎预览） | 13.12 MiB |  5.67 MiB |      83 ms |  69–117 ms |
| 黑祥                 | 14.62 MiB |  6.31 MiB |      99 ms |  76–162 ms |
| 爱音                 | 10.54 MiB |  5.59 MiB |      75 ms |  60–100 ms |
| 素世                 |  9.87 MiB |  5.06 MiB |      68 ms |   57–88 ms |

整包约减少 25.57 MiB（53%）。按四个形态分包的总大小与整包接近，却可以独立释放白祥。这是应用内附带资源的体积收益；外层安装/更新归档本身也可能压缩，下载量不能直接按减少 25.57 MiB 计算。正式导入后的资源库仍存放解压文件，长期保留一份完整预览缓存会抵消整体磁盘收益。

正式三份角色归档合计 23,727,590 字节（约 22.63 MiB，不含清单）。从祥子包选择性释放白祥需要读取 gzip 流，因此与独立白祥包的 83 ms 不同：当前 Mac 上七次空缓存启动预热为 119–198 ms，中位数约 153 ms；缓存命中约 0.3–0.4 ms。测量走真实 `prepareStartupPreview()`，不包含窗口加载、贴图解码或 GPU 上传，也不代表 Windows 或操作系统冷缓存性能。

宿主内部可调用 `prepareBuiltinPreview(characterId)` 按需准备其他欢迎角色；该方法返回本机内部路径，不在 HTTP 方法白名单中。前端接入时仍需提供受控只读访问，不把任意磁盘路径暴露给页面。

可复现的本地测量脚本、原始结果和试验归档位于 `.local/builtin-compression-benchmark/`，不随发布包携带。

## 欢迎页渐变调整（2026-11-02）

Figma `D_Sakiko Design / Designs` 的三个欢迎页（祥子、爱音、素世）已统一调整“庭院渐隐 · 暖纸过渡”：透明区保持到宽度 46%，52.2% 处达到 90% 不透明，56% 处完全过渡到暖纸色。以 1440 px 画板计算，过渡带从原来的 35%–72%（533 px）缩短到 46%–56%（144 px）。

保留 `color/stage`（#F4F1EB）变量绑定、渐变方向、角色图层和右侧入口布局；未改主界面的舞台字幕渐变。三个角色页面均已截图检查。
