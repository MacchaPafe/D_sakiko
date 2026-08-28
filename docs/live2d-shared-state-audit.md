# Live2D 共享行为状态审计（master 基线）

## 基线与范围

- 基线：`master` 的 `cdb3f5d`。
- Pygame 单角色路径是行为事实来源；两个旧 Electron 分支只作下游实现参考。
- 本文不改变既有 runtime/window/OpenGL/Pixi/WebAudio/lip-sync/layout 职责。

## 实际依赖图

```text
dp_local2 / qtUI
  -> is_text_generating_queue, emotion_queue, audio_file_path_queue,
     change_char_queue, char_is_converted_queue, live2d_text_queue
  -> main2 segment/TTS orchestration
  -> live2d_module.Live2DModule.play_live2d
  -> Pygame SDK, mixer, WavHandler, overlay
  -> runtime facts: motion start/finish, audio busy, model ready/failed
```

`motion_complete_value` 是 `not pygame.mixer.music.get_busy()` 的 legacy
projection，而非 SDK motion 完成。上游用它进行分段背压和历史播放 gate，迁移中必须保持该 contract。

## 行为映射

| 原行为 | master 位置 | 输入、状态与事实 | 共享层归属 |
| --- | --- | --- | --- |
| thinking | `live2d_module.py:850-855` | 文本生成队列、think finish、1s/15s timer | 是 |
| emotion/audio segment | `live2d_module.py:933-967` | emotion/audio FIFO、motion start/failed、audio start/end | 是 |
| idle recover/timed idle | `live2d_module.py:865-872` | motion finish、audio busy、2.5s/25s timer | 是 |
| long-audio repeat | `live2d_module.py:343-388` | duration、motion finish、audio busy、2.5s、最多两次 | 是 |
| click/talking/cancel/bye | `live2d_module.py:698-728,874-879,935-952` | UI intent 与 runtime facts | 是 |
| Sakiko black/white/mask | `live2d_module.py:884-931` | conversion intent、variant、mask RNG、V2 capability | 是 |
| model load, V2/V3, layout, FPS, background | `live2d_module.py:738-834` | runtime load result | 下游 mechanics；共享层接收 capability/fact |
| overlay, mixer, lip-sync, eye reopen | `live2d_module.py:215-318,970-994` | runtime execution | 下游 mechanics |

## 已识别的 Electron 可复用资产

- bridge envelope、WebSocket/reconnect、fan-out、local audio server；
- Electron window、透明/hover/resize、Pixi/Cubism、WebAudio/lip-sync；
- renderer command executor 和 motion/audio/model lifecycle fact 回传。

不得复用为权威行为的部分：旧 `Live2DBehaviorController` 的 RNG、priority
arbitration、timer、segment sequencing，以及 `main2.py` 中 Electron 专有业务判断。

## 已知风险与验证要求

## 完成后的运行拓扑

生产启动由 `main2.py` 创建一个 `AuthoritativeLive2DOwner` 和一个
`SharedRendererService`。情感、thinking、控制、cancel、bye、Sakiko
conversion 等 legacy ingress 都先进入 owner intent queue；service 将 owner
决定的 exact command fan-out 到 Pygame command queue 和 Electron Bridge。
Pygame 只加载模型、执行 SDK/窗口/音频/口型和布局 mechanics，并回传
renderer facts；Electron renderer 复用现有 Pixi/WebAudio/lip-sync controller
执行同一 exact command 并回传 facts。`motion_complete_value` 仍由 Pygame
实际 mixer busy 状态投影，保持上游兼容语义。

Pygame 模块不再创建 behavior/scheduler/conversion 实例，也不再读取输入
队列来选择动作；旧 emotion/audio 参数仅作为兼容签名保留。多 renderer
ready facts 绑定到同一 host，command 使用 `target_renderer_ids` fan-out，
因此同一业务事件只经过一次 owner 决策。

1. Pygame 的 SDK random 与旧 Controller 的 Python `randrange` 不能假定等价；共享层须基于 capability 输入给出 exact group/index。
2. Electron `command_failed` 与 Controller 接收的 `motion_failed/audio_failed` 不一致，可能让 segment 永久等待；迁移时统一为带 token/turn/segment 的 failure fact。
3. Renderer ready/capability 不能在 WebSocket 未连接时丢失。
4. 每个切片都需比较输入、命令、runtime facts 与 `motion_complete_value` 边沿；至少覆盖 thinking、emotion、idle、long-audio、cancel/bye、variant、V2/V3 与 failure/fallback。

## 最小风险迁移顺序

1. 建立 Pygame observable trace 与共享 contract 测试。
2. 接入 Pygame thin compatibility boundary：队列转 intent，callback/mixer 转 facts，保留 legacy projection。
3. 迁移 emotion/segment，再迁移 thinking、idle 和 long-audio。
4. 迁移 click/talking/cancel/bye、model variant/mask 与 switch。
5. Electron 只保留 command execution/facts；删除两端重复业务判断。
6. trace 对照后移除 Pygame 内重叠 decision code，补齐最终映射表。
