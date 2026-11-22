import { expect } from 'vitest'

/** @param {import('playwright-core').Page} page 单角色草稿所在的 Electron 窗口。 @returns {Promise<void>} 验证正式页面内的绑定、独立复制和草稿交接。 */
export async function verifyPersonaDraft(page) {
  const composer = page.getByRole('textbox', { name: '消息' })
  await composer.fill('身份选择保留草稿')
  await page.getByRole('button', { name: '我的身份 · 无身份' }).click()
  const picker = page.getByRole('dialog', { name: '我的身份' })
  await picker.getByRole('button', { name: '管理身份' }).click()
  const manager = page.getByRole('dialog', { name: '角色管理' })
  expect(await manager.getByRole('tab', { name: '对话身份' }).getAttribute('aria-selected')).toBe(
    'true'
  )
  await manager.getByRole('tab', { name: '角色', exact: true }).click()
  const name = manager.getByLabel('角色名称', { exact: true })
  const originalName = await name.inputValue()
  await name.fill(originalName + ' ')
  await manager.getByRole('button', { name: '用作对话身份' }).click()
  await manager.getByRole('button', { name: '创建身份' }).click()
  await manager.getByRole('tab', { name: '角色', exact: true }).click()
  await manager.getByRole('button', { name: '用作对话身份' }).click()
  const identityList = manager.getByRole('navigation', { name: '管理的对话身份' })
  await expect.poll(() => identityList.getByRole('button').count()).toBe(1)
  const linkedName = await identityList.getByRole('button').getAttribute('aria-label')
  await manager.getByRole('button', { name: '复制为自定义身份' }).click()
  await manager.getByLabel('身份名称').fill('同桌')
  await manager.getByLabel('身份描述').fill('一起排练')
  await manager.getByRole('button', { name: '创建身份' }).click()
  await identityList.getByRole('button', { name: '同桌', exact: true }).waitFor()
  const description = manager.getByLabel('身份描述')
  const release = Promise.withResolvers()
  let writing = false
  await page.route(
    '**/api/v1/call/characters/savePersona',
    async (route) => {
      writing = true
      await release.promise
      await route.continue()
    },
    { times: 1 }
  )
  try {
    await description.fill('排练伙伴')
    await manager.getByText('等待自动保存', { exact: true }).waitFor()
    await expect.poll(() => writing, { timeout: 10_000 }).toBe(true)
    await description.fill('一起排练的同学')
    await identityList.getByRole('button', { name: linkedName, exact: true }).click()
  } finally {
    release.resolve()
  }
  await manager.getByRole('button', { name: '复制为自定义身份' }).waitFor()
  await identityList.getByRole('button', { name: '同桌', exact: true }).click()
  await expect.poll(() => manager.getByLabel('身份描述').inputValue()).toBe('一起排练的同学')
  await manager.getByLabel('身份名称').fill('')
  await manager.getByRole('tab', { name: '角色', exact: true }).click()
  await manager.getByRole('alert').getByText('请填写身份名称。').waitFor()
  expect(await manager.getByLabel('身份名称').inputValue()).toBe('')
  await manager.getByLabel('身份名称').fill('同桌好友')
  await page.keyboard.press('Escape')
  await manager.waitFor({ state: 'hidden' })
  await picker.getByRole('button', { name: '同桌好友', exact: true }).waitFor()
  await picker.getByRole('button', { name: '管理身份' }).click()
  await identityList.getByRole('button', { name: '同桌好友', exact: true }).click()
  await manager.getByLabel('身份名称').fill('同桌')
  await manager.getByRole('button', { name: '添加身份' }).click()
  await manager.getByLabel('身份名称').fill('未创建的身份')
  await identityList.getByRole('button', { name: '同桌', exact: true }).click()
  expect(await identityList.getByRole('button', { name: '未创建的身份' }).count()).toBe(0)
  await manager.getByRole('button', { name: '添加身份' }).click()
  await manager.getByLabel('身份名称').fill('另一个未创建的身份')
  await manager.getByRole('tab', { name: '角色', exact: true }).click()
  await manager.getByRole('tab', { name: '对话身份' }).click()
  expect(await identityList.getByRole('button').count()).toBe(2)
  await manager.getByRole('button', { name: '关闭角色管理' }).click()
  await picker.getByRole('button', { name: '同桌', exact: true }).click()
  await picker.getByText('一起排练的同学').waitFor()
  await picker.getByRole('button', { name: '确认选择' }).click()
  await page.getByRole('button', { name: '我的身份 · 同桌' }).waitFor()
  expect(await composer.inputValue()).toBe('身份选择保留草稿')
  await page.getByRole('button', { name: '我的身份 · 同桌' }).click()
  await picker.getByRole('button', { name: '管理身份' }).click()
  await manager.getByRole('button', { name: '添加身份' }).click()
  await manager.getByLabel('身份名称').fill('同桌')
  await manager.getByRole('button', { name: '创建身份' }).click()
  await manager.getByRole('button', { name: '同桌 (2)', exact: true }).waitFor()
  await manager.getByRole('button', { name: '删除身份', exact: true }).click()
  await manager.getByRole('button', { name: '确认删除身份' }).click()
  await manager.getByRole('button', { name: '同桌 (2)', exact: true }).waitFor({ state: 'hidden' })
  await manager.getByRole('button', { name: '关闭角色管理' }).click()
  expect(
    await picker.getByRole('button', { name: '同桌', exact: true }).getAttribute('aria-pressed')
  ).toBe('true')
  await picker.getByRole('button', { name: '确认选择' }).click()
}
