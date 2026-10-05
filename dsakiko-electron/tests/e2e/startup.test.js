import { mkdtemp, rm, mkdir, writeFile, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'

const projectRoot = fileURLToPath(new URL('../../', import.meta.url))
let application, temporaryData, server, environment, realEnvironment, page
const errors = []
let requests = 0

beforeAll(async function launchApplication() {
  temporaryData = await mkdtemp(join(tmpdir(), 'dsakiko-electron-test-'))
  const resourceRoot = join(temporaryData, 'resources')
  for (const [id, name] of [
    ['a', '甲'],
    ['b', '乙']
  ]) {
    const folder = join(resourceRoot, 'live2d_related', id)
    await mkdir(folder, { recursive: true })
    await writeFile(join(folder, 'name.txt'), name)
    await writeFile(join(folder, 'character_description.txt'), '友善的测试角色')
  }
  server = createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const input = JSON.parse(Buffer.concat(chunks).toString())
    requests += 1
    const last = input.messages.at(-1).content
    let content = 'malformed JSON'
    if (last.includes('格式校验失败'))
      content = JSON.stringify({ type: 'tool', name: 'get_current_time', arguments: {} })
    if (last.includes('get_current_time 工具结果')) {
      let ids = input.messages[0].content.includes('为两名角色') ? ['a', 'b'] : ['a']
      if (input.messages[0].content.includes('"id":"sakiko"')) ids = ['sakiko', 'anon']
      const longConversation = input.messages.some((message) =>
        message.content.includes('长内容布局回归')
      )
      let lines = ids.map((id) => ({
        speakerId: id,
        text: 'こんにちは。お会いできて嬉しいです。',
        translation: `${id === 'a' ? '甲' : '乙'}：很高兴见到你。`,
        emotion: 'happiness',
        performance: { motion: 'auto', expression: 'auto' }
      }))
      if (longConversation)
        lines = Array.from({ length: 10 }, (_, index) => ({
          speakerId: ids[index % ids.length],
          text: 'こんにちは。今日は一緒にお話しできてうれしいです。'.repeat(3),
          translation:
            `第 ${index + 1} 句：` +
            '今天发生了许多有趣的事，想和你慢慢分享。窗外的风轻轻吹着，我们可以一起聊聊喜欢的音乐。'.repeat(
              3
            ),
          emotion: 'happiness',
          performance: { motion: 'auto', expression: 'auto' }
        }))
      content = JSON.stringify({
        type: 'final',
        lines
      })
    }
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ choices: [{ message: { content } }] }))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  environment = {
    ...process.env,
    DSAKIKO_DATA_DIR: join(temporaryData, 'data'),
    DSAKIKO_RESOURCE_ROOT: resourceRoot
  }
  delete environment.ELECTRON_RUN_AS_NODE
  delete environment.ELECTRON_RENDERER_URL
  application = await electron.launch({
    args: [projectRoot, '--user-data-dir=' + join(temporaryData, 'browser')],
    env: environment
  })
  page = await application.firstWindow()
  page.setDefaultTimeout(6000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.locator('[role="status"][data-status="ready"]').waitFor()
  await page.evaluate(async (baseUrl) => {
    const result = await window.dsakiko.saveSettings({
      baseUrl,
      model: 'fixture',
      voiceEnabled: false
    })
    if (result.problem) throw new Error(result.problem.message)
  }, `http://127.0.0.1:${server.address().port}/v1`)
})

afterEach(async function diagnoseFailure(context) {
  if (context.task.result?.state !== 'fail' || !page || page.isClosed()) return
  console.log('页面错误：', errors)
  console.log('页面内容：', await page.locator('body').innerText())
  await mkdir(join(projectRoot, 'test-results'), { recursive: true })
  await page.screenshot({ path: join(projectRoot, 'test-results/failure.png') })
})

afterAll(async function closeApplication() {
  if (application) await application.close()
  if (server) await new Promise((resolve) => server.close(resolve))
  if (temporaryData) await rm(temporaryData, { recursive: true, force: true })
})

