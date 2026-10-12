# Electron Live2D 桌宠的无焦点与点击穿透

Live2D 桌宠使用独立的透明 `BrowserWindow`。React 和 Pixi 负责显示模型；Electron 主进程负责窗口置顶、禁止取得焦点，以及把模型空白区域的鼠标输入交给下层应用。透明显示、焦点和点击穿透需要分别配置。

本文是后续开发的技术方案。现有 Electron 原型尚未集成桌宠入口；2026-10-06 的独立验证使用 Electron 44.5.1、Pixi 6.5.10、pixi-live2d-display 0.4.0，在 macOS 上加载了真实 Live2D 模型。

## 1 创建不取得焦点的透明窗口

主聊天窗口继续承担输入和设置。桌宠窗口只展示模型并接收鼠标交互，从创建时就禁止取得焦点。

```js
const petWindow = new BrowserWindow({
  width: 480,
  height: 640,
  show: false,
  frame: false,
  transparent: true,
  backgroundColor: '#00000000',
  hasShadow: false,
  resizable: false,
  focusable: false,
  acceptFirstMouse: true,
  alwaysOnTop: true,
  skipTaskbar: true,
  type: process.platform === 'darwin' ? 'panel' : undefined,
  webPreferences: {
    preload: petPreloadPath,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    backgroundThrottling: false
  }
})

petWindow.setAlwaysOnTop(true, 'screen-saver')
petWindow.setIgnoreMouseEvents(true, { forward: true })
petWindow.once('ready-to-show', function showPet() {
  petWindow.showInactive()
})
```

