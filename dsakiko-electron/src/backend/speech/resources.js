import { requireValue } from '../core/support.js'

/** 将 GPT-SoVITS 资源引用解析为本机路径，并保护进程仍在使用的文件。 */
export class SpeechResources {
  constructor({ assets, settings }) {
    Object.assign(this, { assets, settings })
  }

  /**
   * 取得本次启动的文件快照；不创建目录视图，也不复制模型。
   * @returns {Promise<object>} 启动配置、显式文件清单和进程引用回调。
   */
  async acquire(options = {}) {
    const { startup, files, owner } = await this.settings.acquireSpeechResources(options)
    const releaseResources = (confirmedPid = null) =>
      this.assets.releaseProcessOwner(owner, confirmedPid)
    const resolve = async (value) => {
      if (value.id) {
        const info = await this.assets.inspect(value)
        requireValue(info.purpose === 'model' && info.kind === 'file', 'invalid_resource_manifest')
        return this.assets.localPath(value)
      }
      const entries = []
      for (const [name, child] of Object.entries(value)) entries.push([name, await resolve(child)])
      return Object.fromEntries(entries)
    }
    try {
      let resolved
      if (options.allArchitectures) {
        resolved = { shared: await resolve(files.shared), versions: {} }
        for (const [version, entries] of Object.entries(files.versions)) {
          try {
            resolved.versions[version] = await resolve(entries)
          } catch (error) {
            this.assets.reportMaintenance(error)
          }
        }
        requireValue(
          Object.keys(resolved.versions).length > 0,
          'missing_resources',
          '可用的 GPT-SoVITS 架构资源已离线，请重新准备。'
        )
      } else resolved = await resolve(files)
      return {
        ...startup,
        gptSovits: { ...startup.gptSovits, resources: resolved },
        releaseResources,
        bindResources: (pid, group) => this.assets.bindOwnerProcess(owner, pid, group)
      }
    } catch (error) {
      await releaseResources()
      throw error
    }
  }
}
