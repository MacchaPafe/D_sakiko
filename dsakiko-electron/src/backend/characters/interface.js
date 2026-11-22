import { extractAssetReferences } from '../resources/references.js'
import path from 'node:path'
import { pinOutfit } from './outfit-selection.js'
import { missingVoiceMaterials } from '../../shared/reply-voice.js'
import { bangDreamCharacters } from './bang-dream.js'
import {
  copy,
  requireValue,
  fields,
  fingerprint,
  readJson,
  writeJson,
  SerialQueue,
  problem,
  safeId,
  SnapshotBus
} from '../core/support.js'

/** @typedef {import('../../shared/contracts/persona.js').UserPersonaDefinition} UserPersonaDefinition */

/** @typedef {import('../../shared/contracts/characters.js').CharacterDefinition} CharacterDefinition */
/** @typedef {import('../../shared/contracts/characters.js').CharacterCapabilities} CharacterCapabilities */

/**
 * 从模型资源入口或来源推断 Live2D 版本，不依赖持久化字段。
 * @param {import('../../shared/contracts/characters.js').CharacterModel} model 已安装的模型配置。
 * @param {object | null} assetState 资源库索引状态。
 * @returns {'v2' | 'v3' | undefined} 模型版本；无法识别时返回 undefined。
 */
