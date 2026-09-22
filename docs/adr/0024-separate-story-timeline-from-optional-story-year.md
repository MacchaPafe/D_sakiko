# 将剧情时间轴与可空剧情学年分离

世界书使用 `timeline_id` 标识可比较的剧情时间轴，并将原有必填 `season_id` 收敛为可空剧情学年；事件顺序与有效区间由时间轴内的位置表达。Story Event 的事件发生学年只记录历史位置，不限制后续回忆；Character Relation State 与 Character Thought 依靠有效区间；只有 Lore 的设定适用学年参与相应筛选。这样旧版 BanG Dream 动画和手游仍可利用三学年信息，而梦限大等年份未知的作品无需虚构学年或被放入旧时间轴。
