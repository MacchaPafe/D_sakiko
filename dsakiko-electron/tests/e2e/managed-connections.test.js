import { generateKeyPairSync } from 'node:crypto'
import { mkdtemp, writeFile, rm, unlink, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from 'vitest'
import { _electron as electron } from 'playwright-core'

test('在设置表单导入 Vertex 后删除源文件，并从订阅账号目录手选模型', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsakiko-managed-ui-'))
  const appDir = join(directory, 'app')
  let app
  try {
    await mkdir(appDir)
    const file = join(directory, 'test-service-account.json')
    const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
    await writeFile(
      file,
      JSON.stringify({
        type: 'service_account',
        project_id: 'project-test',
        client_email: 'test@project-test.iam.gserviceaccount.com',
        private_key: privateKey.export({ type: 'pkcs8', format: 'pem' })
      })
    )
    const profile = {
      id: 'test-account',
      hostId: 'urn:uuid:test',
      email: 'test@example.com',
      models: [{ id: 'model-one', label: '订阅模型一', efforts: ['low'] }],
      tokens: { refresh_token: 'test-only' }
    }
    await writeFile(join(appDir, 'chatgpt-accounts.json'), JSON.stringify({ profiles: [profile] }))
    const env = {
      ...process.env,
      DSAKIKO_DATA_DIR: appDir,
      DSAKIKO_CACHE_DIR: join(directory, 'cache'),
      DSAKIKO_RESOURCE_LIBRARY_DIR: join(directory, 'library')
    }
    delete env.ELECTRON_RUN_AS_NODE
    delete env.ELECTRON_RENDERER_URL
    app = await electron.launch({
      args: [
        fileURLToPath(new URL('../../', import.meta.url)),
        '--user-data-dir=' + join(directory, 'desktop')
      ],
      env
    })
    const page = await app.firstWindow()
    await app.evaluate(
      ({ dialog, ipcMain, shell }, { file, profile }) => {
        shell.openExternal = async (url) => {
          globalThis.vertexGuideUrl = url
        }
        dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] })
        // 文件选择走正式 IPC。账号目录用可控的登录态，避免桌面测试启动外部授权。
        ipcMain.removeHandler('host:chatgpt-account')
        const native = { id: 'native-account', label: '桌面账号', models: [] }
        const legacy = {
          id: profile.id,
          label: profile.email,
          connected: true,
          models: profile.models,
          fromLegacy: true
        }
        let found = false
        ipcMain.handle('host:chatgpt-account', (_event, action) => {
          if (action === 'models') return { result: profile.models }
          if (action === 'discover') {
            found = true
            return { result: [legacy] }
          }
          return { result: found ? [native, legacy] : [native] }
        })
      },
      { file, profile }
    )
    page.setDefaultTimeout(12000)
    await page.getByRole('button', { name: '快速开始', exact: true }).click()
    await page.getByRole('button', { name: '跳过，直接开始', exact: true }).click()
    await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
    await page.keyboard.press('ControlOrMeta+,')
    const panel = page.getByRole('dialog', { name: '设置' })
    const add = panel.getByRole('button', { name: '新建连接', exact: true })
    async function provider(label) {
      await add.click()
      await panel.getByRole('combobox', { name: '服务商' }).click()
      await panel.getByRole('option', { name: label, exact: true }).click()
    }
    await provider('Google Cloud Vertex AI')
    await panel.getByRole('button', { name: '如何获取服务账号 JSON' }).click()
    await expect
      .poll(() => app.evaluate(() => globalThis.vertexGuideUrl))
      .toBe('https://docs.cloud.google.com/iam/docs/keys-create-delete?hl=zh-cn')
    const model = panel.getByPlaceholder('gemini-4-argon')
    expect(await model.inputValue()).toBe('')
    await panel.getByRole('button', { name: '选择服务账号 JSON' }).click()
    await panel.getByText('test@project-test.iam.gserviceaccount.com · project-test').waitFor()
    await unlink(file)
    await model.fill('gemini-4-argon')
    const save = panel.getByRole('button', { name: '保存连接', exact: true })
    await save.click()
    const vertex = panel.locator('article').filter({ hasText: 'Google Cloud Vertex AI' })
    await vertex.getByRole('button', { name: '编辑' }).click()
    await panel.getByText('手动编辑服务账号').click()
    expect(await panel.getByLabel('服务账号私钥').inputValue()).toBe('')
    expect(await panel.getByLabel('项目 ID').inputValue()).toBe('project-test')
    await panel.getByRole('button', { name: '大模型连接', exact: true }).last().click()
    await provider('ChatGPT 订阅')
    const account = panel.getByRole('combobox', { name: 'ChatGPT 账号' })
    const discover = panel.getByRole('button', { name: '查找之前的账号' })
    expect(await account.inputValue()).toBe('new')
    await discover.click()
    await expect.poll(() => account.inputValue()).toBe(profile.id)
    await account.selectOption('native-account')
    await discover.click()
    await panel.getByText('已找到之前的账号。').waitFor()
    expect(await account.inputValue()).toBe('native-account')
    await panel.getByRole('button', { name: '大模型连接', exact: true }).last().click()
    await provider('ChatGPT 订阅')
    await expect.poll(() => account.inputValue()).toBe(profile.id)
    const choice = panel.getByRole('combobox', { name: '模型 ID 1' })
    expect(await choice.inputValue()).toBe('')
    await choice.selectOption('model-one')
    await save.click()
    await panel.getByRole('heading', { name: 'ChatGPT 订阅', exact: true }).waitFor()
    expect(await panel.locator('article').count()).toBe(2)
  } finally {
    await app?.close()
    await rm(directory, { recursive: true, force: true })
  }
}, 60000)
