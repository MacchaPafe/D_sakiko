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
 * 角色与用户对话身份目录，负责管理角色配置、用户身份（Persona）以及解析角色在对话中可用的能力。
 */
export class CharacterCatalog {
  /**
   * 创建一个角色目录。
   * @param {object} options 创建选项。
   * @param {string} options.directory 角色的数据目录，存储角色配置和对话身份信息。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器。
   * @param {(presentation: import('../../shared/contracts/presentation.js').ModelPresentation) => Promise<object>} [options.resolveModel] 自定义函数，描述如何根据模型文件解析动作、表情和状态等资源。
   */
  constructor({
    directory,
    assets,
    resolveModel = async () => ({ motions: {}, expressions: {}, states: {} })
  }) {
    this.file = path.join(directory, 'characters.json')
    this.directory = directory
    this.queue = new SerialQueue()
    this.resolveModel = resolveModel
    this.assets = assets
    assets.registerOwnerReader('character', (id) => this.get(id))
    assets.registerOwnerReader('persona', (id) => this.getPersona(id))
  }

  /**
   * 读取目录的完整持久化状态（包含角色字典、排序列表和对话身份 ID 列表）。
   */
  async state() {
    return readJson(this.file, { characters: {}, order: [], personas: [] })
  }

  /**
   * 获取指定对话身份的存储文件路径。
   * 拼接路径前先校验 ID 格式，防止传入恶意 ID 导致路径穿越。
   */
  personaFile(id) {
    return path.join(this.directory, 'personas', safeId(id) + '.json')
  }

  /**
   * 按目录顺序获取所有角色的摘要列表（使用各角色的默认形态展示名称和头像）。
   * @returns {Promise<Array<{ id: string, displayName: string, avatar: import('../../shared/contracts/common.js').AssetRef | null }>>} 角色摘要列表。
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
   * 获取指定角色的完整配置。
   * @param {string} characterId 角色 ID。
   * @returns {Promise<CharacterDefinition | null>} 角色配置对象；角色不存在时返回 null。
   */
  async get(characterId) {
    return copy((await this.state()).characters[characterId] ?? null)
  }

