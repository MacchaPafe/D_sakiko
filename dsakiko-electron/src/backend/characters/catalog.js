import { readdir, readFile, stat } from 'node:fs/promises'
import { join, resolve, isAbsolute } from 'node:path'
import { randomUUID } from 'node:crypto'
import { JsonStore, readJson, safeId } from '../storage/files.js'
import { CharacterModels } from './models.js'
import { problemError, toProblem } from '../../shared/contracts/desktop.js'

async function readText(path, fallback = '') {
  try {
    return (await readFile(path, 'utf8')).trim()
  } catch (error) {
    if (error.code === 'ENOENT') return fallback
    throw error
  }
}

async function files(directory, pattern, depth = 0) {
  if (depth > 8) return []
  let entries
  try {
    entries = await readdir(directory, { withFileTypes: true })
  } catch (error) {
    if (error.code === 'ENOENT') return []
    throw error
  }
  const found = []
  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue
    const path = join(directory, entry.name)
    if (entry.isDirectory()) found.push(...(await files(path, pattern, depth + 1)))
    else if (entry.isFile() && pattern.test(entry.name)) found.push(path)
  }
  return found.sort()
}

async function newest(paths) {
  const items = await Promise.all(
    paths.map(async (path) => ({ path, time: (await stat(path)).mtimeMs }))
  )
  items.sort((a, b) => b.time - a.time)
  return items[0]?.path || null
}

