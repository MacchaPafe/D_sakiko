import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, vi } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createServer as createViteServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import wavefile from 'wavefile'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { problemError } from '../../src/shared/problems.js'
const root = fileURLToPath(new URL('../../', import.meta.url))

async function verifySearchSelectScrollbar(page, input) {
  const style = await page.addStyleTag({
    // 让滚动条的点击区不受系统是否自动隐藏滚动条的设置影响。
    content: `
      .search-select-popover::-webkit-scrollbar { width: 16px; }
      .search-select-popover::-webkit-scrollbar-thumb { background: gray; }
    `
  })
  try {
    const popover = page.locator('.search-select-popover:popover-open')
    const selection = popover.getByRole('option', { selected: true })
    const before = await selection.allTextContents()
    const { x, y, scrollbarWidth } = await popover.evaluate((element) => {
      element.scrollTop = 0
      const box = element.getBoundingClientRect()
      const scrollbarWidth = element.offsetWidth - element.clientWidth - element.clientLeft * 2
      return {
        x: box.left + element.clientLeft + element.clientWidth + scrollbarWidth / 2,
        y: box.top + element.clientTop + 8,
        scrollbarWidth
      }
    })
    expect(scrollbarWidth).toBeGreaterThan(0)
    await page.mouse.move(x, y)
    await page.mouse.down()
    try {
      await page.mouse.move(x, y + 60, { steps: 5 })
    } finally {
      await page.mouse.up()
    }
    expect(await input.getAttribute('aria-expanded')).toBe('true')
    expect(await input.evaluate((element) => document.activeElement === element)).toBe(true)
    await expect.poll(() => popover.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
    expect(await selection.allTextContents()).toEqual(before)
  } finally {
    await style.evaluate((element) => element.remove())
  }
}

async function dragStageTools(page, dx, dy, orientation) {
  const handle = page.getByRole('button', { name: '拖动舞台工具栏' })
  const toolbar = page.getByRole('group', { name: '舞台工具' })
  const grip = await handle.boundingBox()
  const x = grip.x + grip.width / 2
  const y = grip.y + grip.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  let preview
  try {
    await page.mouse.move(x + dx, y + dy, { steps: 8 })
    await expect.poll(() => toolbar.getAttribute('data-orientation')).toBe(orientation)
    preview = await handle.boundingBox()
  } finally {
    await page.mouse.up()
  }
  expect(await handle.boundingBox()).toEqual(preview)
  return preview
}

async function dragStageToolsOver(page, surface) {
  const handle = page.getByRole('button', { name: '拖动舞台工具栏' })
  const grip = await handle.boundingBox()
  const area = await surface.boundingBox()
  const point = { x: area.x + 32, y: area.y + area.height / 2 }
  await dragStageTools(
    page,
    point.x - grip.x - grip.width / 2,
    point.y - grip.y - grip.height / 2,
    'horizontal'
  )
  expect(
    await handle.evaluate(
      (element, { x, y }) => element.contains(document.elementFromPoint(x, y)),
      point
    )
  ).toBe(true)
}

async function expectSharedDialog(page, view, shell) {
  await view.waitFor()
  expect(await page.locator('dialog:modal').count()).toBe(1)
  expect(await view.evaluate((active, original) => active === original, shell)).toBe(true)
}

async function verifyResourceDownload(page, backend, settings) {
  const shell = await settings.elementHandle()
  const jobId = 'home-test-asr'
  let job = {
    id: jobId,
    kind: 'download',
    status: 'planned',
    phase: 'planned',
    download: { bytes: 100, totalBytes: 1000, components: [{ id: 'asr.models' }] }
  }
  const handlers = [
    vi.spyOn(backend.setup, 'listJobs').mockImplementation(async () => [job]),
    vi.spyOn(backend.setup, 'createPlan').mockImplementation(async () => job),
    vi.spyOn(backend.setup, 'start').mockImplementation(async () => {
      job = { ...job, status: 'running', phase: 'downloading' }
      return job
    }),
    vi.spyOn(backend.setup, 'observe').mockImplementation(async (id, listener) => {
      expect(id).toBe(jobId)
      listener(job)
      return () => {}
    })
  ]
  try {
    await settings
      .getByRole('article')
      .filter({
        has: page.getByRole('heading', { name: 'Whisper Small', exact: true })
      })
      .getByRole('button', { name: '安装', exact: true })
      .click()
    const confirmation = page.getByRole('dialog', { name: '安装附加资源' })
    await expectSharedDialog(page, confirmation, shell)
    await confirmation.getByText(/预计下载/).waitFor()
    await confirmation.getByRole('button', { name: '开始下载', exact: true }).click()
    const center = page.getByRole('dialog', { name: '下载中心' })
    await expectSharedDialog(page, center, shell)
    await center.getByRole('progressbar', { name: '语音转文字下载进度' }).waitFor()
    expect(
      await center.getByRole('button', { name: '附加资源' }).getAttribute('aria-pressed')
    ).toBe('true')
    await center.getByRole('button', { name: '返回设置' }).click()
    await settings.getByRole('heading', { name: '附加资源下载' }).waitFor()
    await expectSharedDialog(page, settings, shell)
  } finally {
    for (const handler of handlers) handler.mockRestore()
  }
}

async function verifyOutfitDownloads(page, backend, manager, role, form, mode) {
  const shell = await manager.elementHandle()
  const selectedForm = manager.getByRole('group', { name: '形态' }).getByRole('button', {
    pressed: true
  })
  const otherForm = role.forms.find((item) => item.id !== form.id)
  const item = {
    id: 'online-test-outfit',
    version: 'v2',
    characterId: role.bangDreamId,
    name: '线上演出服',
    source: 'bestdori'
  }
  const target = { characterId: role.id, formId: otherForm.id }
  backend.outfits.jobs.set('home-test-pending', {
    id: 'home-test-pending',
    target,
    item,
    status: 'interrupted',
    progress: { completed: 2, total: 5 }
  })
  backend.outfits.jobs.set('home-test-finished', {
    id: 'home-test-finished',
    target,
    item: { ...item, name: '已下载的演出服' },
    status: 'succeeded',
    completedAt: Date.now()
  })
  const catalog = vi.spyOn(backend.outfits, 'catalog').mockResolvedValue({
    affiliation: role.bangDreamId,
    items: [
      item,
      ...Array.from({ length: 18 }, (_, index) => ({
        ...item,
        id: 'outfit-' + index,
        name: '服装 ' + (index + 1)
      }))
    ],
    problems: [{ source: 'modelscope', message: 'ModelScope 测试失败' }]
  })
  const listResources = backend.setup.listJobs.bind(backend.setup)
  const resources = vi.spyOn(backend.setup, 'listJobs').mockImplementation(async () => [
    {
      id: 'home-test-voice',
      kind: 'download',
      status: 'succeeded',
      completedAt: Date.now(),
      download: { components: [{ id: 'voice.sakiko', status: 'ready' }] }
    },
    ...(await listResources())
  ])
  const center = page.getByRole('dialog', { name: '下载中心' })
  try {
    await manager.getByRole('tab', { name: '下载', exact: true }).click()
    await manager.getByText(item.name, { exact: true }).waitFor()
    expect(
      await manager.getByRole('button', { name: 'V2', exact: true }).getAttribute('aria-pressed')
    ).toBe('true')
    const list = manager.locator('.outfit-panel-content')
    const toolbar = manager.locator('.outfit-download-toolbar')
    const toolbarBeforeScroll = await toolbar.boundingBox()
    const scrollTop = await list.evaluate((element) => {
      element.scrollTop = 140
      return element.scrollTop
    })
    expect(scrollTop).toBeGreaterThan(0)
    expect(await toolbar.boundingBox()).toEqual(toolbarBeforeScroll)
    const showDownloads = manager.getByRole('button', { name: '下载任务', exact: true })
    await showDownloads.press('Enter')
    await expectSharedDialog(page, center, shell)
    await center.getByRole('button', { name: '返回服装管理' }).click()
    await expectSharedDialog(page, manager, shell)
    const restoredScroll = await list.evaluate((element) => element.scrollTop)
    expect(Math.abs(restoredScroll - scrollTop)).toBeLessThan(3)
    expect(await showDownloads.evaluate((element) => document.activeElement === element)).toBe(true)

    await manager.getByRole('button', { name: 'V3', exact: true }).click()
    await manager.getByText('V3 服装加载失败').waitFor()
    expect(await manager.getByText('ModelScope 测试失败').count()).toBe(0)
    await showDownloads.click()
    await expectSharedDialog(page, center, shell)
    await center.getByText('2 / 5 个文件', { exact: true }).waitFor()
    expect(
      await center.getByRole('button', { name: 'Live2D 服装' }).getAttribute('aria-pressed')
    ).toBe('true')
    const row = center.getByRole('listitem').filter({ hasText: item.name })
    await row.getByText(/安装到/).waitFor()
    const progress = row.getByRole('progressbar')
    expect(await progress.getAttribute('aria-valuenow')).toBe('40')
    expect(
      await progress
        .locator('[data-slot="progress-indicator"]')
        .evaluate((element) => getComputedStyle(element).backgroundColor)
    ).toBe('rgb(139, 95, 110)')
    if (mode === 'production')
      await page.screenshot({ path: '/tmp/sakiko-download-center.png', scale: 'css' })
    await page.keyboard.press('Escape')
    await center.waitFor({ state: 'hidden' })
    await expectSharedDialog(page, manager, shell)
    expect(
      await manager.getByRole('button', { name: 'V3', exact: true }).getAttribute('aria-pressed')
    ).toBe('true')
    expect(await selectedForm.getAttribute('value')).toBe(form.id)

    await manager.getByRole('tab', { name: '已安装', exact: true }).click()
    await manager.getByRole('button', { name: '下载任务', exact: true }).click()
    await row.getByRole('button', { name: '取消', exact: true }).click()
    await row.waitFor({ state: 'hidden' })
    expect(
      backend.outfits.listJobs(target).find((job) => job.id === 'home-test-pending')?.status
    ).toBe('cancelled')
    await center.getByRole('button', { name: '全部', exact: true }).click()
    await center.getByText('最近完成', { exact: false }).click()
    await center.getByText('祥子语音包', { exact: true }).waitFor()
    await center.getByRole('button', { name: '查看已安装' }).click()
    await center.waitFor({ state: 'hidden' })
    await expectSharedDialog(page, manager, shell)
    await expect.poll(() => selectedForm.getAttribute('value')).toBe(otherForm.id)
  } finally {
    catalog.mockRestore()
    resources.mockRestore()
  }
}

async function verifyDraftOutfit(page, backend, defaultName) {
  const trigger = page.getByRole('button', { name: '切换服装' })
  const wardrobe = page.getByRole('dialog', { name: '服装' })
  const toolbar = page.getByRole('group', { name: '舞台工具' })
  const defaults = wardrobe.getByRole('button', { name: defaultName + ' V3 当前', exact: true })
  const alternate = wardrobe.getByRole('button', { name: '备用服装 V3', exact: true })
  await trigger.click()
  await defaults.waitFor()
  expect(await wardrobe.getByRole('button', { name: '管理服装' }).isEnabled()).toBe(true)
  const stage = await page.locator('.home-stage').boundingBox()
  const bar = await toolbar.boundingBox()
  const bottomGap = stage.y + stage.height - bar.y - bar.height
  await dragStageTools(page, 0, bottomGap - 8 + 12, 'vertical')
  const stopped = await toolbar.boundingBox()
  expect(stage.y + stage.height - stopped.y - stopped.height).toBeCloseTo(8, 0)
  const horizontal = await dragStageTools(page, 0, 104, 'horizontal')
  const up = await dragStageTools(page, 0, -48, 'horizontal')
  const down = await dragStageTools(page, 0, 24, 'horizontal')
  expect(up.y).toBeLessThan(horizontal.y)
  expect(down.y).toBeGreaterThan(up.y)
  const spacing = await toolbar.evaluate((bar) => {
    const centers = Array.from(bar.querySelectorAll(':scope > button'), (button) => {
      const { x, width } = button.getBoundingClientRect()
      return x + width / 2
    })
    return {
      rotate: getComputedStyle(bar.querySelector('.toolbar-grip svg')).rotate,
      gaps: centers.slice(1).map((center, index) => center - centers[index])
    }
  })
  expect(spacing).toEqual({ rotate: '90deg', gaps: [48, 48, 48, 48, 48] })
  await toolbar.screenshot({ path: '/tmp/sakiko-stage-toolbar-horizontal.png' })
  await dragStageToolsOver(page, page.getByRole('textbox', { name: '消息' }))
  const grip = await page.getByRole('button', { name: '拖动舞台工具栏' }).boundingBox()
  await dragStageTools(page, stage.x + stage.width - grip.x - grip.width / 2 - 10, -85, 'vertical')
  const wardrobeBox = await wardrobe.boundingBox()
  const composerBox = await page.locator('.home-composer').boundingBox()
  expect(wardrobeBox.x).toBeLessThan((await toolbar.boundingBox()).x)
  const overlapX = Math.max(wardrobeBox.x, composerBox.x)
  const overlapY = Math.max(wardrobeBox.y, composerBox.y)
  const overlapRight = Math.min(
    wardrobeBox.x + wardrobeBox.width,
    composerBox.x + composerBox.width
  )
  const overlapBottom = Math.min(
    wardrobeBox.y + wardrobeBox.height,
    composerBox.y + composerBox.height
  )
  expect(overlapX).toBeLessThan(overlapRight)
  expect(overlapY).toBeLessThan(overlapBottom)
  expect(
    await wardrobe.evaluate(
      (element, { x, y }) => element.contains(document.elementFromPoint(x, y)),
      {
        x: (overlapX + overlapRight) / 2,
        y: (overlapY + overlapBottom) / 2
      }
    )
  ).toBe(true)
  const modelEntry = '**/api/v1/assets/**/*.model3.json'
  const failModelLoad = (route) => route.abort()
  await page.route(modelEntry, failModelLoad)
  try {
    await alternate.click()
    await wardrobe.getByText('服装加载失败', { exact: true }).waitFor()
    await defaults.waitFor()
  } finally {
    await page.unroute(modelEntry, failModelLoad)
  }
  await wardrobe.getByRole('button', { name: '备用服装 V3 重试', exact: true }).click()
  await wardrobe.getByRole('button', { name: '备用服装 V3 当前', exact: true }).waitFor()
  await page.locator('.home-stage').screenshot({ path: '/tmp/sakiko-stage-wardrobe.png' })
  expect(await backend.conversations.listChats()).toHaveLength(0)
  await page.keyboard.press('Escape')
  await wardrobe.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '新对话', exact: true }).click()
  await trigger.click()
  await defaults.waitFor()
  await alternate.click()
  await wardrobe.getByRole('button', { name: '备用服装 V3 当前', exact: true }).waitFor()
  await page.keyboard.press('Escape')
}