test('真实窗口的隔离、人格、双人对话和后台推进', async function verifyWorkflow() {
  const isolation = await page.evaluate(() => ({
    node: typeof window.require,
    genericIPC: typeof window.dsakiko.invoke
  }))
  expect(isolation).toEqual({ node: 'undefined', genericIPC: 'undefined' })
  await page.getByRole('button', { name: /我的人格/ }).click()
  await page.getByLabel('名称', { exact: true }).fill('测试用户')
  await page.getByLabel('描述', { exact: true }).fill('我是一位喜欢音乐的朋友。')
  await page.getByRole('button', { name: '保存人格' }).click()
  await expect.poll(() => page.getByLabel('编辑人格').inputValue()).not.toBe('')
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '新的对话' }).click()
  await page.locator('.character-option[data-id="a"]').click()
  await page.getByRole('button', { name: '开始对话' }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await page.getByLabel('消息', { exact: true }).fill('保留另一聊天的草稿')
  await page.getByRole('button', { name: '新的对话' }).click()
  await page.locator('.character-option[data-id="a"]').click()
  await page.locator('.character-option[data-id="b"]').click()
  await page.getByLabel('我在对话中的身份').selectOption({ label: '测试用户' })
  await page.getByRole('button', { name: '开始对话' }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await page.getByLabel('消息', { exact: true }).fill('现在几点？一起聊一句吧。')
  await page.getByRole('button', { name: '发送消息' }).click()
  await page.getByRole('button', { name: '停止', exact: true }).waitFor()
  await page.locator('.chat-entry').first().click()
  await expect
    .poll(() => page.getByLabel('消息', { exact: true }).inputValue())
    .toBe('保留另一聊天的草稿')
  await expect.poll(() => page.locator('.activity-dot').count(), { timeout: 15000 }).toBe(0)
  await page.locator('.chat-entry').filter({ hasText: '甲 · 乙' }).click()
  await page.getByText('乙：很高兴见到你。', { exact: true }).waitFor()
  expect(await page.locator('.tool-record').count()).toBe(1)
  expect(await page.locator('.message').count()).toBe(3)
  expect(await page.locator('.persona-badge').innerText()).toBe('测试用户')
  expect(requests).toBe(3)
  expect(errors).toEqual([])
  await mkdir(join(projectRoot, 'test-results'), { recursive: true })
  await page.screenshot({ path: join(projectRoot, 'test-results/startup.png') })
})

test('重启恢复聊天记录和身份，但不重启已结束任务', async function verifyPersistence() {
  await application.close()
  application = await electron.launch({
    args: [projectRoot, '--user-data-dir=' + join(temporaryData, 'browser')],
    env: environment
  })
  page = await application.firstWindow()
  page.setDefaultTimeout(6000)
  await page.locator('[role="status"][data-status="ready"]').waitFor()
  await page.locator('.chat-entry').filter({ hasText: '甲 · 乙' }).click()
  await page.getByText('乙：很高兴见到你。', { exact: true }).waitFor()
  expect(await page.locator('.activity-dot').count()).toBe(0)
  expect(requests).toBe(3)
  expect(await page.locator('.persona-badge').innerText()).toBe('测试用户')
})

test.skipIf(!process.env.DSAKIKO_E2E_RESOURCES)(
  '本机真实双版本 Live2D 资源通过自定义协议加载',
  async function verifyRealModels() {
    await application.close()
    realEnvironment = {
      ...environment,
      DSAKIKO_DATA_DIR: join(temporaryData, 'real-data'),
      DSAKIKO_RESOURCE_ROOT: process.env.DSAKIKO_E2E_RESOURCES
    }
    application = await electron.launch({
      args: [projectRoot, '--user-data-dir=' + join(temporaryData, 'real-browser')],
      env: realEnvironment
    })
    application.process().stderr.on('data', (data) => console.log('宿主日志：', data.toString()))
    page = await application.firstWindow()
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) console.log('渲染日志：', message.text())
    })
    await page.locator('[role="status"][data-status="ready"]').waitFor()
    const bootstrap = await page.evaluate(() => window.dsakiko.getBootstrap())
    expect(bootstrap.value.characters.filter((item) => item.hasModel).length).toBeGreaterThan(1)
    const sakiko = bootstrap.value.characters.find((item) => item.id === 'sakiko')
    const modern = sakiko.models.find((model) => model.version === 'v3' && !model.problem)
    expect(modern).toBeDefined()
    const selected = await page.evaluate(
      (id) => window.dsakiko.setCharacterModel('sakiko', id),
      modern.id
    )
    expect(selected.problem).toBeUndefined()
    await page.getByRole('button', { name: '新的对话' }).click()
    await page.locator('.character-option[data-id="sakiko"]').click()
    await page.locator('.character-option[data-id="anon"]').click()
    await page.getByRole('button', { name: '开始对话' }).click()
    await page.waitForFunction(() => document.querySelectorAll('.stage-name').length === 2)
    await page.locator('.stage-pane[aria-busy="false"]').waitFor()
    expect(await page.locator('.stage-problem').count()).toBe(0)
    expect(errors).toEqual([])
    await page.screenshot({ path: join(projectRoot, 'test-results/live2d.png') })
    const modelPaths = await readFile(join(temporaryData, 'real-data', 'assets.json'), 'utf8')
    expect(modelPaths).toContain('.model3.json')
    expect(modelPaths).toContain('.model.json')
  }
)

