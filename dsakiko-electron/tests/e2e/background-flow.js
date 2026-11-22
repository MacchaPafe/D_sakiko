import { expect, vi } from 'vitest'

/** 通过真实舞台、HTTP 和事件订阅验证下载与预览，仅替换在线图片源。 */
export async function verifyBackgroundDraft(page, backend) {
  const item = {
    name: '放学后的教室',
    filename: 'classroom.png',
    key: 'images/backgrounds/classroom.png',
    sha256: null
  }
  const bytes = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XKZkAAAAASUVORK5CYII=',
    'base64'
  )
  const source = backend.backgrounds.source
  const spies = [
    vi.spyOn(source, 'catalog').mockResolvedValue([item]),
    vi
      .spyOn(source, 'preview')
      .mockResolvedValue('data:image/png;base64,' + bytes.toString('base64')),
    vi.spyOn(source, 'download').mockResolvedValue(bytes)
  ]
  const picker = page.getByRole('dialog', { name: '切换舞台背景' })
  const manager = page.getByRole('dialog', { name: '舞台资源' })
  const backdrop = page.locator('.stage-backdrop')
  try {
    await page.getByRole('button', { name: '切换背景', exact: true }).click()
    await picker.getByRole('searchbox').fill('classroom')
    await picker.getByRole('button', { name: '管理背景' }).click()
    await manager.getByRole('button', { name: '下载', exact: true }).click()
    await manager.getByRole('button', { name: '应用', exact: true }).waitFor()
    expect(await backdrop.count()).toBe(0)
    await manager.getByRole('tab', { name: '服装', exact: true }).click()
    await manager.getByRole('tab', { name: '背景', exact: true }).click()
    await manager.getByRole('button', { name: '关闭舞台资源' }).click()
    expect(await picker.getByRole('searchbox').inputValue()).toBe('classroom')
    const tile = picker.getByRole('button', { name: item.name })
    await tile.hover()
    await expect
      .poll(() => backdrop.evaluateAll((images) => images.some((img) => img.naturalWidth > 0)), {
        timeout: 5000
      })
      .toBe(true)
    await picker.getByRole('heading').hover()
    await expect.poll(() => backdrop.count()).toBe(0)
    await picker.getByRole('searchbox').focus()
    await page.keyboard.press('ArrowDown')
    await tile.press('Enter')
    await picker.waitFor({ state: 'hidden' })
    await backdrop.waitFor()
    return backend.backgrounds.data.items[0].asset
  } finally {
    for (const spy of spies) spy.mockRestore()
  }
}