上面的 `petPreloadPath` 是桌宠专用 preload 的绝对路径。`focusable: false` 防止点击时取得键盘焦点；`showInactive()` 防止显示窗口时激活它。macOS 的 `acceptFirstMouse: true` 让未激活窗口中的模型也能接收第一次点击。不要在模型点击回调中调用 `focus()`、普通 `show()` 或激活主窗口。[Electron 窗口 API](https://www.electronjs.org/docs/latest/api/base-window)

如需在 macOS 的其他桌面及全屏应用上展示，可额外使用：

```js
petWindow.setVisibleOnAllWorkspaces(true, {
  visibleOnFullScreen: true,
  skipTransformProcessType: true
})
```

置顶层级需要按产品需求选择。示例沿用验证程序的 `screen-saver` 层级；普通窗口置顶可使用默认的 `floating`。这些设置不能保证覆盖系统安全界面或所有更高层级窗口。全屏行为需要单独回归，Wayland 不支持 Electron 的 `alwaysOnTop`。[窗口层级说明](https://www.electronjs.org/docs/latest/api/base-window#winsetalwaysontopflag-level-relativelevel)

## 2 让 React 和 Pixi 的背景透明

桌宠页面的 `html`、`body`、React 根节点和舞台容器都应使用透明背景，canvas 铺满窗口内容区。Pixi 创建时设置 `backgroundAlpha: 0`，并将 Live2D 模型加载到舞台。

React 中在挂载完成后的 effect 里创建 Pixi，使用 ref 指向舞台容器；卸载时清理 ticker、模型和渲染器。避免在 React 首次提交之前手动把 canvas 追加到根节点，否则后续提交可能移除它。

透明像素只改变显示效果。CSS 的 `pointer-events: none` 只影响页面内的事件命中，无法单独把原生窗口的点击传给其他应用。[透明窗口限制](https://www.electronjs.org/docs/latest/tutorial/custom-window-styles#limitations)

## 3 用渲染后的 alpha 判断是否穿透

不能仅使用模型包围盒或 `model.hitTest()`：包围盒包含轮廓空白，默认 HitAreas 也可能缺失。应读取最终渲染画面的 alpha，包含纹理透明、模型外部和 Cubism 裁剪形成的空白。

实现顺序如下：

1. 渲染器定期生成 alpha 蒙版，通过专用 preload 和 IPC 发给主进程。主进程校验发送者、宽高和数组长度。
2. 主进程轮询 `screen.getCursorScreenPoint()`，减去 `getContentBounds()` 的原点，得到窗口内坐标。
3. 将坐标映射到蒙版：alpha 大于阈值时接收鼠标，否则调用 `setIgnoreMouseEvents(true)` 穿透。
4. 只在命中状态改变时更新原生窗口。穿透后仍继续轮询，确保鼠标重新进入模型时能够恢复交互。

下面是主进程的核心逻辑。`latestMask` 由经过校验的 IPC 更新，蒙版覆盖整个窗口内容区；初始状态为穿透。

```js
/** @typedef {{width: number, height: number, alpha: Uint8Array}} AlphaMask 窗口内容的透明度蒙版。 */
/** @type {AlphaMask | null} */
let latestMask = null
let previousIgnore = true

/**
 * 根据桌面鼠标位置更新原生窗口的穿透状态。
 * @returns {void} 仅在状态改变时调用窗口 API。
 */
function updateClickThrough() {
  if (petWindow.isDestroyed()) return
  let hit = false
  if (latestMask) {
    const bounds = petWindow.getContentBounds()
    const cursor = screen.getCursorScreenPoint()
    const localX = cursor.x - bounds.x
    const localY = cursor.y - bounds.y
    if (localX >= 0 && localX < bounds.width && localY >= 0 && localY < bounds.height) {
      const x = Math.floor((localX * latestMask.width) / bounds.width)
      const y = Math.floor((localY * latestMask.height) / bounds.height)
      hit = latestMask.alpha[y * latestMask.width + x] > 16
    }
  }
  const ignore = !hit
  if (ignore === previousIgnore) return
  previousIgnore = ignore
  petWindow.setIgnoreMouseEvents(ignore, { forward: true })
}
```

`16/255` 是示例阈值，可按模型的半透明边缘调整。`setIgnoreMouseEvents` 切换的是整个原生窗口；此方案通过当前鼠标位置动态决定是否忽略输入。`forward: true` 在 macOS 和 Windows 上转发移动消息；持续恢复命中判断由主进程轮询保证。[鼠标输入 API](https://www.electronjs.org/docs/latest/api/base-window#winsetignoremouseeventsignore-options)

坐标和更新频率需要特别处理：

- `screen` 鼠标坐标是 DIP，蒙版可能是物理像素，需按窗口内容尺寸映射；canvas 不铺满窗口时还需处理偏移。窗口移动、显示器切换或尺寸变化后要重新确认映射。[screen API](https://www.electronjs.org/docs/latest/api/screen)
- Pixi 6 的 `extract.pixels()` 读取屏幕缓冲区时原点在左下角；验证程序使用 `extract.canvas()` 完成上下翻转，再读取 alpha。
- 验证程序每 100 ms 更新蒙版、每 8 ms 检查鼠标。这两个数值是起点，GPU 读回会有开销，快速跨越动画边缘时也会有延迟。
- 若加入拖动，按下后到释放前应保持接收输入，避免拖动途中穿透；模型清空或蒙版失效时应恢复穿透。关闭窗口时清理轮询与 IPC 监听。

## 4 验证与接入位置

验证程序确认了透明截帧、可见像素接收点击、包围盒内空白像素触发穿透，以及自动模式下分别向桌宠和下层窗口交付点击；桌宠焦点事件为 0。Windows、Linux、多屏 DPI、全屏及快速边缘点击仍需各自回归。

接入本工程时，窗口和轮询放在 `src/main/windows`，蒙版发送方法放在专用 preload，渲染与蒙版生成放在 `src/renderer/src/performance`。复用现有演出协调层，保持一个播放所有者，避免新增窗口造成重复音频或重复推进台词。

独立验证的 [主进程](../../.scratch/technical-validation-20261006/pet-main.cjs)、[渲染器](../../.scratch/technical-validation-20261006/pet-renderer.mjs) 和 [结果](../../.scratch/technical-validation-20261006/summary.json) 位于本机 `.scratch/`，该目录不随 Git 分发。