test.skipIf(!process.env.DSAKIKO_E2E_RESOURCES)(
  '装扮面板切换双版本模型，重启保留选择并恢复双人舞台',
  async function verifyModelSwitching() {
    await page.getByRole('button', { name: '切换舞台装扮' }).click()
    await page.getByRole('radio', { name: '默认模型', exact: true }).check()
    expect(await page.getByRole('button', { name: '应用装扮' }).isEnabled()).toBe(true)
    await page.screenshot({ path: join(projectRoot, 'test-results/model-switcher.png') })
    await page.getByRole('button', { name: '应用装扮' }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    await page.locator('.stage-pane[aria-busy="false"]').waitFor()
    expect(await page.locator('.stage-problem').count()).toBe(0)
    expect(await page.locator('.stage-name').count()).toBe(2)
    await page.screenshot({ path: join(projectRoot, 'test-results/model-v2.png') })
    await page.getByRole('button', { name: '切换舞台装扮' }).click()
    await page.getByRole('radio', { name: '演出服', exact: true }).check()
    await page.getByRole('button', { name: '应用装扮' }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    await page.locator('.stage-pane[aria-busy="false"]').waitFor()
    expect(await page.locator('.stage-problem').count()).toBe(0)
    expect(await page.locator('.stage-name').filter({ hasText: '演出服' }).count()).toBe(1)
    await page.screenshot({ path: join(projectRoot, 'test-results/model-switched.png') })

    await application.close()
    application = await electron.launch({
      args: [projectRoot, '--user-data-dir=' + join(temporaryData, 'real-browser')],
      env: realEnvironment
    })
    page = await application.firstWindow()
    page.on('pageerror', (error) => errors.push(error.message))
    await page.locator('[role="status"][data-status="ready"]').waitFor()
    await page.locator('.chat-entry').click()
    await page.locator('.stage-name').filter({ hasText: '演出服' }).waitFor()
    await page.locator('.stage-pane[aria-busy="false"]').waitFor()
    expect(await page.locator('.stage-name').filter({ hasText: '演出服' }).count()).toBe(1)
    expect(await page.locator('.stage-problem').count()).toBe(0)
    const bootstrap = await page.evaluate(() => window.dsakiko.getBootstrap())
    const sakiko = bootstrap.value.characters.find((item) => item.id === 'sakiko')
    expect(sakiko.models.find((model) => model.id === sakiko.selectedModelId).label).toBe('演出服')
    expect(errors).toEqual([])
  }
)

test.skipIf(!process.env.DSAKIKO_E2E_RESOURCES || !process.env.DSAKIKO_E2E_SPEECH_PYTHON)(
  '真实声音与双版本模型完成整轮演出',
  async function verifyRealPerformance() {
    await page.evaluate(
      async (options) => {
        const result = await window.dsakiko.saveSettings(options)
        if (result.problem) throw new Error(result.problem.message)
      },
      {
        baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
        model: 'fixture',
        voiceEnabled: true,
        pythonExecutable: process.env.DSAKIKO_E2E_SPEECH_PYTHON,
        maxConcurrent: 2,
        maxResident: 2,
        device: 'cpu'
      }
    )
    // 结构设置更新后，重新选中聊天会创建新的演出运行身份。
    await page.locator('.chat-entry').click()
    await page.getByLabel('消息', { exact: true }).fill('现在几点？一起问候我吧。')
    await page.getByRole('button', { name: '发送消息' }).click()
    await page.getByRole('button', { name: '停止', exact: true }).waitFor()
    await expect.poll(() => page.locator('.activity-dot').count(), { timeout: 120000 }).toBe(0)
    expect(await page.locator('.error-banner').count()).toBe(0)
    const data = await page.evaluate(() => window.dsakiko.getBootstrap())
    const lines = data.value.conversations[0].chat.turns[0].messages.filter(
      (entry) => entry.kind === 'character'
    )
    expect(lines).toHaveLength(2)
    expect(lines.every((line) => line.audio?.durationMs > 0)).toBe(true)
    expect(errors).toEqual([])
    await page.screenshot({ path: join(projectRoot, 'test-results/real-performance.png') })
  },
  150000
)

async function readLayout() {
  return page.evaluate(() => {
    function box(selector) {
      const rect = document.querySelector(selector).getBoundingClientRect()
      return { top: rect.top, bottom: rect.bottom, height: rect.height, width: rect.width }
    }
    const messages = document.querySelector('.messages')
    return {
      viewportHeight: window.innerHeight,
      documentHeight: document.documentElement.scrollHeight,
      stage: box('.stage-pane'),
      canvas: box('.live2d-host canvas'),
      subtitle: document.querySelector('.stage-subtitle') ? box('.stage-subtitle') : null,
      chat: box('.chat-pane'),
      composer: box('.composer'),
      messages: {
        ...box('.messages'),
        scrollHeight: messages.scrollHeight,
        scrollTop: messages.scrollTop
      }
    }
  })
}

function expectContainedLayout(layout) {
  expect(layout.documentHeight).toBeLessThanOrEqual(layout.viewportHeight + 1)
  for (const name of ['stage', 'canvas', 'chat', 'composer', 'messages']) {
    expect(layout[name].top, `${name} 的顶部`).toBeGreaterThanOrEqual(0)
    expect(layout[name].bottom, `${name} 的底部`).toBeLessThanOrEqual(layout.viewportHeight + 1)
  }
  expect(layout.messages.height).toBeGreaterThan(100)
  expect(layout.messages.scrollHeight).toBeGreaterThan(layout.messages.height + 500)
  expect(layout.subtitle.height).toBeLessThan(layout.stage.height / 3)
  expect(layout.subtitle.top).toBeGreaterThanOrEqual(layout.stage.top)
}

test('长内容演出仅滚动消息区，舞台与输入框不随台词增长，缩放窗口后仍完整可见', async function verifyLayout() {
  const bootstrap = await page.evaluate(() => window.dsakiko.getBootstrap())
  const realModels = bootstrap.value.characters.some((item) => item.id === 'sakiko')
  const ids = realModels ? ['sakiko', 'anon'] : ['a', 'b']
  await page.evaluate(
    async ({ characterIds, baseUrl }) => {
      const settings = await window.dsakiko.saveSettings({
        voiceEnabled: false,
        baseUrl,
        model: 'layout-fixture'
      })
      if (settings.problem) throw new Error(settings.problem.message)
      const chat = await window.dsakiko.createChat({ title: '长内容布局回归', characterIds })
      if (chat.problem) throw new Error(chat.problem.message)
    },
    { characterIds: ids, baseUrl: `http://127.0.0.1:${server.address().port}/v1` }
  )
  await page.locator('.chat-entry').filter({ hasText: '长内容布局回归' }).click()
  await page.locator('.stage-name').first().waitFor()
  await page.locator('.stage-pane[aria-busy="false"]').waitFor()
  const before = await readLayout()
  await page
    .getByLabel('消息', { exact: true })
    .fill(
      '长内容布局回归，请进行一段包含多句台词的对话。\n' +
        '先回顾之前的故事，再慢慢讲述今天的新见闻。\n'.repeat(40)
    )
  await page.getByRole('button', { name: '发送消息' }).click()
  await expect.poll(() => page.locator('.message').count()).toBe(2)
  await page.locator('.chat-pane').getByText('正在演出', { exact: true }).waitFor()
  await page.locator('.message-translation').first().waitFor()
  expect(await page.getByText('等待演出…', { exact: true }).count()).toBe(0)
  const generated = await page.evaluate(() => window.dsakiko.getBootstrap())
  const active = generated.value.conversations.find((snapshot) => snapshot.selected)
  expect(active.chat.turns[0].messages.filter((entry) => entry.kind === 'character')).toHaveLength(
    10
  )
  expect(active.display.filter((display) => display.mode === 'pending')).toHaveLength(9)
  const playing = await readLayout()
  await mkdir(join(projectRoot, 'test-results'), { recursive: true })
  await writeFile(
    join(projectRoot, 'test-results/layout-metrics.json'),
    JSON.stringify({ before, playing }, null, 2)
  )
  expectContainedLayout(playing)
  expect(playing.stage.height).toBeCloseTo(before.stage.height, 0)
  expect(playing.canvas.height).toBeCloseTo(before.canvas.height, 0)
  expect(playing.composer.bottom).toBeCloseTo(before.composer.bottom, 0)

  await page.locator('.messages').hover()
  await page.mouse.wheel(0, -1000)
  await expect
    .poll(async () => (await readLayout()).messages.scrollTop)
    .toBeLessThan(playing.messages.scrollTop - 300)
  expect((await readLayout()).stage.height).toBeCloseTo(before.stage.height, 0)
  await page.screenshot({ path: join(projectRoot, 'test-results/long-conversation.png') })

  for (const size of [
    [860, 640],
    [1360, 860]
  ]) {
    await application.evaluate(({ BrowserWindow }, dimensions) => {
      BrowserWindow.getAllWindows()[0].setSize(...dimensions)
    }, size)
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(size[0])
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    )
    expectContainedLayout(await readLayout())
    await page.screenshot({
      path: join(projectRoot, `test-results/long-conversation-${size[0]}.png`)
    })
  }
  expect(await page.locator('.stage-problem').count()).toBe(0)
  expect(errors).toEqual([])
})
