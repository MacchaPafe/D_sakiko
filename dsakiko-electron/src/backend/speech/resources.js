import path from 'node:path'
import { mkdir, symlink, copyFile, rm } from 'node:fs/promises'
import { copy, requireValue, uuid } from '../core/support.js'
import {
  validSpeechResources,
  validSpeechResourcePath
} from '../../shared/contracts/speech-resources.js'

/** 逐文件资源清单与仍要求目录布局的 Python 加载器之间的适配。 */
export class SpeechResources {
  constructor({ assets, settings, cacheRoot }) {
    Object.assign(this, { assets, settings, cacheRoot })
  }

  /**
   * 登记应用资源库中已经准备好的文件，并将引用提交给全局设置。
   * 这是可信后端入口，参数没有任意绝对路径；迎新扫描和外部文件导入另行实现。
   * @param {{shared: string[], versions: Record<string, string[]>}} manifest 基础文件路径清单。
   * @param {object} [options] 设置版本检查选项。
   * @returns {Promise<object>} 保存后的全局设置。
   */
  async registerDirectory(manifest, options = {}) {
    requireValue(
      manifest &&
        Array.isArray(manifest.shared) &&
        manifest.versions &&
        typeof manifest.versions === 'object' &&
        !Array.isArray(manifest.versions),
      'invalid_resource_manifest'
    )
    const names = [...manifest.shared]
    for (const files of Object.values(manifest.versions)) {
      requireValue(Array.isArray(files), 'invalid_resource_manifest')
      names.push(...files)
    }
    requireValue(names.every(validSpeechResourcePath), 'invalid_resource_manifest')
    const probe = (files) => Object.fromEntries(files.map((name) => [name, { id: 'validation' }]))
    requireValue(
      validSpeechResources({
        shared: probe(manifest.shared),
        versions: Object.fromEntries(
          Object.entries(manifest.versions).map(([version, files]) => [version, probe(files)])
        )
      }),
      'invalid_resource_manifest'
    )
    manifest = copy(manifest)
    const owner = await this.assets.createTemporaryOwner('registration')
    try {
      const refs = await this.assets.registerModelFiles(names, owner)
      function selected(files) {
        return Object.fromEntries(files.map((file) => [file, refs[file]]))
      }
      const resources = {
        shared: selected(manifest.shared),
        versions: Object.fromEntries(
          Object.entries(manifest.versions).map(([version, files]) => [version, selected(files)])
        )
      }
      return await this.settings.set('speechStartup.resources', resources, options)
    } finally {
      await this.assets.releaseWhenPossible(owner)
    }
  }

  /**
   * 创建本次进程的目录视图。普通文件仍各自登记；视图属于缓存，不能反向写入模型文件。
   * @returns {Promise<object>} 启动配置、根目录与绑定/释放进程引用的回调。
   */
  async acquire() {
    const { startup, files, owner } = await this.settings.acquireSpeechResources()
    const resourceRoot = path.join(this.cacheRoot, 'speech-resources', uuid())
    const releaseResources = async (confirmedPid = null) => {
      if (!(await this.assets.releaseProcessOwner(owner, confirmedPid))) return
      // 确认进程树已经结束后才拆除视图，不能仅以 HTTP 父进程退出为依据。
      await rm(resourceRoot, { recursive: true, force: true }).catch((error) =>
        this.assets.reportMaintenance(error)
      )
    }
    try {
      for (const [name, ref] of Object.entries(files)) {
        const info = await this.assets.inspect(ref)
        requireValue(info.purpose === 'model' && info.kind === 'file', 'invalid_resource_manifest')
        const source = await this.assets.localPath(ref)
        const target = path.join(resourceRoot, name)
        await mkdir(path.dirname(target), { recursive: true })
        // 这里只生成运行时视图，不改变导入策略。无创建符号链接权限时使用缓存副本。
        try {
          await symlink(source, target, 'file')
        } catch (error) {
          if (!['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) throw error
          await copyFile(source, target)
        }
      }
      return {
        ...startup,
        resourceRoot,
        releaseResources,
        bindResources: (pid, group) => this.assets.bindOwnerProcess(owner, pid, group)
      }
    } catch (error) {
      await releaseResources()
      throw error
    }
  }
}
