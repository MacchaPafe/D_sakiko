# 在未发布的 manifest v1 中增加对话上下文

能够作为对话根包的季度世界书必须在 manifest 中提供由 `series_id`、`canon_branch` 和可空 `story_year` 组成的 `conversation_context`，运行时结合已有 `timeline_id` 和固定分集坐标规则解析剧情进度；通用依赖包不作为根包，可以省略这组字段。当前 manifest 尚未向用户形成兼容承诺，因此继续使用 `format_version: 1` 并同步更新构建器、正式开发包和校验，不为开发期字段补充虚增 v2 或旧格式猜测兼容；条目 `schema_version` 的独立冻结规则不受影响。
