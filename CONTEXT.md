# D_sakiko

本上下文描述 D_sakiko 中角色展示、语音和 Live2D 演出相关的项目语言。

## Language

**动作编辑器预览**:
在 `live2d_viewer.py` 动作编辑器中临时播放某个 motion 文件，用来让用户检查左侧动作文件或右侧动作组条目的实际表现。它不是角色正式动作组配置的一部分，不应要求持久修改 `model3.json`。
_Avoid_: 预览动作组持久化、导入动作

**Live2D 支持模块**:
项目自有的 Live2D 辅助代码，包括布局、模型规范化、runtime 适配、表情策略和动作选择策略。该概念不包含第三方 `live2d` runtime 包本身。
_Avoid_: live2d 包、runtime 包

**项目标准动作组**:
应用内部稳定使用的一组 Live2D motion group id，例如 `happiness`、`IDLE`、`text_generating`、`idle_motion`。模型导入、动作编辑器、普通聊天和小剧场都应围绕这些 id 协作，而不是各自发明动作组命名。
_Avoid_: 下载站点原始动作类别、表情 ID