async function verifySidebar(page, application, backend, selected) {
  const { chat } = await backend.store.load(selected)
  const sampleReplies = [
    'あの曲を初めて聴いたとき、窓の外はすっかり暗くなっていました。',
    '大家说下次也要一起练习，等演出结束再去吃点好吃的。',
    'そうですね。少しだけ考えさせてください。',
    '那就约在周六下午吧，我会带上新的谱子。',
    '帰り道、風が気持ちよかったです。',
    '如果明天放晴，我们就去庭院走走吧。'
  ]
  const sampleIds = []
  for (const [index, text] of sampleReplies.entries()) {
    const copy = structuredClone(chat)
    copy.title = `侧栏测试 ${index + 1}`
    copy.turns[0].messages[1].parts[0].lines[0].text = text
    sampleIds.push(await backend.conversations.saveCopy(copy))
  }
  const anonId = await backend.conversations.createChat({
    title: '爱音侧栏测试',
    characterIds: ['anon'],
    mode: 'single-character'
  })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.reload()
  const sakiko = page.getByRole('button', { name: '祥子', exact: true })
  const anon = page.getByRole('button', { name: '爱音', exact: true })
  const preview = page.getByRole('button', { name: '今天也辛苦了，先休息一会儿吧。' })
  const draft = page.getByRole('textbox', { name: '消息' })
  const subtitle = page.locator('.subtitle-text')
  await preview.click()
  await expect.poll(() => subtitle.innerText()).toBe('今天也辛苦了，先休息一会儿吧。')
  await draft.fill('折叠和切换都保留这段草稿')

  const group = page.locator('.home-role-group').filter({ has: sakiko })
  const disclosure = group.locator('.home-role-disclosure')
  await sakiko.press('Enter')
  expect(await sakiko.getAttribute('aria-expanded')).toBe('false')
  expect(await sakiko.evaluate((element) => element.classList.contains('selected'))).toBe(false)
  expect(await sakiko.getAttribute('aria-current')).toBe('true')
  expect(await disclosure.evaluate((element) => element.inert)).toBe(true)
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => document.activeElement.getAttribute('aria-label'))).toBe(
    '编辑角色归属：祥子'
  )
  await expect
    .poll(() => page.evaluate(() => getComputedStyle(document.activeElement).opacity))
    .toBe('1')
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => document.activeElement.textContent)).toBe('爱音')
  await expect.poll(() => disclosure.evaluate((element) => element.offsetHeight)).toBe(0)
  expect(await draft.inputValue()).toBe('折叠和切换都保留这段草稿')
  expect(await subtitle.innerText()).toBe('今天也辛苦了，先休息一会儿吧。')
  await sakiko.click()
  expect(await preview.getAttribute('aria-current')).toBe('page')

  const row = group.locator('.home-chat-row').filter({ hasText: sampleReplies[0] })
  const deleteButton = row.locator('.home-chat-delete')
  const background = () => row.evaluate((element) => getComputedStyle(element).backgroundColor)
  const rest = await background()
  await row.hover()
  await expect.poll(background).not.toBe(rest)
  await expect
    .poll(() => deleteButton.evaluate((element) => getComputedStyle(element).opacity))
    .toBe('1')
  const geometry = await row.evaluate((element) => {
    const [text, remove] = element.children
    const [red, green, blue] = getComputedStyle(remove).color.match(/\d+/g).map(Number)
    return {
      left: parseFloat(getComputedStyle(text).paddingLeft),
      right: element.getBoundingClientRect().right - remove.getBoundingClientRect().right,
      redIcon: red > green && red > blue
    }
  })
  expect(geometry.left).toBeGreaterThanOrEqual(12)
  expect(geometry.right).toBeGreaterThanOrEqual(8)
  expect(geometry.redIcon).toBe(true)
  const navigation = page.locator('.home-navigation')
  const hasOverflow = () =>
    navigation.evaluate((element) => element.scrollWidth > element.clientWidth)
  expect(await hasOverflow()).toBe(false)
  await page.screenshot({
    path: '/tmp/sakiko-sidebar-wide.png',
    scale: 'css',
    animations: 'disabled'
  })

  await application.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].setContentSize(1024, 640)
  })
  await row.hover()
  expect(await hasOverflow()).toBe(false)
  await page.screenshot({
    path: '/tmp/sakiko-sidebar-narrow.png',
    scale: 'css',
    animations: 'disabled'
  })
  await page.evaluate(() => {
    window.roleTransitions = []
    document.querySelector('.home-navigation').addEventListener('transitionrun', (event) => {
      if (event.propertyName === 'grid-template-rows')
        window.roleTransitions.push(event.target.dataset.expanded)
    })
  })
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  const history = page.getByRole('complementary', { name: '聊天记录' })
  const originalReply = history.getByText('今天也辛苦了，先休息一会儿吧。', { exact: true })
  await originalReply.waitFor()
  await anon.scrollIntoViewIfNeeded()
  const oldScroll = await navigation.evaluate((element) => element.scrollTop)
  expect(oldScroll).toBeGreaterThan(0)
  await anon.click()
  expect(await sakiko.getAttribute('aria-expanded')).toBe('false')
  expect(await anon.getAttribute('aria-expanded')).toBe('true')
  expect(await sakiko.evaluate((element) => element.classList.contains('selected'))).toBe(false)
  expect(await anon.evaluate((element) => element.classList.contains('selected'))).toBe(true)
  const editAnon = page.getByRole('button', { name: '编辑角色归属：爱音' })
  const editOpacity = () => editAnon.evaluate((element) => getComputedStyle(element).opacity)
  await anon.hover()
  await expect.poll(editOpacity).toBe('1')
  await page.locator('.home-brand').hover()
  await expect.poll(editOpacity).toBe('0')
  await expect
    .poll(() => page.evaluate(() => window.roleTransitions.sort()))
    .toEqual(['false', 'true'])
  expect(await sakiko.getAttribute('aria-current')).toBe('true')
  expect(await anon.getAttribute('aria-current')).toBeNull()
  expect(await draft.inputValue()).toBe('折叠和切换都保留这段草稿')
  expect(await subtitle.innerText()).toBe('今天也辛苦了，先休息一会儿吧。')
  expect(await originalReply.isVisible()).toBe(true)
  await expect
    .poll(() => navigation.evaluate((element) => element.scrollTop))
    .toBeLessThan(oldScroll)
  const anonGroup = page.locator('.home-role-group').filter({ has: anon })
  await expect
    .poll(() =>
      anonGroup.evaluate((element) => {
        const viewport = element.closest('.home-navigation').getBoundingClientRect()
        const header = element.querySelector('.home-role').getBoundingClientRect()
        const action = element.querySelector('.home-new-chat').getBoundingClientRect()
        return header.top >= viewport.top - 1 && action.bottom <= viewport.bottom + 1
      })
    )
    .toBe(true)

  const anonPreview = page.getByRole('button', { name: '暂无回复', exact: true })
  await anonPreview.click()
  expect(await anon.getAttribute('aria-current')).toBe('true')
  expect(await anonPreview.getAttribute('aria-current')).toBe('page')
  await originalReply.waitFor({ state: 'hidden' })
  await sakiko.click()
  expect(await anon.getAttribute('aria-current')).toBe('true')
  expect(await preview.getAttribute('aria-current')).toBeNull()
  await preview.click()
  expect(await preview.getAttribute('aria-current')).toBe('page')
  expect(await draft.inputValue()).toBe('折叠和切换都保留这段草稿')

  const soyo = page.getByRole('button', { name: '素世', exact: true })
  await soyo.click()
  expect(await sakiko.getAttribute('aria-current')).toBe('true')
  const soyoList = page.locator('.home-role-group').filter({ has: soyo })
  expect(await soyoList.locator('.home-chat-row').count()).toBe(0)
  await soyoList.getByRole('button', { name: '新对话', exact: true }).click()
  expect(await soyo.getAttribute('aria-current')).toBe('true')
  expect(await draft.inputValue()).toBe('')
  expect(await backend.conversations.listChats()).toHaveLength(sampleIds.length + 2)

  await sakiko.click()
  await preview.click()
  await anon.click()
  expect(await anonPreview.getAttribute('aria-current')).toBeNull()

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await anon.click()
  const anonList = anonGroup.locator('.home-role-disclosure')
  expect(await anonList.evaluate((element) => getComputedStyle(element).transitionDuration)).toBe(
    '0s, 0s'
  )
  expect(await anonList.evaluate((element) => element.offsetHeight)).toBe(0)
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  expect(
    await page
      .locator('.history-panel')
      .evaluate((element) => getComputedStyle(element).animationName)
  ).toBe('none')
  await anon.click()
  expect(await anon.getAttribute('aria-expanded')).toBe('true')

  for (const id of [...sampleIds, anonId]) await backend.conversations.deleteChat(id)
  await application.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].setSize(1440, 1024)
  })
  await page.reload()
  await expect.poll(() => preview.getAttribute('aria-current')).toBe('page')
  expect(await sakiko.getAttribute('aria-expanded')).toBe('true')
  expect(await anon.getAttribute('aria-expanded')).toBe('false')
}