/** 原型目录只读旧资源；人格是新工程自己的持久数据。 */
export class CharacterCatalog {
  characters = new Map()
  constructor(dataRoot, assets, getRoot) {
    this.personas = new JsonStore(join(dataRoot, 'personas'))
    this.models = new CharacterModels(dataRoot, assets)
    this.assets = assets
    this.getRoot = getRoot
  }
  legacyPath(value) {
    if (!value || typeof value !== 'string') return null
    return isAbsolute(value) ? value : resolve(this.getRoot(), 'GPT_SoVITS', value)
  }
  async refresh() {
    const root = this.getRoot()
    const next = new Map()
    if (!root) {
      this.characters = next
      return []
    }
    let directories
    try {
      directories = await readdir(join(root, 'live2d_related'), { withFileTypes: true })
    } catch {
      throw problemError(
        'missing_resources',
        '所选目录需要包含 live2d_related 和 reference_audio。'
      )
    }
    const legacy = await readJson(join(root, 'd_sakiko_config.json'), {})
    for (const directory of directories) {
      if (!directory.isDirectory() || directory.name.startsWith('.')) continue
      const id = safeId(directory.name)
      const folder = join(root, 'live2d_related', id)
      const displayName = await readText(join(folder, 'name.txt'))
      const description = await readText(join(folder, 'character_description.txt'))
      if (!displayName || !description) continue
      const definition = {
        id,
        revision: 1,
        displayName,
        description,
        avatar: null,
        presentation: null,
        voice: null,
        performances: { motions: [], expressions: [] },
        models: [],
        selectedModelId: null,
        problems: []
      }
      try {
        const icon = join(folder, `${id}_icon.png`)
        const avatar = (await stat(icon).catch(() => null))?.isFile() ? icon : null
        if (avatar) definition.avatar = await this.assets.register(avatar)
      } catch (error) {
        definition.problems.push({
          ...toProblem(error),
          message: '该角色的头像无法读取。'
        })
      }
      try {
        const override = legacy.character_setting?.l2d_json_paths_dict?.[displayName]
        Object.assign(definition, await this.models.discover(id, folder, this.legacyPath(override)))
        if (definition.selectedModelId)
          Object.assign(definition, await this.models.resolve(id, definition.selectedModelId))
      } catch {
        definition.problems.push({
          code: 'model_unavailable',
          message: '当前模型无法读取，可在角色装扮中重新选择。',
          retryable: true
        })
      }
      try {
        definition.voice = await this.readVoice(id)
      } catch {
        definition.problems.push({
          code: 'voice_unavailable',
          message: '声音资源不完整，将使用无语音演出。',
          retryable: false
        })
      }
      next.set(id, definition)
    }
    this.characters = next
    return this.list()
  }
  async readVoice(id) {
    const folder = join(this.getRoot(), 'reference_audio', id)
    const gpt = await newest(await files(join(folder, 'GPT-SoVITS_models'), /\.ckpt$/i))
    const sovits = await newest(await files(join(folder, 'GPT-SoVITS_models'), /\.pth$/i))
    if (!gpt || !sovits) return null
    const storedRefs = await readJson(join(folder, 'reference_audio_and_text.json'), {})
    const refs = storedRefs.emotion_refs || storedRefs
    let audio = null
    let text = ''
    for (const entry of [refs.happiness, ...Object.values(refs)]) {
      if (entry?.audio_path && entry?.text) {
        audio = this.legacyPath(entry.audio_path)
        text = entry.text
        break
      }
    }
    if (!audio && id === 'sakiko') {
      const selected = await readText(join(folder, 'default_ref_audio_white.txt'))
      audio = selected ? this.legacyPath(selected) : join(folder, 'white_sakiko.wav')
      text =
        (await readText(join(folder, 'white_sakiko.txt'))) ||
        (await readText(join(folder, 'reference_text_white_sakiko.txt')))
    }
    if (!audio || !text) {
      const selected = await readText(join(folder, 'default_ref_audio.txt'))
      audio = selected
        ? this.legacyPath(selected)
        : await newest(await files(folder, /\.(wav|mp3)$/i))
      text = await readText(join(folder, 'reference_text.txt'))
    }
    if (!audio || !text) return null
    const rawLanguage = (await readText(join(folder, 'reference_audio_language.txt'), '日文'))
      .split('\n')
      .find((line) => line && !line.startsWith('#'))
    const languageMap = {
      1: 'zh',
      2: 'en',
      3: 'ja',
      中文: 'zh',
      英文: 'en',
      日文: 'ja',
      日语: 'ja'
    }
    return {
      gptWeights: await this.assets.register(gpt),
      sovitsWeights: await this.assets.register(sovits),
      referenceAudio: await this.assets.register(audio),
      referenceText: text,
      referenceLanguage: languageMap[rawLanguage] || rawLanguage
    }
  }
  async list() {
    return [...this.characters.values()].map((character) => ({
      id: character.id,
      displayName: character.displayName,
      avatar: character.avatar,
      hasModel: Boolean(character.presentation),
      hasVoice: Boolean(character.voice),
      models: structuredClone(character.models),
      selectedModelId: character.selectedModelId,
      problems: character.problems
    }))
  }
  /**
   * 保存模型偏好后一起替换展示与动作能力，调用方负责运行态保护。
   * @param {string} characterId 目录中的角色身份。
   * @param {string} modelId 已发现的模型身份。
   * @returns {Promise<void>} 选择已持久化且能力已更新。
   */
  async setModel(characterId, modelId) {
    const definition = this.characters.get(characterId)
    if (!definition) throw problemError('character_unavailable', '角色不存在。')
    const capabilities = await this.models.select(characterId, modelId)
    Object.assign(definition, capabilities, {
      selectedModelId: modelId,
      revision: definition.revision + 1
    })
    definition.problems = definition.problems.filter(
      (problem) => problem.code !== 'model_unavailable'
    )
  }
  async get(id) {
    return structuredClone(this.characters.get(id) || null)
  }
  async resolveCapabilities(id) {
    const character = await this.get(id)
    if (!character) throw problemError('character_unavailable', '角色不存在，请检查资源目录。')
    return character
  }
  async getPersona(id) {
    return this.personas.load(id)
  }
  async listPersonas() {
    return Promise.all(
      (await this.personas.list()).map(async (definition) => {
        try {
          const value = await this.resolvePersona(definition.id)
          return {
            id: definition.id,
            displayName: value.displayName,
            avatar: value.avatar,
            problem: null
          }
        } catch (error) {
          return { id: definition.id, displayName: null, avatar: null, problem: toProblem(error) }
        }
      })
    )
  }
  async savePersona(definition) {
    const source = definition?.source
    if (!source || !['custom', 'character'].includes(source.kind))
      throw problemError('invalid_persona', '人格来源无效。')
    let normalized
    if (source.kind === 'custom') {
      if (
        typeof source.displayName !== 'string' ||
        !source.displayName.trim() ||
        typeof source.description !== 'string' ||
        source.description.length > 20000
      )
        throw problemError('invalid_persona', '请填写人格名称和有效描述。')
      normalized = {
        kind: 'custom',
        displayName: source.displayName.trim(),
        description: source.description,
        avatar: null
      }
    } else {
      await this.resolveCapabilities(source.characterId)
      normalized = { kind: 'character', characterId: source.characterId }
    }
    return this.personas.save({
      id: definition.id || randomUUID(),
      revision: definition.revision ?? 0,
      source: normalized
    })
  }
  async resolvePersona(id) {
    const definition = await this.getPersona(id)
    if (!definition) throw problemError('persona_unavailable', '人格不存在。')
    const source = definition.source
    const resolved =
      source.kind === 'character' ? await this.resolveCapabilities(source.characterId) : source
    return {
      personaId: id,
      sourceCharacterId: source.kind === 'character' ? source.characterId : null,
      displayName: resolved.displayName,
      description: resolved.description,
      avatar: resolved.avatar
    }
  }
}
