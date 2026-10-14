import path from 'node:path'
import {
  copy,
  requireValue,
  fields,
  readJson,
  writeJson,
  SerialQueue,
  problem,
  safeId
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/persona.js').UserPersonaDefinition} UserPersonaDefinition */

/** @typedef {import('../../shared/contracts/characters.js').CharacterDefinition} CharacterDefinition */
/** @typedef {import('../../shared/contracts/characters.js').CharacterCapabilities} CharacterCapabilities */

/**
 * 角色目录模块：统一管理角色配置、对话身份定义与资源解析，是 Node 中唯一的权威目录。
 * 对话身份独立存放于用户数据目录的 personas/<personaId>.json；路径由宿主装配，不由业务调用方指定。
 * 需要：角色配置、对话身份定义和资源引用。
 * 不需要：全局“当前角色”、当前 Chat、生成历史、演出时间线或模型推理实例。
 * 修改目录不自动替换运行中的演出，也不追溯修改已有轮次快照。
 * 保存成功后，后续读取与解析立即可见；本模块更新或失效自己的缓存，依赖资源变化也须使相关解析缓存失效。
 * 页面可重新查询列表；Python 与演出模块只接收任务材料，不各自维护完整角色目录。
 * 人格指向角色时只保存引用，不会立刻存储人格信息；解析时机与 Chat 快照保存由 Conversation 决定，目录不接收 Chat。
 * 调用方只把能力快照中必要的部分交给 Agent、语音和演出模块。
 * 业务实现由宿主注入存储位置与外部能力。
 */
export class CharacterCatalog {
  /**
   * 绑定角色定义存储与模型元数据解析能力，不加载渲染实例。
   * @param {object} options 宿主依赖。
   * @param {string} options.directory 角色目录与人格文件的存储根。
   * @param {(presentation: import('../../shared/contracts/presentation.js').ModelPresentation) => Promise<object>} [options.resolveModel] 解析动作、表情和状态声明。
   */
  constructor({
    directory,
    resolveModel = async () => ({ motions: {}, expressions: {}, states: {} })
  }) {
    this.file = path.join(directory, 'characters.json')
    this.directory = directory
    this.queue = new SerialQueue()
    this.resolveModel = resolveModel
  }
  /** 目录索引与定义一起读取，调用方在 catalog 串行队列内完成比较和发布。 */
  async state() {
    return readJson(this.file, { characters: {}, order: [], personas: [] })
  }
  /** 人格身份必须先通过文件名校验，不能用调用方的 ID 拼接任意路径。 */
  personaFile(id) {
    return path.join(this.directory, 'personas', safeId(id) + '.json')
  }

  /**
   * 按目录顺序列出 AI 角色默认形态的摘要，不包含用户人格、不加载推理模型。
   * 与 Python 旧设计不同：新角色经目录保存后即可出现在后续查询中并用于创建 Chat，不要求重启应用。
   * @returns {Promise<Array<{ id: string, displayName: string, avatar: import('../../shared/contracts/common.js').AssetRef | null }>>} 角色摘要。
   */
  async list() {
    const state = await this.state()
    return state.order.map((id) => {
      const character = state.characters[id]
      const form = character.forms.find((item) => item.id === character.defaultFormId)
      return { id, displayName: form.displayName, avatar: copy(form.avatar) }
    })
  }

  /**
   * 读取角色配置。
   * 不会开始任何语音模型、live2d 等内容的加载。
   * @param {string} characterId 角色身份。
   * @returns {Promise<CharacterDefinition | null>} 独立配置快照。
   */
  async get(characterId) {
    return copy((await this.state()).characters[characterId] ?? null)
  }

  /**
   * 使用 definition.revision 核对并保存角色。防止未预期的覆盖。
   * 版本比较、保存和版本递增必须原子完成；校验模型只属于一个形态及各默认模型指针有效。
   * revision 成功后由目录递增一次，调用方不应当主动递增。
   * 0 仅用于新建且要求该 ID 不存在，首次保存为 1；冲突拒绝，不修改输入对象。
   * @param {CharacterDefinition} definition 已完成修改、仍携带原版本的完整配置。
   * @returns {Promise<CharacterDefinition>} 已保存且携带新版本的独立配置；后续读取与解析可见新配置。
   */
  async save(definition) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      safeId(definition.id)
      requireValue(!['__proto__', 'constructor', 'prototype'].includes(definition.id))
      requireValue(
        definition.revision === (state.characters[definition.id]?.revision ?? 0),
        'revision_conflict'
      )
      requireValue(Array.isArray(definition.forms) && definition.forms.length > 0)
      requireValue(
        new Set(definition.forms.map((form) => form.id)).size === definition.forms.length
      )
      requireValue(definition.forms.some((form) => form.id === definition.defaultFormId))
      const models = definition.forms.flatMap((form) => form.models)
      requireValue(new Set(models.map((model) => model.id)).size === models.length)
      for (const form of definition.forms)
        requireValue(
          form.defaultModelId === null
            ? !form.models.length
            : form.models.some((model) => model.id === form.defaultModelId)
        )
      const next = copy(definition)
      next.revision += 1
      state.characters[next.id] = next
      if (!state.order.includes(next.id)) state.order.push(next.id)
      await writeJson(this.file, state)
      return copy(next)
    })
  }

  /**
   * 解析身份、演出目录与声音配置；只读取描述和资源元数据，不常驻 Live2D 或 TTS 模型。
   * 显式无效形态/模型或模型不属于所选形态时拒绝，省略 formId 使用默认形态；已配置但缺失的可选资源保留诊断。
   * 解析模型映射的 states/on/off 及 stateEffects；无效状态报告问题并从 performances.states 排除，不猜动作名称。
   * 普通动作目录排除状态专用动作，状态目录只返回键和说明；实际布尔值与待执行目标由 Performance 维护。
   * @param {string} characterId 角色身份。
   * @param {{ formId?: string, modelId?: string | null }} options formId 省略时使用默认形态；modelId 省略或为 null 时使用该形态默认模型。
   * @returns {Promise<CharacterCapabilities>} 可供一次编排使用的只读快照。
   */
  async resolveCapabilities(characterId, options) {
    const character = await this.get(characterId)
    requireValue(character, 'character_missing')
    const form = character.forms.find(
      (item) => item.id === (options.formId ?? character.defaultFormId)
    )
    requireValue(form, 'form_missing')
    const modelId = options.modelId ?? form.defaultModelId
    const model = form.models.find((item) => item.id === modelId)
    requireValue(modelId === null || model, 'model_missing')
    const problems = []
    let mapping = { motions: {}, expressions: {}, states: {} }
    if (model) {
      try {
        mapping = await this.resolveModel(model.presentation)
      } catch (error) {
        problems.push(problem(error))
      }
    }
    const states = {}
    const excluded = new Set()
    for (const [key, state] of Object.entries(mapping.states ?? {})) {
      if (!mapping.motions?.[state.on?.motion] || !mapping.motions?.[state.off?.motion]) {
        problems.push({
          code: 'invalid_model_state',
          message: '模型状态动作无效：' + key,
          retryable: false
        })
        continue
      }
      states[key] = state.description ?? key
      excluded.add(state.on.motion)
      excluded.add(state.off.motion)
    }
    function descriptions(entries) {
      return Object.entries(entries ?? {})
        .filter(([key]) => !excluded.has(key))
        .map(([key, value]) => ({ id: key, description: value.description ?? key }))
    }
    return {
      id: character.id,
      revision: character.revision,
      formId: form.id,
      displayName: form.displayName,
      description: form.description,
      avatar: copy(form.avatar),
      themeColor: form.themeColor,
      modelId,
      presentation: copy(model?.presentation ?? null),
      performances: {
        motions: descriptions(mapping.motions),
        expressions: descriptions(mapping.expressions),
        states: Object.entries(states).map(([id, description]) => ({ id, description }))
      },
      voice: copy(form.voice),
      problems
    }
  }

  /**
   * 根据已解析的形态能力和台词情绪，选出完整的语音合成参考材料或无声降级结果。
   * 普通生成用已冻结能力，历史重合成先解析当前原形态能力；本方法不重读目录、不重新推断情绪。
   * @param {CharacterCapabilities} capabilities 一致的形态与声音快照。
   * @param {string | null} emotion 原台词情绪。
   * @returns {Promise<import('../../shared/contracts/characters.js').VoiceResolution>} 完整声音材料或同形态降级结果。
   */
  async resolveVoice(capabilities, emotion) {
    const voice = capabilities.voice
    const reference = voice?.emotions?.[emotion] ?? voice?.defaultReference
    if (!voice || !reference)
      return {
        voice: null,
        selected: 'silent',
        problems: [
          { code: 'voice_unconfigured', message: '当前形态没有可用参考声音。', retryable: false }
        ]
      }
    return {
      voice: {
        gptWeights: copy(voice.gptWeights),
        sovitsWeights: copy(voice.sovitsWeights),
        referenceAudio: copy(reference.audio),
        referenceText: reference.text,
        referenceLanguage: reference.language
      },
      selected: voice.emotions?.[emotion] ? 'emotion' : 'default',
      problems: []
    }
  }

  /**
   * 更新角色的排序。角色排序可能会用于新建对话时的角色列表顺序。
   * @param {string[]} characterIds 完整的，修改排序后的角色 ID。
   * @returns {Promise<void>} 仅更新目录顺序，不改角色定义。
   */
  async setOrder(characterIds) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      requireValue(
        characterIds.length === state.order.length &&
          new Set(characterIds).size === characterIds.length &&
          characterIds.every((id) => state.order.includes(id))
      )
      state.order = copy(characterIds)
      await writeJson(this.file, state)
    })
  }

  /**
   * 删除一个对话身份。
   * @param {string} personaId 对话身份定义。
   * @param {number} expectedRevision 所基于版本（防止误删更新的版本）
   * @returns {Promise<void>} 定义已删除，已有 Chat 的人格快照及头像引用不受影响。
   */
  async deletePersona(personaId, expectedRevision) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      const previous = await this.getPersona(personaId)
      requireValue(previous?.revision === expectedRevision, 'revision_conflict')
      state.personas = state.personas.filter((id) => id !== personaId)
      await writeJson(this.file, state)
    })
  }

  /**
   * 列出对话身份供用户选择；关联角色的摘要使用当前名称与头像，不将摘要保存为身份快照。
   * 角色身份通过其默认形态解析名称和头像；来源角色缺失的身份仍保留入口并报告问题，以便编辑修复，不使整个列表失败。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSummary[]>} 当前身份摘要。
   */
  async listPersonas() {
    const result = []
    for (const id of (await this.state()).personas) {
      try {
        const resolved = await this.resolvePersona(id)
        result.push({
          id,
          displayName: resolved.displayName,
          avatar: resolved.avatar,
          problem: null
        })
      } catch (error) {
        result.push({ id, displayName: null, avatar: null, problem: problem(error) })
      }
    }
    return result
  }

  /**
   * 读取对话身份定义。
   * 如果对话身份是某个角色，那么角色引用将保持引用状态，不会补全为对应角色的 prompt 文本。
   * @param {string} personaId 对话身份编号。
   * @returns {Promise<UserPersonaDefinition | null>} 独立定义快照；身份不存在时为 null。
   */
  async getPersona(personaId) {
    if (!(await this.state()).personas.includes(personaId)) return null
    return readJson(this.personaFile(personaId), null)
  }

  /**
   * 保存可复用的对话身份，支持自定义名称/描述或角色引用。
   * definition.revision 是所基于的版本，0 仅用于新建；版本比较和写入原子完成，成功后递增一次。
   * 关联角色时，不复制名称、描述或头像；来源角色后续更新不递增此定义的 revision。
   * @param {UserPersonaDefinition} definition 完整身份定义；不得同时携带自定义文本与角色引用。
   * @returns {Promise<UserPersonaDefinition>} 已保存且携带新版本的独立定义；冲突拒绝，不修改输入对象。
   */
  async savePersona(definition) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      const previous = await this.getPersona(definition.id)
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      if (definition.source.kind === 'character') fields(definition.source, ['kind', 'characterId'])
      else {
        requireValue(definition.source.kind === 'custom')
        fields(definition.source, ['kind', 'displayName', 'description', 'avatar'])
      }
      const next = copy(definition)
      next.revision += 1
      await writeJson(this.personaFile(next.id), next)
      if (!state.personas.includes(next.id)) {
        state.personas.push(next.id)
        await writeJson(this.file, state)
      }
      return copy(next)
    })
  }

  /**
   * 按调用时的定义解析实际身份，返回实际的对话身份信息。
   * 对角色引用，每次调用时都读取当前瞬间的角色名称、描述和头像。
   * 角色引用采用当前默认形态的名称、描述和头像；返回结果来自一致的定义与角色配置快照。
   * @param {string} personaId 对话身份编号。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSnapshot>} 实际身份快照；身份或来源角色不存在时拒绝，不静默降级为默认身份。
   */
  async resolvePersona(personaId) {
    const definition = await this.getPersona(personaId)
    requireValue(definition, 'persona_missing')
    if (definition.source.kind === 'custom')
      return {
        personaId,
        sourceCharacterId: null,
        displayName: definition.source.displayName,
        description: definition.source.description,
        avatar: copy(definition.source.avatar)
      }
    const character = await this.get(definition.source.characterId)
    requireValue(character, 'character_missing')
    const form = character.forms.find((item) => item.id === character.defaultFormId)
    return {
      personaId,
      sourceCharacterId: character.id,
      displayName: form.displayName,
      description: form.description,
      avatar: copy(form.avatar)
    }
  }
}