async function verifyHistory(page, application, backend, selected) {
  const wav = new wavefile.WaveFile()
  wav.fromScratch(1, 16000, '16', new Int16Array(48000))
  const { asset } = await backend.assets.importAsset(
    { kind: 'bytes', name: 'history.wav', mediaType: 'audio/wav', bytes: wav.toBuffer() },
    'test:history',
    'audio'
  )
  const { chat } = await backend.store.load(selected)
  const [question, reply] = chat.turns[0].messages
  question.content.text =
    '祥子，你还记得当初在排练室里第一次听到那首曲子时是什么感觉吗？后来你们又做了哪些改动？'
  const lines = reply.parts[0].lines
  const last = { ...lines[0] }
  Object.assign(lines[0], {
    text: 'あの曲を初めて聴いたとき、窓の外はもうすっかり暗くなっていました。でも、誰も帰ろうとしなかったんです。',
    translation: '第一次听到那首曲子的时候，窗外已经完全黑了，但谁也没有打算回家。',
    audio: { asset, durationMs: 3000 }
  })
  lines.push({ ...lines[0], id: 'another-voiced-line', text: 'そうですね。', translation: null })
  lines.push({ ...last, id: 'last-unvoiced-line' })
  const copyId = await backend.conversations.saveCopy(chat)
  await backend.assets.releaseOwner('test:history')
  await page.reload()
  const chats = page.locator('.home-chat-row > button:first-child')
  await expect.poll(() => chats.count()).toBe(2)
  await page.locator('.home-chat-row > button:first-child:not([aria-current])').click()
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  const longUser = page.locator('.history-user .history-bubble')
  const replies = page.locator('.history-assistant')
  await replies.nth(2).waitFor()
  const longReply = replies.first().locator('.history-bubble')
  const shortReply = replies.nth(1).locator('.history-bubble')
  const resize = page.getByRole('button', { name: '调整记录框宽度' })
  const panel = page.locator('.history-panel')
  const narrowUser = await longUser.boundingBox()
  const narrowReply = await longReply.boundingBox()
  const narrowShort = await shortReply.boundingBox()
  for (let i = 0; i < 14; i += 1) await resize.press('ArrowLeft')
  expect((await longUser.boundingBox()).width).toBeGreaterThan(narrowUser.width + 90)
  expect((await longReply.boundingBox()).width).toBeGreaterThan(narrowReply.width + 90)
  expect((await longReply.boundingBox()).height).toBeLessThan(narrowReply.height)
  expect((await shortReply.boundingBox()).width).toBeCloseTo(narrowShort.width, 0)
  expect(await longReply.locator(':scope > .message-text').innerText()).toBe(lines[0].text)
  expect(await longReply.locator('.history-translation').innerText()).toBe(lines[0].translation)
  expect(await replies.nth(2).locator('.history-translation').count()).toBe(0)
  expect(await panel.getByRole('button', { name: '播放语音' }).count()).toBe(2)
  expect(await replies.nth(2).getByRole('button', { name: '播放语音' }).count()).toBe(0)

  await page.evaluate(() => {
    window.historyAudio = []
    const play = HTMLAudioElement.prototype.play
    HTMLAudioElement.prototype.play = async function () {
      await play.call(this)
      window.historyAudio.push(this)
    }
  })
  const subtitle = page.locator('.subtitle-text')
  const subtitleRegion = page.getByRole('region', { name: '当前台词' })
  const stageStop = subtitleRegion.getByRole('button', { name: '停止本轮回放' })
  await expect.poll(() => subtitle.innerText()).toBe(last.text)
  await replies.first().getByRole('button', { name: '播放语音' }).click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(1)
  expect(await subtitle.innerText()).toBe(last.text)
  await replies.nth(1).getByRole('button', { name: '播放语音' }).click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(2)
  expect(await page.evaluate(() => window.historyAudio[0].paused)).toBe(true)
  await panel.getByRole('button', { name: '停止语音' }).click()
  expect(await page.evaluate(() => window.historyAudio[1].paused)).toBe(true)
  await replies.first().getByRole('button', { name: '播放语音' }).click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(3)
  await expect
    .poll(() => panel.getByRole('button', { name: '播放语音' }).count(), { timeout: 6000 })
    .toBe(2)
  expect(await subtitle.innerText()).toBe(last.text)

  const wide = await panel.boundingBox()
  await replies.first().getByRole('button', { name: '播放语音' }).click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(4)
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  await expect.poll(() => page.evaluate(() => window.historyAudio[3].paused)).toBe(true)
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  const replay = panel.getByRole('button', { name: '回放本轮回复' })
  await replay.click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(5)
  await expect.poll(() => subtitle.innerText()).toBe(lines[0].text)
  await expect
    .poll(() => page.evaluate(() => window.historyAudio.length), { timeout: 6000 })
    .toBe(6)
  await expect.poll(() => subtitle.innerText()).toBe(lines[1].text)
  await stageStop.waitFor()
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  await stageStop.waitFor()
  expect(await page.evaluate(() => window.historyAudio[5].paused)).toBe(false)
  await expect.poll(() => subtitle.innerText(), { timeout: 6000 }).toBe(last.text)
  await stageStop.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  await replay.waitFor()
  expect(await page.evaluate(() => window.historyAudio.length)).toBe(6)
  await page.mouse.move(1, 1)
  await panel.locator('.history-scroll').evaluate((scroll) => {
    scroll.scrollTop = 0
  })
  await page.screenshot({ path: '/tmp/sakiko-history-wide.png', scale: 'css' })
  await application.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].setContentSize(1024, 640)
  })
  for (let i = 0; i < 16; i += 1) await resize.press('ArrowRight')
  await resize.blur()
  await panel.locator('.history-scroll').evaluate((scroll) => {
    scroll.scrollTop = 0
  })
  await page.screenshot({ path: '/tmp/sakiko-history-narrow.png', scale: 'css' })
  for (let i = 0; i < 8; i += 1) await resize.press('ArrowRight')
  expect((await panel.boundingBox()).width).toBeLessThan(wide.width - 200)
  expect((await longUser.boundingBox()).width).toBeLessThan(narrowUser.width)
  expect((await longReply.boundingBox()).width).toBeLessThan(narrowReply.width)
  expect((await shortReply.boundingBox()).width).toBeCloseTo(narrowShort.width, 0)
  expect(
    await panel
      .locator('.history-scroll')
      .evaluate((scroll) => scroll.scrollWidth <= scroll.clientWidth)
  ).toBe(true)
  const replayLine = subtitleRegion.getByRole('button', { name: '回放当句对话' })
  const stopLine = subtitleRegion.getByRole('button', { name: '停止当句回放' })
  const spoken = subtitle.locator('.spoken')
  const lastLength = Array.from(last.text).length
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  expect(await replayLine.locator('svg.lucide-volume-2').count()).toBe(1)
  expect(await replayLine.textContent()).toBe('')
  expect(await replayLine.getAttribute('title')).toBeNull()
  await replayLine.click()
  await stopLine.waitFor()
  expect(await stopLine.locator('svg.lucide-square').count()).toBe(1)
  expect(await stopLine.textContent()).toBe('')
  expect(await stopLine.getAttribute('title')).toBeNull()
  await expect.poll(() => spoken.count()).toBeLessThan(lastLength)
  expect(await page.evaluate(() => window.historyAudio.length)).toBe(6)
  await stopLine.click()
  await expect.poll(() => spoken.count()).toBe(lastLength)

  await page.getByRole('button', { name: '展开聊天记录' }).click()
  await replay.click()
  await expect.poll(() => page.evaluate(() => window.historyAudio.length)).toBe(7)
  await expect.poll(() => subtitle.innerText()).toBe(lines[0].text)
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  expect(
    await page
      .locator('.home-composer')
      .getByRole('button', { name: /停止.*回放/ })
      .count()
  ).toBe(0)
  expect(await stageStop.locator('svg.lucide-square').count()).toBe(1)
  expect(await stageStop.textContent()).toBe('')
  expect(await stageStop.getAttribute('title')).toBeNull()
  await stageStop.click()
  await expect.poll(() => subtitle.innerText()).toBe(last.text)
  expect(await page.evaluate(() => window.historyAudio[6].paused)).toBe(true)
  expect(await page.evaluate(() => window.historyAudio.length)).toBe(7)
  await replayLine.waitFor()
  await backend.conversations.deleteChat(copyId)
  await application.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0].setSize(1440, 1024)
  })
  await page.reload()
  await expect.poll(() => page.locator('.home-chat-row').count()).toBe(1)
}

