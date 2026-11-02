# 内置角色资源包与形态元数据

更新日期：2026-11-02。

## 打包与初始化

沿用 electron-builder 的 `extraResources`：`resources/builtin-characters/` 随程序发布，位于 ASAR 外，无额外 ZIP 压缩层。模型可从只读目录加载；欢迎页预览不需要先释放资源。

统一使用已有 Onboarding 流程：快速开始调用 `onboarding.begin({ path: 'quick' })`；旧版导入后由 `onboarding.prepareOptionalResources()` 补齐缺失角色。模型经 `MediaAssets.importAsset` 复制到用户资源库，再通过 `CharacterCatalog.save` 保存角色及资源引用。同 ID 或同名角色保持原样，不覆盖用户修改。不再提供独立的内置角色安装接口。

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

与已有远程实现一致，顶层使用数字 `version: 1` 和 `characters`。每个形态直接提供 `directory` 和 `entry`，不再使用顶层 `models` 或 `bundleModelId` 间接引用。

```json
{
  "version": 1,
  "characters": [
    {
      "id": "sakiko",
      "name": "祥子",
      "description": "角色共用人设",
      "themeColor": null,
      "defaultFormId": "black",
      "previewFormId": "white",
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

祥子的清单顺序保持 `black`、`white`，同时明确 `defaultFormId: black`。欢迎页预览白祥与正式默认黑祥相互独立。

## 重新打包

在 Electron 工程目录执行（实际命令写在一行）：

```sh
node scripts/resources/pack-builtin-characters.mjs --models-root /path/to/downloaded-model-folders --characters-root /path/to/legacy/live2d_related --output resources/builtin-characters-next
```

脚本只复制指定四套模型入口及其引用的动作、表情、贴图、物理等文件，不携带面具版、来源报告或临时文件。原始内容不改写，不生成哈希清单。完成后发布暂存目录；输出目录已存在时拒绝覆盖。核对新包后再替换正式目录。

`resources/builtin-characters/` 在 `.prettierignore` 中排除，避免批量格式化第三方资源。

## 验证

`tests/core/setup.test.js` 在既有初始化与旧版导入测试上增加真实四套 v3 模型能力解析、初始化前不创建角色、重复初始化不增加模型副本、默认形态不受排序影响、独立形态人设及旧格式回退覆盖。
