import { mkdtemp, writeFile, readdir, mkdir, symlink, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { expect, test } from 'vitest'
import { collectDownloadCache } from '../../src/backend/setup/cache.js'

test('缓存回收保护可恢复任务和共享哈希，清除终态及孤儿文件，不跟随链接', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'setup-cache-'))
  try {
    const hashes = 'abcdef'.split('').map((value) => value.repeat(64))
    for (const hash of hashes) {
      await writeFile(path.join(root, hash), 'complete')
      await writeFile(path.join(root, hash + '.part'), 'partial')
    }
    await mkdir(path.join(root, '0'.repeat(64)))
    await symlink('/tmp', path.join(root, '1'.repeat(64)))
    await writeFile(path.join(root, 'unrelated'), 'keep')
    const jobs = ['planned', 'paused', 'failed', 'succeeded', 'cancelled'].map((status, index) => ({
      id: String(index),
      kind: 'download',
      status,
      selected: [{ files: [{ sha256: hashes[index] }] }]
    }))
    jobs.push({
      id: 'shared',
      kind: 'download',
      status: 'running',
      selected: [{ files: [{ sha256: hashes[3] }] }]
    })
    await collectDownloadCache(root, jobs)
    let files = await readdir(root)
    for (const hash of hashes.slice(0, 4)) {
      expect(files).toContain(hash)
      expect(files).toContain(hash + '.part')
    }
    for (const hash of hashes.slice(4)) expect(files).not.toContain(hash)
    jobs.at(-1).status = 'succeeded'
    await collectDownloadCache(root, jobs, new Set(['shared']))
    expect(await readdir(root)).toContain(hashes[3])
    await collectDownloadCache(root, jobs)
    files = await readdir(root)
    expect(files).not.toContain(hashes[3])
    expect(files).toContain('unrelated')
    expect(files).toContain('0'.repeat(64))
    expect(files).toContain('1'.repeat(64))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('运行环境提交后才清理安装材料，暂停保留、取消释放、维护补做且不跟随链接', async () => {
  const { createCoreBackend } = await import('../../src/backend/create-core-backend.js')
  const { writeJson } = await import('../../src/backend/core/support.js')
  const { randomUUID } = await import('node:crypto')
  const root = await mkdtemp(path.join(tmpdir(), 'runtime-cache-'))
  const options = {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache'),
    scanReminders: false
  }
  const backend = await createCoreBackend(options)
  try {
    const id = randomUUID()
    const directory = path.join(options.resourceLibraryRoot, 'runtimes', id, 'gsv.runtime')
    async function materials() {
      for (const name of ['wheelhouse', '.uv-cache', 'python', 'env']) {
        await mkdir(path.join(directory, name), { recursive: true })
        await writeFile(path.join(directory, name, 'keep-or-remove'), name)
      }
    }
    await materials()
    const job = { id, kind: 'download', status: 'paused', completed: [] }
    const record = { id: 'gsv.runtime', jobId: id, status: 'ready', directory, refs: {} }
    await writeJson(path.join(options.appDataRoot, 'setup/installations.json'), {
      'gsv.runtime': record
    })
    await backend.setup.cleanRuntimeMaterials(job)
    expect(await readdir(directory)).toContain('wheelhouse')
    job.completed.push('gsv.runtime')
    await backend.setup.cleanRuntimeMaterials(job)
    expect((await readdir(directory)).sort()).toEqual(['env', 'python'])
    await materials()
    await writeJson(path.join(options.appDataRoot, 'setup/jobs', id + '.json'), {
      ...job,
      status: 'succeeded',
      phase: 'complete',
      selected: []
    })
    await backend.setup.cleanDownloadCache()
    expect(await readdir(directory)).not.toContain('.uv-cache')
    await materials()
    job.completed = []
    job.status = 'cancelled'
    backend.setup.running.set(id, {})
    await backend.setup.cleanRuntimeMaterials(job)
    expect(await readdir(directory)).toContain('wheelhouse')
    backend.setup.running.delete(id)
    await backend.setup.cleanRuntimeMaterials(job)
    expect(await readdir(directory)).not.toContain('wheelhouse')
    await materials()
    const external = path.join(root, 'external')
    await mkdir(external)
    await rm(path.join(directory, 'wheelhouse'), { recursive: true })
    await symlink(external, path.join(directory, 'wheelhouse'))
    await writeFile(path.join(external, 'untouched'), 'keep')
    await backend.setup.cleanRuntimeMaterials(job)
    expect(await readdir(external)).toEqual(['untouched'])
  } finally {
    await backend.close()
    await rm(root, { recursive: true, force: true })
  }
})
