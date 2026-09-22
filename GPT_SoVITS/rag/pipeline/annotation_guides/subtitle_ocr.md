# Stage 0：视频内嵌字幕 OCR 与复核

## 适用范围

本阶段只用于没有字幕轨、但画面内嵌官方中文字幕的视频。已有可靠 ASS 时不需要运行 OCR。

Stage 0 的正式边界是：

```text
视频 → observations JSON → review JSON → 人工处理风险项 → 正式 ASS
```

扫描和聚合阶段不会生成 ASS。正式 ASS 只由复核工作台或 `publish-ocr-subtitles` 从满足发布门槛的
review JSON 生成。

## 第一步：扫描视频

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline extract-video-subtitles \
  --video '/path/to/episode01.mp4' \
  --series-id yume_mita \
  --episode 1 \
  --output-dir GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita
```

默认布局 `bilibili_1080p` 适用于本项目验证过的 1920×1080 Bilibili 官方中文字幕。其他布局应先复制
内置 JSON 档案并调整比例坐标，再通过 `--profile /path/to/profile.json` 使用。

扫描按 500ms 粗采样。程序先识别固定单行和双行区域；置信度达到布局档案中的
`fixed_region_minimum_confidence` 时直接采用，结果缺失或置信度不足时再运行完整文字检测。粗扫聚合后，字幕
起止附近按 100ms 回查；相邻时间点顺序解码，只有时间间隔超过 `boundary_seek_threshold_ms` 才重新 seek，
避免 HEVC 视频反复随机定位。产物记录逐帧候选、来源、置信度、字框、淘汰原因、运行时版本、粗扫和边界阶段
耗时及完整生效配置。视频身份只使用路径、大小、修改时间、时长和分辨率，不计算视频哈希。

中断后继续：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline extract-video-subtitles \
  --video '/path/to/episode01.mp4' \
  --series-id yume_mita \
  --episode 1 \
  --output-dir GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita \
  --resume
```

只重跑聚合：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline extract-video-subtitles \
  --series-id yume_mita \
  --episode 1 \
  --output-dir GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita \
  --aggregate-only
```

若已有 review 包含人工修改，重新聚合会写入独立 `candidate-review.json`，不会覆盖人工成果。

## 第二步：复核字幕数据集

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline review-ocr-subtitles \
  --input 'GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita/yume_mita[01].review.json'
```

事件状态：

- `auto_accepted`：无已知风险，发布前不要求逐条人工确认。
- `accepted`：已经人工确认或修改。
- `pending`：必须处理后才能发布。
- `deleted`：软删除，不进入 ASS，可以恢复。

重点检查固定区域回退、低置信度、单帧字幕、弱投票、多行顺序不稳定和异常字符后缀。工作台只在内存保留
当前附近的有限静态帧；异常证据和按需截图采用受限 JPEG 与磁盘 LRU 缓存，不会把整集帧载入内存。

OP、ED、插入歌歌词若不用于后续标注，可显式多选后批量软删除。不要仅凭固定片头时长自动删除，因为不同版本
视频的广告、前情提要和片尾位置可能不同。

编辑操作先进入内存草稿。使用“保存”执行完整 schema 校验、外部修改检测和原子写入；不要同时打开两个编辑器
修改同一 review 文件。

## 第三步：发布正式 ASS

当 `pending` 为零后，在编辑器点击“发布 ASS”。也可以运行：

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline publish-ocr-subtitles \
  --input 'GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita/yume_mita[01].review.json'
```

发布前会检查正文、时间顺序、事件重叠和集数文件名，再生成临时 ASS 并使用现有字幕 loader 回读。全部通过后
才原子替换 `yume_mita[01].ass`。正式文件只包含 `Dial_CH` 字幕；软删除事件和所有审核信息只留在 JSON。

发布后若再次编辑 review，工作台会提示 ASS 已过期，必须重新发布。

## 第四步：进入 Stage 1

```bash
PYTHONPATH=GPT_SoVITS python -m rag.pipeline prepare-stage1 \
  --subtitle 'GPT_SoVITS/rag/pipeline/data/subtitle_ocr/yume_mita/yume_mita[01].ass' \
  --output GPT_SoVITS/rag/pipeline/data/annotations_stage1/ep01_prepared.json \
  --series-id yume_mita \
  --timeline-id yume_mita_anime
```

动画标题默认从 `yume_mita` 系列配置读取；剧情学年未知时不传 `--story-year`，产物保存为 `null`。梦限大的
五位默认角色候选先验已经按 `series_id` 生效。

## 完成前检查

- observations 显示扫描已经完成，视频身份与当前文件一致；
- review 中没有 `pending`；
- OP/ED 等不参与标注的事件已软删除；
- 正式 ASS 文件名包含正确的 `[01]` 集数；
- ASS 不包含 OCR 审核标签，并能被 `prepare-stage1` 正常读取；
- review 修改后已经重新发布 ASS。