async function cancelTheatreFilePicker(page, dialog) {
  const session = await page.context().newCDPSession(page)
  try {
    // 让 Chromium 取消这次系统选取，验证浏览器发出的真实文件输入事件。
    await session.send('Page.setInterceptFileChooserDialog', { enabled: true, cancel: true })
    const input = dialog.locator('input[type=file]')
    await Promise.all([
      input.evaluate(
        (element) =>
          new Promise((resolve) =>
            element.addEventListener('cancel', () => resolve(true), { once: true })
          )
      ),
      dialog.getByRole('button', { name: '添加参考文件' }).click()
    ])
    expect(await dialog.isVisible()).toBe(true)
    expect(await dialog.locator('.attachment-chip span').innerText()).toBe('参考.txt')
  } finally {
    await session.detach()
  }
}

/** 使用隔离配置与本地测试 Agent，验证正式舞台、角色选择和成场回放。 */
async function verifyTheatre(page, backend, agent) {
  await page.getByRole('tab', { name: '小剧场', exact: true }).click()
  await page.locator('.theatre-empty-card').waitFor()
  await page.getByRole('button', { name: '新建小剧场' }).click()
  const setup = page.getByRole('dialog', { name: '新建小剧场' })
  const detailToggle = setup.getByRole('button', { name: /角色细节/ })
  expect(await detailToggle.getAttribute('aria-expanded')).toBe('false')
  await detailToggle.click()
  expect(await detailToggle.getAttribute('aria-expanded')).toBe('true')
  const detailGrid = setup.locator('.theatre-details-grid')
  await detailGrid.waitFor()
  expect((await detailGrid.boundingBox()).height).toBeGreaterThan(80)
  await setup
    .locator('.theatre-details-chevron')
    .evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)))
  const symmetry = await setup.evaluate((dialog) => {
    const form = dialog.querySelector('.theatre-form').getBoundingClientRect()
    const cards = Array.from(dialog.querySelectorAll('.theatre-actor-card'), (el) =>
      el.getBoundingClientRect()
    )
    return {
      leftGap: cards[0].left - form.left,
      rightGap: form.right - cards[1].right,
      sameRow: Math.abs(cards[0].top - cards[1].top) < 2
    }
  })
  expect(symmetry.sameRow).toBe(true)
  expect(Math.abs(symmetry.leftGap - symmetry.rightGap)).toBeLessThan(3)
  await page.screenshot({ path: '/tmp/sakiko-theatre-setup.png' })
  await setup
    .locator('input[type=file]')
    .setInputFiles({ name: '参考.txt', mimeType: 'text/plain', buffer: Buffer.from('回家') })
  await expect.poll(() => setup.locator('.attachment-chip span').innerText()).toBe('参考.txt')
  await setup.getByLabel('情境（可选）').fill('排练结束后一起回家')
  await cancelTheatreFilePicker(page, setup)
  expect(await setup.getByLabel('情境（可选）').inputValue()).toBe('排练结束后一起回家')
  await setup.getByRole('button', { name: '创建小剧场' }).click()
  const generate = page.getByRole('button', { name: '生成本场', exact: true })
  await generate.waitFor()
  expect(await page.locator('.read-history').count()).toBe(0)
  await page
    .locator('.theatre-actions')
    .getByRole('button', { name: '剧场设定', exact: true })
    .click()
  const editSetup = page.getByRole('dialog', { name: '剧场设定' })
  await editSetup.waitFor()
  expect(await editSetup.locator('.attachment-chip span').innerText()).toBe('参考.txt')
  await editSetup.getByLabel('情境（可选）').fill('尚未保存的情境')
  await cancelTheatreFilePicker(page, editSetup)
  expect(await editSetup.getByLabel('情境（可选）').inputValue()).toBe('尚未保存的情境')
  await page.keyboard.press('Escape')
  await editSetup.waitFor({ state: 'hidden' })
  const chat = (await backend.conversations.listChats()).find(
    (item) => item.mode === 'scripted-dialogue'
  )
  expect(chat.characterIds).toHaveLength(2)
  const savedSetup = (await backend.conversations.getSnapshot(chat.id)).chat.meta
  expect(savedSetup.settings.scenario.situation).toBe('排练结束后一起回家')
  expect(savedSetup.settings.tools.enabled).toEqual([
    'read_file_content',
    'grep_search_in_file',
    'read_url'
  ])
  expect(savedSetup.workspaceFiles[0].name).toBe('参考.txt')
  await expect.poll(() => page.locator('.two-actors .stage-actor').count()).toBe(2)
  await page.getByRole('button', { name: '调整舞台' }).click()
  await page.getByRole('button', { name: '朝向', exact: true }).waitFor()
  const stageTabWidths = await page
    .locator('.theatre-panel-tabs button')
    .evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect().width))
  expect(stageTabWidths).toHaveLength(2)
  expect(stageTabWidths.every((width) => width > 40)).toBe(true)
  await page.getByRole('button', { name: '朝向', exact: true }).click()
  await page.getByRole('button', { name: '面对面', exact: true }).click()
  const snapshot = () => backend.conversations.getSnapshot(chat.id)
  await expect
    .poll(async () => (await snapshot()).chat.meta.settings.presentation.orientation)
    .toBe('face-to-face')
  await page.getByRole('button', { name: '站位', exact: true }).click()
  expect(
    await page
      .locator('.theatre-halves button')
      .evaluateAll((buttons) => buttons.every((button) => button.textContent === ''))
  ).toBe(true)
  await page.locator('.theatre-halves button').last().click()
  await page.getByRole('button', { name: '缩小选中演员' }).click()
  await page.screenshot({ path: '/tmp/sakiko-theatre-adjust.png' })
  await expect
    .poll(async () => Object.keys((await snapshot()).chat.meta.settings.presentation.frames ?? {}))
    .toEqual([chat.characterIds[1]])
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: '调整舞台' }).waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '切换服装' }).click()
  const wardrobe = page.getByRole('dialog', { name: '服装', exact: true })
  const actorTabs = wardrobe.locator('.theatre-actor-tabs button')
  const tabTexts = await actorTabs.allTextContents()
  expect(tabTexts.every((text) => !text.includes('左 ·') && !text.includes('右 ·'))).toBe(true)
  const wardrobeMetrics = await wardrobe.evaluate((panel) => {
    const tabs = panel.querySelector('.theatre-actor-tabs').getBoundingClientRect()
    const [first, second] = Array.from(
      panel.querySelectorAll('.theatre-actor-tabs button'),
      (button) => button.getBoundingClientRect()
    )
    const list = panel.querySelector('.stage-wardrobe-list').getBoundingClientRect()
    return {
      widthDiff: Math.abs(first.width - second.width),
      gapBelowTabs: list.top - tabs.bottom
    }
  })
  expect(wardrobeMetrics.widthDiff).toBeLessThan(2)
  expect(wardrobeMetrics.gapBelowTabs).toBeGreaterThanOrEqual(8)
  await page.screenshot({ path: '/tmp/sakiko-theatre-wardrobe.png' })
  const savedCharacters = (await snapshot()).chat.meta.settings.characters
  await actorTabs.first().click()
  await wardrobe.getByRole('button', { name: '备用演出服 V3', exact: true }).click()
  await wardrobe.getByRole('button', { name: '备用演出服 V3 当前', exact: true }).waitFor()
  const updated = (await snapshot()).chat.meta.settings.characters
  expect(updated[chat.characterIds[1]]).toEqual(savedCharacters[chat.characterIds[1]])
  expect(updated.sakiko.modelOverrides[updated.sakiko.formId]).toBe('home-test-outfit')
  await actorTabs.last().click()
  await wardrobe.getByRole('button', { name: '管理服装' }).click()
  const manager = page.getByRole('dialog', { name: '服装管理' })
  await manager.waitFor()
  expect(await manager.getByLabel('角色', { exact: true }).inputValue()).toBe(chat.characterIds[1])
  await page.keyboard.press('Escape')
  await manager.waitFor({ state: 'hidden' })
  agent.run.mockImplementationOnce(async (request, observer) => {
    await observer.onResponse({
      responseId: 'script-message',
      protocol: {},
      appendParts: [
        {
          id: 'script-part',
          kind: 'dialogue',
          lines: request.characters.map((role, index) => ({
            id: 'script-line-' + index,
            speakerId: role.id,
            formId: role.formId,
            text: index ? '一起走吧。' : '排练结束了。',
            emotion: 'happiness',
            translation: null,
            performance: { motion: 'auto', expression: 'auto' }
          }))
        }
      ]
    })
    return { status: 'completed', usage: null }
  })
  await generate.click()
  await expect.poll(async () => (await snapshot()).chat.turns.length).toBe(1)
  await expect.poll(async () => (await snapshot()).activeTurnId, { timeout: 15000 }).toBeNull()
  const bothVisible = () =>
    page.locator('.two-actors canvas').evaluate(
      (canvas) =>
        new Promise((resolve) =>
          requestAnimationFrame(() => {
            // 只取舞台透明画布的非透明像素，避免把背景图或空画布算作演员。
            const sample = document.createElement('canvas')
            sample.width = 160
            sample.height = 100
            const context = sample.getContext('2d')
            context.drawImage(canvas, 0, 0, 160, 100)
            const { data } = context.getImageData(0, 0, 160, 100)
            const counts = [0, 0]
            for (let pixel = 0; pixel < 16000; pixel++) {
              if (data[pixel * 4 + 3] > 128) counts[Number(pixel % 160 >= 80)]++
            }
            resolve(counts)
          })
        )
    )
  await expect
    .poll(bothVisible, { timeout: 8000 })
    .toEqual([expect.toSatisfy((count) => count > 1000), expect.toSatisfy((count) => count > 1000)])
  await page.screenshot({ path: '/tmp/sakiko-theatre-stage.png' })
  await page
    .locator('.theatre-actions')
    .getByRole('button', { name: '回放本场', exact: true })
    .click()
  await page
    .locator('.theatre-actions')
    .getByRole('button', { name: '停止回放', exact: true })
    .click()
  await page.getByRole('button', { name: '展开聊天记录' }).click()
  const replayScript = page.getByRole('button', { name: '回放整场' })
  await replayScript.waitFor()
  // 面板入场时整体平移，同一帧读取两个气泡和按钮的位置。
  const alignment = await page.locator('.history-panel').evaluate((panel) => {
    const [first, second] = panel.querySelectorAll('.history-assistant .history-bubble')
    const replay = panel.querySelector('[aria-label="回放整场"]')
    return {
      leftOffset: second.getBoundingClientRect().left - first.getBoundingClientRect().left,
      buttonGap: replay.getBoundingClientRect().left - first.getBoundingClientRect().right
    }
  })
  expect(alignment.leftOffset).toBe(0)
  expect(alignment.buttonGap).toBeGreaterThan(0)
  await replayScript.click()
  await page.getByRole('button', { name: '收起聊天记录' }).click()
  const stop = page.locator('.home-subtitle').getByRole('button', { name: /停止.*回放/ })
  await stop.click()
  expect((await snapshot()).chat.turns).toHaveLength(1)
  // 验证无法面对面时隐藏「朝向」页签，并按是否曾开启面对面决定是否展示降级说明。
  await page.getByRole('button', { name: '调整舞台' }).click()
  await page.getByRole('button', { name: '朝向', exact: true }).click()
  const originalResolve = backend.characters.resolveCapabilities.bind(backend.characters)
  const resolveSpy = vi
    .spyOn(backend.characters, 'resolveCapabilities')
    .mockImplementation(async (characterId, options) => {
      const capability = await originalResolve(characterId, options)
      return characterId === 'sakiko' ? { ...capability, modelVersion: 'v2' } : capability
    })
  try {
    await backend.conversations.patchSettings(chat.id, {
      presentation: { orientation: 'face-to-face' }
    })
    await page.locator('.theatre-stage-title').waitFor()
    expect(await page.locator('.theatre-stage-title').innerText()).toBe('调整站位')
    expect(await page.getByRole('button', { name: '朝向', exact: true }).count()).toBe(0)
    await page.locator('.theatre-halves').waitFor()
    await page.locator('.theatre-degraded-note').waitFor()
    expect(await page.locator('.theatre-degraded-note').innerText()).toBe(
      '祥子的服装不支持面对面，已暂改为面向屏幕'
    )
    await page.screenshot({ path: '/tmp/sakiko-theatre-adjust-degraded.png' })
    await backend.conversations.patchSettings(chat.id, {
      presentation: { orientation: 'screen' }
    })
    await page.locator('.theatre-degraded-note').waitFor({ state: 'hidden' })
    expect(await page.locator('.theatre-stage-title').innerText()).toBe('调整站位')
    expect(await page.getByRole('button', { name: '朝向', exact: true }).count()).toBe(0)
  } finally {
    resolveSpy.mockRestore()
  }
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: '调整舞台' }).waitFor({ state: 'hidden' })
}