  /**
   * 创建或更新一个角色配置。
   * @param {CharacterDefinition} definition 完整的角色配置。新建角色时 revision 应传 0，更新已有角色时需传入所基于的 revision 版本号以防止并发覆盖。
   * @returns {Promise<CharacterDefinition>} 保存成功并递增版本号后的角色配置副本。
   */
  async save(definition) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      safeId(definition.id)
      requireValue(!['__proto__', 'constructor', 'prototype'].includes(definition.id))
      // 核对版本号防止并发修改互相覆盖；由后端统一递增 revision，避免调用方自行计算版本号出错。
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
      await this.assets.assertPersistent(definition)
      const next = copy(definition)
      next.revision += 1
      state.characters[next.id] = next
      if (!state.order.includes(next.id)) state.order.push(next.id)
      await this.assets.commitReferences('character:' + next.id, next, () =>
        writeJson(this.file, state)
      )
      return copy(next)
    })
  }

  /**
   * 删除指定的角色配置。
   * @param {string} characterId 要删除的角色 ID。
   * @param {number} expectedRevision 当前所基于的版本号，用于防止误删已被更新的角色。
   * @returns {Promise<void>}
   */
  async delete(characterId, expectedRevision) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      requireValue(
        state.characters[characterId]?.revision === expectedRevision,
        'revision_conflict'
      )
      delete state.characters[characterId]
      state.order = state.order.filter((id) => id !== characterId)
      await this.assets.commitReferences('character:' + characterId, null, () =>
        writeJson(this.file, state)
      )
    })
  }

  /**
   * 解析指定角色在特定形态和模型下的可用能力（包括人设信息、可用动作/表情/状态列表以及语音配置）。
   * @param {string} characterId 角色 ID。
   * @param {{ formId?: string, modelId?: string | null }} options 解析选项。省略 formId 时使用角色的默认形态；省略 modelId 或为 null 时使用该形态的默认模型。
   * @returns {Promise<CharacterCapabilities>} 角色能力快照及解析过程中发现的问题列表。
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
      // 为什么把状态切换关联的动作加入 excluded：
      // 状态专属的开/关动作需要通过状态切换接口触发，从普通动作列表中剔除可避免 LLM 在日常台词中误选导致模型状态错乱。
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
   * 根据角色能力和台词情绪，选择对应的语音合成参考音频与模型权重。
   * @param {CharacterCapabilities} capabilities 已解析的角色能力快照。
   * @param {string | null} emotion 台词的情绪标签。若该情绪无专属参考音频，则回退使用默认参考音频。
   * @returns {Promise<import('../../shared/contracts/characters.js').VoiceResolution>} 匹配到的语音合成参考材料；若未配置可用声音则返回静音降级结果。
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
   * 更新角色列表的展示排序。
   * @param {string[]} characterIds 重新排序后的完整角色 ID 数组（不能遗漏或重复已有角色）。
   * @returns {Promise<void>}
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
   * 删除一个用户对话身份（Persona）。
   * @param {string} personaId 要删除的对话身份 ID。
   * @param {number} expectedRevision 当前所基于的版本号，用于防止误删已被更新的身份。
   * @returns {Promise<void>}
   */
  async deletePersona(personaId, expectedRevision) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      const previous = await this.getPersona(personaId)
      requireValue(previous?.revision === expectedRevision, 'revision_conflict')
      state.personas = state.personas.filter((id) => id !== personaId)
      await this.assets.commitReferences('persona:' + personaId, null, () =>
        writeJson(this.file, state)
      )
    })
  }

  /**
   * 列出所有可选的用户对话身份摘要。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSummary[]>} 对话身份摘要列表。
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
        // 为什么关联角色缺失时不直接抛出异常：
        // 若某个身份引用的角色被删除，仍保留该身份项并附带错误信息，方便用户在界面上看到并修复或删除它，而不是让整个列表无法加载。
        result.push({ id, displayName: null, avatar: null, problem: problem(error) })
      }
    }
    return result
  }

  /**
   * 获取指定用户对话身份的原始定义。
   * @param {string} personaId 对话身份 ID。
   * @returns {Promise<UserPersonaDefinition | null>} 对话身份定义；不存在时返回 null。
   */
  async getPersona(personaId) {
    if (!(await this.state()).personas.includes(personaId)) return null
    return readJson(this.personaFile(personaId), null)
  }

  /**
   * 创建或更新一个用户对话身份（支持自定义文本或引用某个角色）。
   * @param {UserPersonaDefinition} definition 完整的身份定义。新建时 revision 为 0，更新时传入当前版本号。
   * @returns {Promise<UserPersonaDefinition>} 保存成功并递增版本号后的身份定义副本。
   */
  async savePersona(definition) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      const previous = await this.getPersona(definition.id)
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      // 引用角色时只保存 characterId 而不复制角色的名称和描述，这样角色更新后，新建对话时能自动获取到最新的人设信息。
      if (definition.source.kind === 'character') fields(definition.source, ['kind', 'characterId'])
      else {
        requireValue(definition.source.kind === 'custom')
        fields(definition.source, ['kind', 'displayName', 'description', 'avatar'])
      }
      await this.assets.assertPersistent(definition)
      const next = copy(definition)
      next.revision += 1
      await this.assets.commitReferences('persona:' + next.id, next, async () => {
        await writeJson(this.personaFile(next.id), next)
        if (!state.personas.includes(next.id)) {
          state.personas.push(next.id)
          await writeJson(this.file, state)
        }
      })
      return copy(next)
    })
  }

  /**
   * 解析指定对话身份的实际名称、人设描述和头像（若身份引用了某个角色，则提取该角色当前默认形态的信息）。
   * @param {string} personaId 对话身份 ID。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSnapshot>} 解析后的实际身份信息快照。
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