function inferModelVersion(model, assetState) {
  const assetId = model?.presentation?.model?.id
  if (assetId && assetState) {
    const candidates = [assetState.locations?.[assetId]?.entry, assetState.assets?.[assetId]?.name]
    for (const candidate of candidates) {
      if (typeof candidate !== 'string') continue
      if (/model3\.json$/iu.test(candidate)) return 'v3'
      if (/model\.json$/iu.test(candidate)) return 'v2'
    }
  }
  if (model?.source?.kind === 'modelscope') return 'v3'
  if (model?.source?.kind === 'bestdori') return 'v2'
  return undefined
}

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
    this.changes = new SnapshotBus()
    this.resolveModel = resolveModel
    this.assets = assets
    this.removingForms = new Map()
    this.removingOutfits = new Set()
    assets.registerOwnerReader('character', (id) => this.readRaw(id))
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
   * 按目录顺序获取所有角色的摘要列表（使用各角色的默认形态展示名称、头像与主题色）。
   * @returns {Promise<Array<{ id: string, defaultFormId: string, name: string, displayName: string, avatar: import('../../shared/contracts/common.js').AssetRef | null, themeColor: string | null, formThemeColors: Record<string, string | null>, bangDreamId?: string | null }>>} 角色摘要列表，默认形态编号可用于创建明确的身份引用。
   */
  async list() {
    const state = await this.state()
    return state.order.map((id) => {
      const character = state.characters[id]
      const form = character.forms.find((item) => item.id === character.defaultFormId)
      const formThemeColors = Object.fromEntries(
        character.forms.map((item) => [item.id, item.themeColor ?? null])
      )
      return {
        id,
        defaultFormId: form.id,
        forms: character.forms.map(({ id, displayName }) => ({ id, displayName })),
        name: character.name ?? form.displayName,
        displayName: form.displayName,
        avatar: copy(form.avatar),
        themeColor: form.themeColor ?? null,
        formThemeColors,
        ...(character.bangDreamId !== undefined && { bangDreamId: character.bangDreamId })
      }
    })
  }

  /**
   * 读取指定角色的原始持久化配置（不包含派生字段）。
   * @param {string} characterId 角色 ID。
   * @returns {Promise<CharacterDefinition | null>} 原始角色配置对象；角色不存在时返回 null。
   */
  async readRaw(characterId) {
    const { characters } = await this.state()
    return Object.hasOwn(characters, characterId) ? copy(characters[characterId]) : null
  }

  /**
   * 为角色各形态的已安装模型附加派生的 Live2D 版本信息。
   * @param {CharacterDefinition} character 角色配置副本。
   * @returns {Promise<CharacterDefinition>} 附加版本信息后的角色配置。
   */
  async attachModelVersions(character) {
    const hasModels = character.forms.some((form) => form.models.length > 0)
    if (!hasModels) return character
    const assetState =
      typeof this.assets?.state === 'function' ? await this.assets.state().catch(() => null) : null
    for (const form of character.forms) {
      for (const model of form.models) {
        const version = inferModelVersion(model, assetState)
        if (version) model.version = version
        else delete model.version
      }
    }
    return character
  }

  /**
   * 获取指定角色的完整配置。
   * @param {string} characterId 角色 ID。
   * @returns {Promise<CharacterDefinition | null>} 角色配置对象；角色不存在时返回 null。
   */
  async get(characterId) {
    const character = await this.readRaw(characterId)
    return character ? await this.attachModelVersions(character) : null
  }

  /** 在角色增删后通知依赖当前目录的对话状态。 */
  observeMembership(listener) {
    return this.changes.subscribe('membership', listener)
  }

  /** @param {(characterId: string) => void} listener 角色资料变化观察者。 @returns {() => void} 取消观察；编辑也会触发，以便刷新通知名称与能力。 */
  observeChanges(listener) {
    return this.changes.subscribe('definition', listener)
  }

  /** 将新轮次的保存与角色删除串行化。 */
  async withExistingCharacters(ids, operation, selections = {}) {
    return this.queue.run('catalog', async () => {
      const { characters } = await this.state()
      requireValue(
        ids.every((id) => Object.hasOwn(characters, id)),
        'character_missing',
        '对话关联的角色已不存在。'
      )
      for (const id of ids) {
        const selection = selections[id]
        if (!selection) continue
        const form = characters[id].forms.find(({ id: formId }) => formId === selection.formId)
        this.assertFormAvailable(id, form)
        for (const [formId, modelId] of Object.entries(selection.modelOverrides ?? {})) {
          const owner = characters[id].forms.find((item) => item.id === formId)
          this.assertFormAvailable(id, owner)
          if (modelId != null) this.assertOutfitAvailable(id, owner, modelId)
        }
      }
      return operation()
    })
  }

  /** 导入与分支在入库时固定仍可用的人格和服装，保留已删除角色的只读历史。 */
  async withRestoredSelections(ids, selections, operation) {
    return this.queue.run('catalog', async () => {
      const { characters } = await this.state()
      const restored = { ...selections }
      for (const id of ids) {
        const role = characters[id]
        if (!role) continue
        requireValue(!this.removingForms.has(id), 'character_busy', '正在删除人格，请稍后重试。')
        const selection = selections[id]
        const overrides = {}
        for (const form of role.forms) {
          const modelId = selection?.modelOverrides?.[form.id]
          if (form.models.some((model) => model.id === modelId)) {
            this.assertOutfitAvailable(id, form, modelId)
            overrides[form.id] = modelId
          }
        }
        const selected = role.forms.find((form) => form.id === selection?.formId)
        restored[id] = pinOutfit(role, { formId: selected?.id, modelOverrides: overrides })
        const activeForm = role.forms.find((form) => form.id === restored[id].formId)
        const modelId = restored[id].modelOverrides[activeForm.id]
        if (modelId != null) this.assertOutfitAvailable(id, activeForm, modelId)
      }
      return operation(restored)
    })
  }

  assertFormAvailable(id, form) {
    requireValue(
      form && this.removingForms.get(id) !== form.id,
      'form_missing',
      '该人格已不可用，请重新选择。'
    )
  }

  assertOutfitAvailable(characterId, form, modelId) {
    requireValue(
      form?.models.some((model) => model.id === modelId) &&
        !this.removingOutfits.has(JSON.stringify([characterId, form.id, modelId])),
      'model_missing',
      '服装已不可用，请重新选择。'
    )
  }

  /**
   * 禁止在删除跨存档引用期间承诺新的服装选择；现有演出资源仍由各自持有者保护。
   * @template T
   * @param {{characterId: string, formId: string}} target 所属角色形态。
   * @param {string} modelId 待删服装。
   * @param {() => Promise<T>} operation 持有角色业务锁的删除作业。
   * @returns {Promise<T>} 删除作业的结果。
   */
  async withRemovingOutfit(target, modelId, operation) {
    const key = JSON.stringify([target.characterId, target.formId, modelId])
    await this.queue.run('catalog', () => this.removingOutfits.add(key))
    try {
      return await operation()
    } finally {
      this.removingOutfits.delete(key)
    }
  }

  /**
   * 创建或更新一个角色配置。
   * @param {CharacterDefinition} definition 完整的角色配置。新建角色时 revision 应传 0，更新已有角色时需传入所基于的 revision 版本号以防止并发覆盖。
   * @param {string[]} [previewedThemeFormIds] 已在编辑器预览过归属配色的形态，提交用户最后选定的颜色（包括清空）。
   * @returns {Promise<CharacterDefinition>} 保存成功并递增版本号后的角色配置副本。
   */
  async save(definition, previewedThemeFormIds = []) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      safeId(definition.id)
      requireValue(
        !this.removingForms.has(definition.id),
        'character_busy',
        '正在删除人格，请稍后重试。'
      )
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
      for (const form of definition.forms) {
        if (form.replyVoiceEnabled === undefined) continue
        requireValue(
          typeof form.replyVoiceEnabled === 'boolean' &&
            form.replyVoiceEnabled === Boolean(form.voice),
          'invalid_voice_configuration'
        )
        if (form.replyVoiceEnabled)
          requireValue(
            !missingVoiceMaterials(form.voice).length,
            'invalid_voice_configuration',
            '启用回复语音前，请先补齐模型、参考录音、文本和语言。'
          )
      }
      await this.assets.assertPersistent(definition)
      const next = copy(definition)
      // 派生版本字段只用于界面展示，不写入 characters.json。
      for (const form of next.forms) {
        for (const model of form.models) delete model.version
      }
      requireValue(
        next.bangDreamId === undefined ||
          next.bangDreamId === null ||
          bangDreamCharacters.has(next.bangDreamId),
        'invalid_bang_dream_character',
        '请选择目录中的 BanG Dream! 角色。'
      )
      requireValue(Array.isArray(previewedThemeFormIds))
      const previous = state.characters[next.id]
      requireValue(
        ['character', 'form'].includes(next.selfNameMode ?? 'character'),
        'invalid_self_name_mode'
      )
      requireValue(
        !previous || previous.forms.every((old) => next.forms.some((form) => form.id === old.id)),
        'form_removal_required',
        '请使用删除人格操作。'
      )
      const formNames = new Set()
      for (const form of next.forms) {
        safeId(form.id)
        requireValue(!['__proto__', 'constructor', 'prototype'].includes(form.id))
        form.displayName =
          form.displayName?.trim() ||
          (next.forms.length === 1 && next.selfNameMode !== 'form' ? next.name?.trim() : '')
        requireValue(
          form.displayName && !formNames.has(form.displayName),
          'invalid_form_name',
          '人格名称不能为空或重复。'
        )
        formNames.add(form.displayName)
      }
      const preset = bangDreamCharacters.get(next.bangDreamId)
      if (preset) {
        const relinked = previous?.bangDreamId !== next.bangDreamId
        const existingForms = new Set(previous?.forms.map((form) => form.id))
        for (const form of next.forms) {
          if (relinked) {
            // 旧版导入、内置安装和服装页直接关联时，全部形态一次性采用预设色。
            if (!previewedThemeFormIds.includes(form.id)) form.themeColor = preset.themeColor
          } else if (!existingForms.has(form.id) && !previewedThemeFormIds.includes(form.id)) {
            form.themeColor ??= preset.themeColor
          }
        }
      }
      const defaultForm = next.forms.find((form) => form.id === next.defaultFormId)
      requireValue(
        next.name === undefined || typeof next.name === 'string',
        'invalid_character_name'
      )
      next.name = (next.name ?? state.characters[next.id]?.name ?? defaultForm.displayName).trim()
      requireValue(next.name.length > 0, 'invalid_character_name', '角色名称不能为空。')
      for (const other of Object.values(state.characters)) {
        const otherName =
          other.name ?? other.forms.find((form) => form.id === other.defaultFormId).displayName
        requireValue(
          other.id === next.id || otherName.trim() !== next.name,
          'duplicate_character_name',
          '角色名称不能重复。'
        )
      }
      next.revision += 1
      state.characters[next.id] = next
      if (!state.order.includes(next.id)) state.order.push(next.id)
      await this.assets.commitReferences('character:' + next.id, next, () =>
        writeJson(this.file, state)
      )
      if (definition.revision === 0) this.changes.emit('membership', next.id)
      this.changes.emit('definition', next.id)
      return await this.attachModelVersions(copy(next))
    })
  }

  formDeletionStamp(role, formId, personas, chats) {
    return fingerprint([
      role.id,
      formId,
      role.revision,
      personas.map(({ id, revision }) => [id, revision]),
      chats.map(({ id }) => id)
    ])
  }

  /** @param {string} characterId 来源角色编号。 @param {string} formId 来源人格编号。 @returns {Promise<UserPersonaDefinition[]>} 目录顺序下绑定到该人格的身份。 */
  async formPersonas(characterId, formId) {
    const result = []
    for (const id of (await this.state()).personas) {
      const persona = await this.getPersona(id)
      const source = persona?.source
      if (
        source?.kind === 'character' &&
        source.characterId === characterId &&
        source.formId === formId
      )
        result.push(persona)
    }
    return result
  }

  /** @param {string} characterId 角色。 @param {string} formId 待删人格。 @returns {Promise<object>} 需接替的对话及转为独立身份的绑定。 */
  async previewFormDeletion(characterId, formId) {
    const role = await this.get(characterId)
    const form = role?.forms.find((entry) => entry.id === formId)
    requireValue(form && role.forms.length > 1, 'form_required', '角色至少要保留一个人格。')
    const personas = await this.formPersonas(characterId, formId)
    const usage = await this.formUsage({ characterId, formId, dryRun: true })
    return {
      ...usage,
      personas: personas.map(({ id }) => ({ id, name: form.displayName })),
      stamp: this.formDeletionStamp(role, formId, personas, usage.chats)
    }
  }

  /** @param {string} characterId 角色。 @param {string} formId 待删人格。 @param {string} replacementId 接替人格。 @param {string} stamp 确认时的影响摘要。 @returns {Promise<void>} 完成交接并删除人格。 */
  async deleteForm(characterId, formId, replacementId, stamp) {
    await this.queue.run('catalog', async () => {
      const role = await this.readRaw(characterId)
      requireValue(
        formId !== replacementId &&
          role?.forms.some((item) => item.id === formId) &&
          role.forms.some((item) => item.id === replacementId),
        'invalid_replacement'
      )
      requireValue(!this.removingForms.has(characterId), 'character_busy')
      this.removingForms.set(characterId, formId)
    })
    try {
      // 先锁住受影响对话，再取目录锁，与首轮提交的锁顺序一致。
      await this.formUsage({ characterId, formId, replacementId }, async (chats, transfer) => {
        await this.queue.run('catalog', async () => {
          const state = await this.state()
          const role = state.characters[characterId]
          const form = role.forms.find((item) => item.id === formId)
          const personas = await this.formPersonas(characterId, formId)
          requireValue(
            stamp === this.formDeletionStamp(role, formId, personas, chats),
            'revision_conflict',
            '使用情况已变化，请重新确认删除。'
          )
          // 先把每个使用方交接到仍然存在的资料，失败重试时始终能解析两侧身份。
          await transfer()
          for (const persona of personas) {
            persona.source = {
              kind: 'custom',
              displayName: form.displayName,
              description: form.description,
              avatar: form.avatar
            }
            persona.revision++
            await this.assets.commitReferences('persona:' + persona.id, persona, () =>
              writeJson(this.personaFile(persona.id), persona)
            )
          }
          role.forms = role.forms.filter((item) => item.id !== formId)
          if (role.defaultFormId === formId) role.defaultFormId = replacementId
          role.revision++
          await this.assets.commitReferences('character:' + characterId, role, () =>
            writeJson(this.file, state)
          )
          this.changes.emit('definition', characterId)
        })
      })
    } finally {
      this.removingForms.delete(characterId)
    }
  }

  /** @param {string} characterId 目标角色。 @returns {Promise<object>} 当前存档、身份及任务的删除影响。 */
  async previewDeletion(characterId) {
    const state = await this.state()
    const role = state.characters[characterId]
    requireValue(role, 'character_missing')
    const personas = []
    for (const id of state.personas) {
      const persona = await this.getPersona(id)
      if (persona?.source.kind !== 'character' || persona.source.characterId !== characterId)
        continue
      const form = role.forms.find((item) => item.id === persona.source.formId)
      personas.push({
        id,
        revision: persona.revision,
        name: form?.displayName || role.name,
        formId: persona.source.formId
      })
    }
    const usage = (await this.deletionUsage?.(characterId)) ?? { chats: [], busy: false }
    const stamp = fingerprint([role.revision, personas, usage.chats.map((chat) => chat.id).sort()])
    return { ...usage, personas, stamp }
  }

  /**
   * 删除指定的角色配置。
   * @param {string} characterId 要删除的角色 ID。
   * @param {number} expectedRevision 当前所基于的版本号，用于防止误删已被更新的角色。
   * @returns {Promise<void>}
   */
  async delete(characterId, expectedRevision, confirmation) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      requireValue(
        state.characters[characterId]?.revision === expectedRevision,
        'revision_conflict'
      )
      requireValue(!this.removingForms.has(characterId), 'character_busy')
      if (confirmation) {
        const impact = await this.previewDeletion(characterId)
        requireValue(!impact.busy, 'character_busy', '请先结束该角色的生成、演出或安装任务。')
        requireValue(
          impact.stamp === confirmation.stamp,
          'revision_conflict',
          '角色使用情况已变化，请重新查看删除影响。'
        )
        if (confirmation.keepPersonas) {
          for (const item of impact.personas) {
            const form = state.characters[characterId].forms.find(
              (entry) => entry.id === item.formId
            )
            if (!form) continue
            const persona = await this.getPersona(item.id)
            persona.source = {
              kind: 'custom',
              displayName: form.displayName,
              description: form.description,
              avatar: form.avatar
            }
            persona.revision += 1
            // 先交接头像引用并保存独立身份，再释放角色持有的资源。
            await this.assets.commitReferences('persona:' + item.id, persona, () =>
              writeJson(this.personaFile(item.id), persona)
            )
          }
        }
      }
      delete state.characters[characterId]
      state.order = state.order.filter((id) => id !== characterId)
      await this.assets.commitReferences('character:' + characterId, null, () =>
        writeJson(this.file, state)
      )
      this.changes.emit('membership', characterId)
      this.changes.emit('definition', characterId)
    })
  }

  /** 形态和模型的选择规则由首帧和完整能力共同使用。 */
  async modelSelection(characterId, options) {
    const character = await this.get(characterId)
    requireValue(character, 'character_missing')
    const form = character.forms.find(
      (item) => item.id === (options.formId ?? character.defaultFormId)
    )
    this.assertFormAvailable(characterId, form)
    const modelId = options.modelId ?? form.defaultModelId
    const model = form.models.find((item) => item.id === modelId)
    if (modelId !== null) this.assertOutfitAvailable(characterId, form, modelId)
    return { character, form, modelId, model }
  }

  /**
   * @param {string} characterId 角色 ID。
   * @param {{formId?: string, modelId?: string | null}} options 当前会话选择。
   * @returns {Promise<{displayName: string, presentation: import('../../shared/contracts/presentation.js').ModelPresentation | null}>} 只读首帧材料。
   */
  async getPresentation(characterId, options = {}) {
    const { form, model } = await this.modelSelection(characterId, options)
    return { displayName: form.displayName, presentation: model?.presentation ?? null }
  }

  /** 在释放目录锁前交接模型引用，避免清理与场景切换交错。 */
  async acquirePresentation(owner, characterId, options) {
    return this.queue.run('catalog', async () => {
      const value = await this.getPresentation(characterId, options)
      await this.assets.addReferences(owner, extractAssetReferences(value))
      return value
    })
  }

  /**
   * 解析指定角色在特定形态和模型下的可用能力（包括人设信息、可用动作/表情/状态列表以及语音配置）。
   * @param {string} characterId 角色 ID。
   * @param {{ formId?: string, modelId?: string | null }} options 解析选项。省略 formId 时使用角色的默认形态；省略 modelId 或为 null 时使用该形态的默认模型。
   * @returns {Promise<CharacterCapabilities>} 角色能力快照及解析过程中发现的问题列表。
   */
  async resolveCapabilities(characterId, options) {
    const { character, form, modelId, model } = await this.modelSelection(characterId, options)
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
    problems.push(...(mapping.problems ?? []))
    const excluded = new Set()
    for (const [key, state] of Object.entries(mapping.states ?? {})) {
      // 每个 model state（比如戴上/摘下面具）必须有对应的 on/off（进入/退出）动作。
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
        .filter(
          ([key, value]) =>
            !excluded.has(key) && (mapping.version === undefined || value.description)
        )
        .map(([key, value]) => ({ id: key, description: value.description ?? key }))
    }
    return {
      id: character.id,
      name:
        character.name ??
        character.forms.find((item) => item.id === character.defaultFormId).displayName,
      forms: character.forms.map((item) => ({
        id: item.id,
        displayName: item.displayName,
        description: item.description
      })),
      revision: character.revision,
      formId: form.id,
      selfNameMode: character.selfNameMode ?? 'character',
      displayName: form.displayName,
      description: form.description,
      avatar: copy(form.avatar),
      themeColor: form.themeColor,
      modelId,
      modelVersion: mapping.version ?? null,
      motionDirections: Object.fromEntries(
        Object.entries(mapping.motions)
          .filter(([id]) => !excluded.has(id))
          .map(([id, motion]) => [id, Object.keys(motion.variants ?? {})])
      ),
      presentation: copy(model?.presentation ?? null),
      performances: {
        motions: descriptions(mapping.motions),
        expressions: descriptions(mapping.expressions),
        presets: (mapping.presets ?? []).filter((item) => !excluded.has(item.motion)),
        states: Object.entries(states).map(([id, description]) => ({ id, description }))
      },
      voice: copy(form.voice),
      replyVoiceEnabled: form.replyVoiceEnabled,
      problems
    }
  }

  /** 在目录锁内读取并交接能力，防止更换角色资源与清理穿过快照读取。 */
  async acquireCapabilities(owner, characterId, options) {
    return this.queue.run('catalog', async () => {
      const value = await this.resolveCapabilities(characterId, options)
      await this.assets.addReferences(owner, extractAssetReferences(value))
      return value
    })
  }

  /** 身份可能间接引用角色头像，两者共用目录锁完成快照交接。 */
  async acquirePersona(owner, personaId) {
    return this.queue.run('catalog', async () => {
      const value = await this.resolvePersona(personaId)
      await this.assets.addReferences(owner, extractAssetReferences(value))
      return value
    })
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
          { code: 'voice_unconfigured', message: '当前人格没有可用参考声音。', retryable: false }
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
        // 角色或形态被删除后仍保留失效身份及问题说明，便于修复且不阻断整个列表。
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
   * 创建或更新一个用户对话身份（支持自定义文本或引用角色的具体形态）。
   * @param {UserPersonaDefinition} definition 完整的身份定义。新建时 revision 为 0，更新时传入当前版本号。
   * @returns {Promise<UserPersonaDefinition>} 保存成功并递增版本号后的身份定义副本。
   */
  async savePersona(definition) {
    return this.queue.run('catalog', async () => {
      const state = await this.state()
      const previous = await this.getPersona(definition.id)
      requireValue(definition.revision === (previous?.revision ?? 0), 'revision_conflict')
      // 固定形态身份但不复制其内容，新建聊天采用该形态的最新设定。
      if (definition.source.kind === 'character') {
        await this.resolvePersonaForm(definition.source)
      } else {
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
   * 解析指定对话身份的实际名称、人设描述和头像；角色来源只读取明确绑定的形态。
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
        sourceFormId: null,
        displayName: definition.source.displayName,
        description: definition.source.description,
        avatar: copy(definition.source.avatar)
      }
    const form = await this.resolvePersonaForm(definition.source)
    return {
      personaId,
      sourceCharacterId: definition.source.characterId,
      sourceFormId: form.id,
      displayName: form.displayName,
      description: form.description,
      avatar: copy(form.avatar)
    }
  }

  /**
   * 保存与解析共用同一形态校验，不允许缺失引用悄悄变成默认形态。
   * @param {import('../../shared/contracts/persona.js').CharacterPersonaSource} source 明确的角色形态引用。
   * @returns {Promise<import('../../shared/contracts/characters.js').CharacterForm>} 当前形态定义。
   */
  async resolvePersonaForm(source) {
    fields(source, ['kind', 'characterId', 'formId'])
    requireValue(source.kind === 'character')
    requireValue(typeof source.characterId === 'string' && source.characterId.trim().length > 0)
    requireValue(typeof source.formId === 'string' && source.formId.trim().length > 0)
    const character = await this.get(source.characterId)
    requireValue(character, 'character_missing')
    const form = character.forms.find((item) => item.id === source.formId)
    this.assertFormAvailable(source.characterId, form)
    return form
  }
}