test.each(['production', 'development'])(
  '%s：引导进入主页、交互、真实模型与 HTTP/WS 聊天及恢复',
  async (mode) => {
    const directory = await mkdtemp(join(tmpdir(), 'sakiko-home-ui-'))
    let backend, server, application, vite
    let role
    const agent = {
      inspectContext: async () => ({ fit: 'fits' }),
      run: vi.fn(async (request, observer) => {
        await observer.onResponse({
          responseId: 'response-' + request.turnId,
          protocol: {},
          appendParts: [
            {
              id: 'p-' + request.turnId,
              kind: 'dialogue',
              lines: [
                {
                  id: 'line-' + request.turnId,
                  speakerId: 'sakiko',
                  formId: role.defaultFormId,
                  text: '今天也辛苦了，先休息一会儿吧。',
                  translation: null,
                  emotion: null,
                  performance: { motion: 'auto', expression: 'auto' }
                }
              ]
            }
          ]
        })
        return { status: 'completed', usage: null }
      })
    }
    try {
      backend = await createCoreBackend({
        appDataRoot: join(directory, 'app'),
        resourceLibraryRoot: join(directory, 'library'),
        cacheRoot: join(directory, 'cache'),
        builtinDirectory: join(root, 'resources/builtin-characters'),
        scanReminders: false,
        agent
      })
      let rendererUrl = ''
      if (mode === 'development') {
        vite = await createViteServer({
          configFile: false,
          root: join(root, 'src/renderer'),
          resolve: { alias: { '@': join(root, 'src/renderer/src') } },
          plugins: [react(), tailwindcss()],
          server: { host: '127.0.0.1', port: 0 }
        })
        await vite.listen()
        rendererUrl = 'http://127.0.0.1:' + vite.httpServer.address().port
      }
      server = await startHttpServer(backend, {
        origins: ['null', 'file://', ...(rendererUrl ? [rendererUrl] : [])]
      })
      const hostFile = join(directory, 'host.cjs')
      await writeFile(
        hostFile,
        `const { app, BrowserWindow, ipcMain } = require('electron')
app.whenReady().then(async () => {
  ipcMain.handle('host:backend-connection', () => JSON.parse(process.env.HOME_TEST_CONNECTION))
  ipcMain.handle('host:get-window-visibility', () => true)
  const window = new BrowserWindow({
    width: 1440, height: 1024,
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset', titleBarOverlay: true } : {}),
    webPreferences: {
      preload: process.env.HOME_TEST_PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true
    }
  })
  if (process.env.HOME_TEST_RENDERER_URL) await window.loadURL(process.env.HOME_TEST_RENDERER_URL)
  else await window.loadFile(process.env.HOME_TEST_RENDERER)
  app.show?.()
  window.show()
})
app.on('window-all-closed', () => app.quit())`
      )
      const env = {
        ...process.env,
        HOME_TEST_CONNECTION: JSON.stringify({ baseUrl: server.baseUrl, token: server.token }),
        HOME_TEST_RENDERER_URL: rendererUrl,
        HOME_TEST_PRELOAD: join(root, 'out/preload/index.cjs'),
        HOME_TEST_RENDERER: join(root, 'out/renderer/index.html')
      }
      delete env.ELECTRON_RUN_AS_NODE
      application = await electron.launch({
        args: [
          hostFile,
          '--user-data-dir=' + join(directory, 'desktop'),
          '--use-fake-device-for-media-stream',
          '--use-fake-ui-for-media-stream'
        ],
        env
      })
      const page = await application.firstWindow()
      page.setDefaultTimeout(15000)
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      await page.getByRole('button', { name: '快速开始', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
      await page.getByRole('button', { name: '跳过，直接开始', exact: true }).click()
      await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
      expect((await backend.onboarding.getState()).initialized).toBe(true)
      if (process.platform === 'darwin') {
        const regions = await page.locator('.app-window').evaluate((windowElement) =>
          Array.from(windowElement.querySelectorAll('*'))
            .filter(
              (element) => getComputedStyle(element).getPropertyValue('app-region') === 'drag'
            )
            .map((element) => element.getBoundingClientRect().toJSON())
        )
        expect(regions).toHaveLength(1)
        const [strip] = regions
        const brand = await page.locator('.home-brand').boundingBox()
        const stage = await page.locator('.home-stage').boundingBox()
        expect(strip.height).toBeGreaterThan(0)
        expect(strip.bottom).toBeLessThanOrEqual(brand.y)
        expect(strip.x).toBeLessThan(stage.x)
        expect(strip.right).toBe(stage.x + stage.width)
      }
      const dialogueTab = page.getByRole('tab', { name: '对话', exact: true })
      const theatreTab = page.getByRole('tab', { name: '小剧场', exact: true })
      const tabBackground = () =>
        theatreTab.evaluate((element) => getComputedStyle(element).backgroundColor)
      const restingTabBackground = await tabBackground()
      await theatreTab.hover()
      await expect.poll(tabBackground).not.toBe(restingTabBackground)
      await dialogueTab.press('ArrowRight')
      await expect
        .poll(() => theatreTab.evaluate((element) => document.activeElement === element))
        .toBe(true)
      expect(await dialogueTab.getAttribute('aria-selected')).toBe('true')
      await theatreTab.press('Enter')
      expect(await theatreTab.getAttribute('aria-selected')).toBe('true')
      await page.getByRole('tabpanel', { name: '小剧场' }).waitFor()
      await dialogueTab.click()
      await backend.assets.using({}, async (owner) => {
        const { asset } = await backend.assets.importAsset(
          {
            kind: 'bytes',
            name: 'broken.png',
            mediaType: 'image/png',
            bytes: Buffer.from('broken')
          },
          owner,
          'avatar'
        )
        await backend.characters.save({
          id: 'local-actor',
          name: '测试演员',
          revision: 0,
          bangDreamId: 'tomori',
          defaultFormId: 'default',
          forms: [
            {
              id: 'default',
              displayName: '测试演员',
              description: '头像回退验证',
              avatar: asset,
              models: [],
              defaultModelId: null,
              voice: null
            }
          ]
        })
      })
      role = await backend.characters.get('sakiko')
      const form = role.forms.find((item) => item.id === role.defaultFormId)
      const defaultName = form.models.find((item) => item.id === form.defaultModelId).name
      const alternate = role.forms.find((item) => item !== form).models[0]
      form.models.push({ ...alternate, id: 'home-test-outfit', name: '备用服装' })
      role = await backend.characters.save(role)
      await backend.settings.saveConnection(
        {
          id: 'test',
          revision: 0,
          name: '测试连接',
          provider: 'openai-compatible',
          protocol: 'chat',
          baseUrl: 'http://127.0.0.1:9999/v1',
          attachmentTransport: 'inline',
          models: [
            { id: 'test', declared: { contextTokens: 10000 }, overrides: {} },
            ...Array.from({ length: 11 }, (_, index) => ({
              id: 'model-' + index,
              declared: {},
              overrides: {}
            }))
          ]
        },
        { kind: 'replace', secret: 'test' }
      )
      await backend.settings.update({ generation: { connectionId: 'test', modelId: 'test' } })
      const brokenPortrait = page.waitForResponse((response) => {
        const url = new URL(response.url())
        return (
          url.origin === server.baseUrl &&
          /^\/api\/v1\/assets\/[^/]+$/.test(url.pathname) &&
          response.headers()['content-type'] === 'image/png'
        )
      })
      // 重载验证正式初始化恢复；只使用隔离目录和可控 Agent，不发送外部请求。
      await page.reload()
      await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
      await brokenPortrait
      const fallbackPortrait = page
        .getByRole('button', { name: '测试演员', exact: true })
        .locator('img')
      await expect
        .poll(() =>
          fallbackPortrait.evaluate((image) =>
            image.naturalWidth ? new URL(image.src).pathname : ''
          )
        )
        .toMatch(/\/tomori(?:-[^/]+)?\.webp$/)
      await expect
        .poll(() => page.locator('.home-canvas').getAttribute('data-stage-state'))
        .toBe('ready')
      await expect.poll(() => page.locator('.home-stage-status[role=alert]').count()).toBe(0)
      const readCatalog = backend.setup.getCatalog.bind(backend.setup)
      backend.setup.getCatalog = (options) => readCatalog({ ...options, refresh: false })
      await page.keyboard.press('ControlOrMeta+,')
      const settings = page.getByRole('dialog', { name: '设置' })
      await settings.getByRole('heading', { name: '已配置的连接' }).waitFor()
      if (mode === 'production')
        await page.screenshot({ path: '/tmp/sakiko-settings-connections.png' })
      await settings.getByRole('button', { name: '编辑', exact: true }).click()
      expect(await settings.getByRole('textbox', { name: '连接名称' }).inputValue()).toBe(
        '测试连接'
      )
      expect(await settings.getByLabel('API Key', { exact: true }).inputValue()).toBe('')
      const provider = settings.getByRole('combobox', { name: '服务商' })
      await provider.click()
      await verifySearchSelectScrollbar(page, provider)
      await settings.getByRole('textbox', { name: '连接名称' }).click()
      expect(await provider.getAttribute('aria-expanded')).toBe('false')
      await page.keyboard.press('Escape')
      await settings.waitFor({ state: 'hidden' })
      await page.getByRole('button', { name: '语音输入', exact: true }).click()
      await page
        .getByRole('dialog', { name: '启用语音输入' })
        .getByRole('button', { name: '前往下载' })
        .click()
      await settings.getByRole('heading', { name: '附加资源下载' }).waitFor()
      const target = `${process.platform}-${process.arch}`
      if (['win32-x64', 'darwin-arm64'].includes(target)) {
        await settings.getByRole('heading', { name: '语音识别模型' }).waitFor()
        await verifyResourceDownload(page, backend, settings)
      } else await settings.getByText('当前仅支持 Windows x64 和 macOS arm64。').waitFor()
      expect(
        await settings.evaluate((element) => {
          const close = element.querySelector('.settings-dismiss').getBoundingClientRect()
          const scroll = element.querySelector('.settings-content').getBoundingClientRect()
          return close.bottom <= scroll.top
        })
      ).toBe(true)
      if (mode === 'production')
        await page.screenshot({ path: '/tmp/sakiko-settings-resources.png' })
      await settings.getByRole('button', { name: '应用', exact: true }).click()
      const preferences = settings.getByRole('region', { name: '偏好' })
      await preferences.getByRole('heading', { name: '偏好', exact: true }).waitFor()
      const speedSlider = preferences.getByRole('slider', { name: '回复语速' })
      const pauseSlider = preferences.getByRole('slider', { name: '句间停顿' })
      expect(await speedSlider.getAttribute('aria-valuenow')).toBe('0.88')
      expect(await pauseSlider.getAttribute('aria-valuenow')).toBe('0.5')
      await speedSlider.press('ArrowRight')
      await pauseSlider.press('ArrowRight')
      await expect.poll(() => backend.settings.get('speech.speed')).toBe(0.9)
      await expect.poll(() => backend.settings.get('speech.sentencePauseMs')).toBe(550)
      await preferences.getByRole('button', { name: '恢复默认语速' }).click()
      await preferences.getByRole('button', { name: '恢复默认句间停顿' }).click()
      await expect.poll(() => backend.settings.get('speech.speed')).toBe(0.88)
      await expect.poll(() => backend.settings.get('speech.sentencePauseMs')).toBe(500)
      expect(await preferences.getByRole('button', { name: '恢复默认语速' }).isDisabled()).toBe(
        true
      )
      await settings.getByRole('button', { name: '关闭设置' }).click()
      const actualFeatureState = backend.setup.getFeatureState.bind(backend.setup)
      backend.setup.getFeatureState = async () => ({
        ...(await actualFeatureState()),
        asrReady: true
      })
      const transcribe = vi
        .spyOn(backend.recognition, 'transcribe')
        .mockImplementation(async (request) => {
          expect(request).toMatchObject({ sampleRateHz: 16000, language: null })
          const wave = await readFile(await backend.assets.localPath(request.audio))
          expect(wave.subarray(0, 4).toString()).toBe('RIFF')
          return { status: 'succeeded', text: '识别到的文字' }
        })
      await page.reload()
      const composer = page.getByRole('textbox', { name: '消息' })
      const toolsButton = page.getByRole('button', { name: '工具', exact: true })
      await toolsButton.click()
      const toolMenu = page.getByRole('dialog', { name: '工具设置' })
      const expandFiles = toolMenu.getByRole('button', { name: '文件操作', exact: true })
      expect(await expandFiles.getAttribute('aria-expanded')).toBe('false')
      await expandFiles.click()
      expect(await expandFiles.getAttribute('aria-expanded')).toBe('true')
      const read = toolMenu.getByRole('checkbox', { name: '读取文件', exact: true })
      await read.click()
      const fileGroup = toolMenu.getByRole('checkbox', { name: '文件操作', exact: true })
      expect(await fileGroup.getAttribute('aria-checked')).toBe('mixed')
      await fileGroup.click()
      expect(await fileGroup.getAttribute('aria-checked')).toBe('true')
      await fileGroup.click()
      expect(await fileGroup.getAttribute('aria-checked')).toBe('false')
      await fileGroup.click()
      expect(await fileGroup.getAttribute('aria-checked')).toBe('true')
      const searchTools = toolMenu.getByRole('textbox', { name: '搜索工具' })
      await searchTools.fill('天气')
      await toolMenu.getByRole('checkbox', { name: '查询天气', exact: true }).waitFor()
      expect(await fileGroup.count()).toBe(0)
      await searchTools.fill('')
      await composer.click()
      expect(await toolMenu.isVisible()).toBe(false)
      await page
        .locator('input[type=file][multiple]')
        .first()
        .setInputFiles({
          name: '场景参考.txt',
          mimeType: 'text/plain',
          buffer: Buffer.from('秋日的海边')
        })
      await page.getByRole('button', { name: '移除 场景参考.txt' }).waitFor()
      await expect
        .poll(() => page.locator('.attachment-chip span').innerText())
        .toBe('场景参考.txt')
      await page.getByRole('button', { name: '移除 场景参考.txt' }).click()
      await composer.fill('保留原草稿')
      await page.getByRole('button', { name: '语音输入', exact: true }).click()
      const finishRecording = page.getByRole('button', { name: '结束录音并识别' })
      await finishRecording.waitFor()
      await page.waitForTimeout(400)
      await finishRecording.click()
      await expect.poll(() => composer.inputValue()).toBe('保留原草稿\n识别到的文字')
      expect(transcribe).toHaveBeenCalledOnce()
      transcribe.mockRestore()
      backend.setup.getFeatureState = actualFeatureState
      const picker = page.getByRole('combobox', { name: '对话模型' })
      expect(await picker.inputValue()).toBe('test')
      await picker.click()
      await verifySearchSelectScrollbar(page, picker)
      await page.getByRole('option', { name: 'test', exact: true }).click()
      await picker.click()
      await page.getByRole('button', { name: '管理模型' }).click()
      expect(await picker.getAttribute('aria-expanded')).toBe('false')
      await settings.getByRole('heading', { name: '已配置的连接' }).waitFor()
      expect(await settings.getByRole('button', { name: /^拖动排序：/ }).count()).toBe(0)
      await settings.getByRole('button', { name: '新建连接', exact: true }).click()
      await settings.getByRole('textbox', { name: '连接名称' }).fill('备用连接')
      await settings.getByLabel('API Key', { exact: true }).fill('dummy-key')
      await settings.getByLabel('模型 ID 1', { exact: true }).fill('alt-model')
      await settings.getByRole('button', { name: '保存连接', exact: true }).click()
      await settings.getByRole('heading', { name: '备用连接', exact: true }).waitFor()
      const altHandle = settings.getByRole('button', { name: '拖动排序：备用连接', exact: true })
      const testHandle = settings.getByRole('button', { name: '拖动排序：测试连接', exact: true })
      await altHandle.waitFor()
      await testHandle.waitFor()
      await altHandle.focus()
      await altHandle.press('ArrowUp')
      await expect
        .poll(() => settings.locator('.settings-connection-summary h4').allTextContents())
        .toEqual(['备用连接', '测试连接'])
      expect(await altHandle.evaluate((element) => document.activeElement === element)).toBe(true)
      await settings.getByRole('button', { name: '关闭设置' }).click()
      await settings.waitFor({ state: 'hidden' })
      await picker.click()
      await expect
        .poll(() =>
          page
            .getByRole('listbox', { name: '对话模型' })
            .getByRole('group')
            .evaluateAll((groups) => groups.map((group) => group.getAttribute('aria-label')))
        )
        .toEqual(['备用连接', '测试连接'])
      await page.getByRole('button', { name: '管理模型' }).click()
      await settings.getByRole('heading', { name: '已配置的连接' }).waitFor()
      const altCard = settings
        .locator('.settings-card')
        .filter({ has: page.getByRole('heading', { name: '备用连接', exact: true }) })
      let handleX, altCardBox
      const writeOrder = vi.spyOn(backend.settings, 'reorderConnections')
      async function previewSwap() {
        await testHandle.click({ trial: true })
        const testHandleBox = await testHandle.boundingBox()
        altCardBox = await altCard.boundingBox()
        handleX = testHandleBox.x + testHandleBox.width / 2
        const handleY = testHandleBox.y + testHandleBox.height / 2
        await page.mouse.move(handleX, handleY)
        await page.mouse.down()
        await page.mouse.move(handleX, handleY - 2)
        expect(await settings.locator('.settings-drop-slot').count()).toBe(0)
        await page.mouse.move(handleX, altCardBox.y + 8, { steps: 8 })
        await settings.locator('.settings-drop-slot').waitFor()
        // 按住并停留超过让位动画时长，列表节点与已保存的顺序应保持稳定。
        await page.waitForTimeout(350)
        expect(await settings.locator('.settings-connection-summary h4').allTextContents()).toEqual(
          ['备用连接', '测试连接']
        )
        expect(await settings.locator('article[data-dragging="true"] h4').innerText()).toBe(
          '测试连接'
        )
        expect(writeOrder).not.toHaveBeenCalled()
      }
      try {
        await previewSwap()
        await page.keyboard.press('Escape')
        await page.mouse.up()
        await settings.locator('.settings-drop-slot').waitFor({ state: 'detached' })
        await expect
          .poll(() => testHandle.evaluate((el) => el.closest('article').getAnimations().length))
          .toBe(0)
        await previewSwap()
        await testHandle.evaluate((handle) => {
          handle.addEventListener(
            'pointermove',
            (event) => handle.releasePointerCapture(event.pointerId),
            { once: true }
          )
        })
        await page.mouse.move(handleX, altCardBox.y + 10, { steps: 2 })
        await page.mouse.up()
        await settings.locator('.settings-drop-slot').waitFor({ state: 'detached' })
        expect(writeOrder).not.toHaveBeenCalled()
        await expect
          .poll(() => testHandle.evaluate((el) => el.closest('article').getAnimations().length))
          .toBe(0)
        await previewSwap()
        await page.mouse.up()
        await expect.poll(() => writeOrder.mock.calls.length).toBe(1)
      } finally {
        await page.mouse.up()
        writeOrder.mockRestore()
      }
      await expect
        .poll(() => settings.locator('.settings-connection-summary h4').allTextContents())
        .toEqual(['测试连接', '备用连接'])
      await altCard.getByRole('button', { name: '删除', exact: true }).click()
      await altCard.getByRole('button', { name: '删除连接', exact: true }).click()
      await altCard.waitFor({ state: 'hidden' })
      expect(await settings.getByRole('button', { name: /^拖动排序：/ }).count()).toBe(0)
      await settings.getByRole('button', { name: '关闭设置' }).click()
      await settings.waitFor({ state: 'hidden' })
      await page.getByRole('textbox', { name: '消息' }).fill('你好，**祥子**！')
      await page
        .getByRole('textbox', { name: '消息' })
        .dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
      expect(agent.run).not.toHaveBeenCalled()
      await page.getByRole('textbox', { name: '消息' }).press('Shift+Enter')
      expect(await page.getByRole('textbox', { name: '消息' }).inputValue()).toContain('\n')
      await verifyDraftOutfit(page, backend, defaultName)
      await page.getByRole('textbox', { name: '消息' }).fill('你好，**祥子**！')
      await page.getByRole('button', { name: '发送消息' }).click()
      await page.locator('.subtitle-text').filter({ hasText: '今天也辛苦了' }).waitFor()
      expect(await page.locator('.subtitle-text').innerText()).toBe(
        '今天也辛苦了，先休息一会儿吧。'
      )
      await dragStageToolsOver(page, composer)
      await dragStageToolsOver(page, page.getByRole('region', { name: '当前台词' }))
      const [outfitChat] = await backend.conversations.listChats()
      const savedOutfit = async () => {
        const { chat } = await backend.store.load(outfitChat.id)
        return chat.meta.settings.characters.sakiko.modelOverrides[role.defaultFormId]
      }
      expect(await savedOutfit()).toBe('home-test-outfit')
      await page.getByRole('button', { name: '切换服装' }).click()
      const wardrobe = page.getByRole('dialog', { name: '服装' })
      await wardrobe.getByRole('button', { name: '备用服装 V3 当前', exact: true }).waitFor()
      await wardrobe.getByRole('button', { name: defaultName + ' V3', exact: true }).click()
      await wardrobe.getByRole('button', { name: defaultName + ' V3 当前', exact: true }).waitFor()
      expect(await savedOutfit()).toBe(form.defaultModelId)
      await wardrobe.getByRole('button', { name: '管理服装' }).click()
      const manager = page.getByRole('dialog', { name: '服装管理' })
      const managerShell = await manager.elementHandle()
      const outfit = manager.getByRole('listitem').filter({ hasText: '备用服装' })
      await outfit.locator('.outfit-version-badge').getByText('V3', { exact: true }).waitFor()
      const defaultMenu = manager.getByRole('button', { name: '管理 ' + defaultName, exact: true })
      const altMenu = outfit.getByRole('button', { name: '管理 备用服装', exact: true })
      const openMenu = manager.getByRole('menu')
      await defaultMenu.click()
      await openMenu.waitFor()
      await altMenu.click()
      expect(await openMenu.count()).toBe(1)
      expect(await altMenu.getAttribute('aria-expanded')).toBe('true')
      const menuBounds = await openMenu.boundingBox()
      const triggerBounds = await altMenu.boundingBox()
      expect(menuBounds.x + menuBounds.width).toBeLessThanOrEqual(triggerBounds.x)
      await page.keyboard.press('Escape')
      await openMenu.waitFor({ state: 'hidden' })
      await manager.waitFor()
      await expect
        .poll(() => altMenu.evaluate((element) => document.activeElement === element))
        .toBe(true)
      // 缩小触发按钮左侧的可用空间，验证菜单仍完整显示在浮窗内。
      const managerBounds = await manager.boundingBox()
      await altMenu.evaluate((element, left) => {
        Object.assign(element.style, { position: 'fixed', left: `${left}px` })
      }, managerBounds.x + 16)
      await altMenu.press('Enter')
      await openMenu.waitFor()
      const movedMenuBounds = await openMenu.boundingBox()
      expect(await openMenu.getAttribute('data-side')).not.toBe('left')
      expect(movedMenuBounds.x).toBeGreaterThanOrEqual(managerBounds.x)
      expect(movedMenuBounds.x + movedMenuBounds.width).toBeLessThanOrEqual(
        managerBounds.x + managerBounds.width
      )
      await page.keyboard.press('Escape')
      await openMenu.waitFor({ state: 'hidden' })
      await altMenu.evaluate((element) => element.removeAttribute('style'))
      await altMenu.click()
      await openMenu.getByRole('menuitem', { name: '重命名' }).click()
      const rename = page.getByRole('dialog', { name: '重命名服装' })
      await expectSharedDialog(page, rename, managerShell)
      await rename.getByLabel('服装名称').fill('备用演出服')
      await rename.getByRole('button', { name: '保存名称' }).click()
      await manager.getByText('备用演出服', { exact: true }).waitFor()
      await expectSharedDialog(page, manager, managerShell)
      expect(
        await manager
          .getByRole('button', { name: '管理 备用演出服', exact: true })
          .evaluate((element) => document.activeElement === element)
      ).toBe(true)
      if (mode === 'production')
        await manager.screenshot({ path: '/tmp/sakiko-outfit-manager.png' })
      await verifyOutfitDownloads(page, backend, manager, role, form, mode)
      await manager.getByLabel('角色', { exact: true }).selectOption('anon')
      await manager.getByText('正在读取角色服装…').waitFor({ state: 'hidden' })
      expect(await manager.getByText('形态', { exact: true }).count()).toBe(0)
      await manager.getByRole('button', { name: '关闭服装管理' }).click()
      await manager.waitFor({ state: 'hidden' })
      const downloadEntry = page.getByRole('button', { name: '下载中心', exact: true })
      expect(await downloadEntry.count()).toBe(0)
      backend.outfits.jobs.set('home-test-sidebar-entry', {
        id: 'home-test-sidebar-entry',
        target: { characterId: role.id, formId: form.id },
        item: {
          id: 'sidebar-test-outfit',
          version: 'v2',
          characterId: role.bangDreamId,
          name: '侧栏待续下载',
          source: 'bestdori'
        },
        status: 'interrupted',
        progress: { completed: 1, total: 4 }
      })
      await downloadEntry.waitFor()
      await downloadEntry.click()
      const sidebarDownloads = page.getByRole('dialog', { name: '下载中心' })
      await sidebarDownloads.waitFor()
      await sidebarDownloads
        .getByRole('listitem')
        .filter({ hasText: '侧栏待续下载' })
        .getByRole('button', { name: '取消', exact: true })
        .click()
      await page.keyboard.press('Escape')
      await sidebarDownloads.waitFor({ state: 'hidden' })
      await downloadEntry.waitFor({ state: 'hidden' })
      const editRole = page.getByRole('button', { name: /^编辑角色归属：/ }).first()
      const roleHeader = editRole.locator('..')
      await roleHeader.hover()
      const expanded = await roleHeader.locator('.home-role').getAttribute('aria-expanded')
      await editRole.click()
      const affiliation = page.getByRole('dialog', { name: '编辑角色归属' })
      const search = affiliation.getByRole('combobox', { name: '对应角色' })
      await search.click()
      await verifySearchSelectScrollbar(page, search)
      await search.press('Tab')
      expect(await search.getAttribute('aria-expanded')).toBe('false')
      await search.click()
      const groups = affiliation.getByRole('listbox', { name: '对应角色' }).getByRole('group')
      expect(await groups.count()).toBe(13)
      expect(await groups.nth(0).getAttribute('aria-label')).toBe('Poppin’Party')
      const afterglow = groups.nth(1)
      expect(await afterglow.getAttribute('aria-label')).toBe('Afterglow')
      expect(await afterglow.evaluate((element) => getComputedStyle(element).borderTopWidth)).toBe(
        '1px'
      )
      expect(await groups.last().getAttribute('aria-label')).toBe('其他 BanG Dream! 角色')
      await search.fill('MyGO')
      expect(await groups.count()).toBe(1)
      expect(await groups.first().getAttribute('aria-label')).toBe('MyGO!!!!!')
      expect(await groups.first().getByRole('option').count()).toBe(5)
      await search.fill('戶山香澄')
      await affiliation.getByText('没有匹配的选项').waitFor()
      await search.fill('kasumi')
      await affiliation.getByRole('option', { name: '户山香澄' }).waitFor()
      await page.keyboard.press('Enter')
      expect(await search.inputValue()).toBe('户山香澄')
      await search.fill('ビオラ')
      await affiliation.getByRole('option', { name: '薇欧拉', exact: true }).waitFor()
      await page.keyboard.press('Enter')
      expect(await search.inputValue()).toBe('薇欧拉')
      await search.fill('若葉睦')
      await affiliation.getByRole('option', { name: '若叶睦', exact: true }).waitFor()
      await page.keyboard.press('Enter')
      expect(await search.inputValue()).toBe('若叶睦')
      await search.fill('珠手知由（chu2）')
      await affiliation.getByRole('option', { name: '珠手知由（chu2）', exact: true }).waitFor()
      await page.keyboard.press('Enter')
      expect(await search.inputValue()).toBe('珠手知由（chu2）')
      await affiliation.getByRole('button', { name: '取消', exact: true }).click()
      expect(await roleHeader.locator('.home-role').getAttribute('aria-expanded')).toBe(expanded)
      expect(await savedOutfit()).toBe(form.defaultModelId)
      await page.evaluate(() => {
        window.homeCanvas = document.querySelector('.home-canvas canvas')
      })
      await page.getByRole('button', { name: '展开聊天记录' }).click()
      await page.getByRole('complementary', { name: '聊天记录' }).waitFor()
      await page.locator('.history-panel').evaluate(async (element) => {
        await Promise.all(element.getAnimations().map((animation) => animation.finished))
      })
      if (process.platform === 'darwin') {
        const history = page.getByRole('complementary', { name: '聊天记录' })
        expect(
          await history.evaluate((element) =>
            getComputedStyle(element).getPropertyValue('app-region')
          )
        ).toBe('no-drag')
      }
      await expect.poll(() => page.locator('.history-bubble').count()).toBe(2)
      const replay = page.getByRole('button', { name: '回放本轮回复' })
      const stageStop = page.getByRole('button', { name: '停止本轮回放' })
      await replay.waitFor()
      const replayBounds = await replay.boundingBox()
      const userBounds = await page.locator('.history-user .history-bubble').boundingBox()
      expect(replayBounds.x + replayBounds.width).toBeLessThan(userBounds.x)
      await replay.click()
      await stageStop.click()
      await replay.waitFor()
      await replay.click()
      await stageStop.waitFor()
      await replay.waitFor()
      await replay.click()
      await stageStop.waitFor()
      await page.getByRole('button', { name: '收起聊天记录' }).click()
      await stageStop.click()
      await page.getByRole('button', { name: '展开聊天记录' }).click()
      await replay.waitFor()
      expect(
        await page.evaluate(
          () => window.homeCanvas === document.querySelector('.home-canvas canvas')
        )
      ).toBe(true)
      expect(await page.locator('.history-bubble strong').innerText()).toBe('祥子')
      expect(agent.run.mock.calls[0][0].tools.map((tool) => tool.name)).toContain('ask_user')
      // 端到端窗口可能受宿主显示器高度限制，先固定设计尺寸再验证双轴拖动。
      const viewport = await page.context().newCDPSession(page)
      await viewport.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 1024,
        deviceScaleFactor: 1,
        mobile: false
      })
      await expect
        .poll(async () => (await page.locator('.history-panel').boundingBox()).height)
        .toBe(671)
      const initialSize = await page.locator('.history-panel').boundingBox()
      const shortUser = page.locator('.history-user .history-bubble')
      const shortUserWidth = (await shortUser.boundingBox()).width
      const resizeHandle = page.getByRole('button', { name: '调整记录框大小', exact: true })
      await resizeHandle.hover()
      const handle = await resizeHandle.boundingBox()
      await page.mouse.down()
      await page.mouse.move(handle.x - 91, handle.y + 49, { steps: 5 })
      await page.mouse.up()
      await expect
        .poll(async () => (await page.locator('.history-panel').boundingBox()).width)
        .toBeCloseTo(initialSize.width + 100, 0)
      const resized = await page.locator('.history-panel').boundingBox()
      expect(resized.height).toBeCloseTo(initialSize.height + 40, 0)
      expect((await shortUser.boundingBox()).width).toBeCloseTo(shortUserWidth, 0)
      await page.screenshot({ path: '/tmp/sakiko-home-1440.png' })
      await viewport.send('Emulation.clearDeviceMetricsOverride')
      await application.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0].setSize(1147, 745)
      )
      await page.waitForTimeout(300)
      const stageBounds = await page.locator('.home-stage').boundingBox()
      const inputBounds = await page.locator('.home-composer').boundingBox()
      expect(inputBounds.x).toBeGreaterThan(stageBounds.x)
      expect(inputBounds.y + inputBounds.height).toBeLessThanOrEqual(745)
      await page.screenshot({ path: '/tmp/sakiko-home-1147.png' })
      await application.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0].setContentSize(1024, 640)
      )
      await page.screenshot({ path: '/tmp/sakiko-home-1024.png', scale: 'css' })
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth === window.innerWidth)
      ).toBe(true)
      const selected = (await backend.conversations.listChats())[0].id
      await page.reload()
      await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
      await expect.poll(() => page.locator('.home-chat-row.selected').count()).toBe(1)
      expect(await page.locator('.home-chat-row.selected').innerText()).toContain('今天也辛苦了')
      await expect
        .poll(() => page.locator('.subtitle-text').innerText())
        .toBe('今天也辛苦了，先休息一会儿吧。')
      expect(agent.run).toHaveBeenCalledTimes(1)
      await page.getByRole('button', { name: '展开聊天记录' }).click()
      expect(
        (await page.getByRole('complementary', { name: '聊天记录' }).boundingBox()).width
      ).toBeCloseTo(resized.width, 0)
      await viewport.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 1024,
        deviceScaleFactor: 1,
        mobile: false
      })
      await expect
        .poll(async () => Math.round((await page.locator('.history-panel').boundingBox()).height))
        .toBe(Math.round(resized.height))
      await viewport.send('Emulation.clearDeviceMetricsOverride')
      await viewport.detach()
      if (mode === 'production') {
        await verifyHistory(page, application, backend, selected)
        await verifySidebar(page, application, backend, selected)
      }
      await page.getByRole('button', { name: '新对话', exact: true }).click()
      expect((await backend.conversations.listChats()).map((chat) => chat.id)).toEqual([selected])
      const remove = page.getByRole('button', { name: '删除对话：你好，**祥子**！', exact: true })
      const removeRow = remove.locator('..')
      const rowBackground = () =>
        removeRow.evaluate((element) => getComputedStyle(element).backgroundColor)
      const removeOpacity = () => remove.evaluate((element) => getComputedStyle(element).opacity)
      const deleteDialog = page.getByRole('dialog', { name: '删除这段对话？' })
      await page.locator('.home-brand').hover()
      await expect.poll(removeOpacity).toBe('0')
      const restingBackground = await rowBackground()
      await removeRow.hover()
      await remove.click()
      await deleteDialog.getByRole('button', { name: '取消', exact: true }).click()
      expect(await backend.conversations.listChats()).toHaveLength(1)
      expect(await remove.evaluate((element) => document.activeElement === element)).toBe(true)
      await expect.poll(rowBackground).toBe(restingBackground)
      await expect.poll(removeOpacity).toBe('0')
      await remove.press('Enter')
      await deleteDialog.waitFor()
      await page.keyboard.press('Escape')
      expect(await remove.evaluate((element) => document.activeElement === element)).toBe(true)
      await expect.poll(removeOpacity).toBe('1')
      await remove.press('Enter')
      await deleteDialog.getByRole('button', { name: '删除', exact: true }).click()
      await expect.poll(async () => (await backend.conversations.listChats()).length).toBe(0)
      await page.getByRole('dialog').waitFor({ state: 'hidden' })
      await page.getByRole('button', { name: '新对话', exact: true }).click()
      const generation = Promise.withResolvers()
      agent.run.mockImplementationOnce(async () => {
        await generation.promise
        throw problemError('provider_unavailable', '模型服务暂时不可用。', true)
      })
      const composerGroup = page.locator('.home-composer-group')
      try {
        await page.getByRole('textbox', { name: '消息' }).fill('验证发送失败提示')
        await page.getByRole('button', { name: '发送消息' }).click()
        await composerGroup.getByRole('status').getByText('正在生成回复…').waitFor()
      } finally {
        generation.resolve()
      }
      await composerGroup.getByRole('alert').getByText('模型服务暂时不可用。').waitFor()
      await composerGroup.getByRole('status').waitFor({ state: 'hidden' })
      const [failedChat] = await backend.conversations.listChats()
      const { chat } = await backend.store.load(failedChat.id)
      expect(chat.turns[0].messages.map(({ kind }) => kind)).toEqual(['user'])
      agent.run.mockImplementationOnce(async (request, observer, signal) => {
        const responseId = '互动-' + request.turnId
        for (const [toolName, params] of [
          ['ask_user', { question: '去哪儿？', choices: ['海边', '山上'] }],
          ['start_lottery', { title: '选一种饮料', options: ['茶', '果汁', '水'] }]
        ]) {
          const callId = toolName
          await observer.onResponse({
            responseId,
            protocol: {},
            appendParts: [
              { id: callId, kind: 'tool-call', callId, toolName, arguments: params, protocol: {} }
            ]
          })
          const outcome = await request.tools
            .find((tool) => tool.name === toolName)
            .execute(
              params,
              { turnId: request.turnId, assistantMessageId: responseId, callId },
              signal
            )
          await observer.onToolResult({
            id: 'result-' + callId,
            kind: 'tool-result',
            assistantMessageId: responseId,
            callId,
            origin: 'tool',
            outcome,
            extensions: {}
          })
        }
        return { status: 'completed', usage: null }
      })
      await page.getByRole('textbox', { name: '消息' }).fill('互动')
      await page.getByRole('button', { name: '发送消息' }).click()
      const closeHistory = page.getByRole('button', { name: '收起聊天记录' })
      if (await closeHistory.isVisible()) await closeHistory.click()
      const card = page.locator('.interaction-card')
      await card.getByText('去哪儿？').waitFor()
      const mountain = card.getByRole('button', { name: /山上/ })
      await card.locator('strong').first().click()
      await page.keyboard.press('2')
      expect(await mountain.evaluate((element) => element === document.activeElement)).toBe(true)
      await page.keyboard.press('2')
      await page.emulateMedia({ reducedMotion: mode === 'production' ? 'reduce' : 'no-preference' })
      await card.getByRole('button', { name: '开始抽签' }).click()
      if (mode === 'development') {
        await card.getByText('正在抽签').waitFor()
        expect(await card.getByText(/^抽中/).count()).toBe(0)
      }
      await card.getByText(/^抽中/).waitFor()
      await verifyTheatre(page, backend, agent)
      expect(errors).toEqual([])
    } finally {
      await application?.close()
      await server?.close()
      await backend?.close()
      await vite?.close()
      await rm(directory, { recursive: true, force: true })
    }
  },
  120000
)
