import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { fingerprint, requireValue, safeId } from '../core/support.js'
import { relativeFile, stamp } from './files.js'
import { createLegacySource } from './legacy-source.js'
import { isTimelineChat, pythonMedia, validatePythonChat } from '../storage/python-format.js'

/** 旧路径默认相对 GPT_SoVITS，聊天附件相对 reference_audio，结果始终限制在所选目录内。 */
export function legacyRelative(root, value, base = 'GPT_SoVITS') {
  requireValue(typeof value === 'string' && value.trim(), 'legacy_path_missing')
  const normalized = value.replaceAll('\\', '/')
  // Live2D 目录在较新的 Python 存档中也可以写成项目根目录相对路径。
  const relativeBase = /^(?:\.\/)?live2d_related\//.test(normalized) ? '' : base
  const absolute = path.isAbsolute(normalized)
    ? normalized
    : path.resolve(root, relativeBase, normalized)
  const relative = path.relative(root, absolute).split(path.sep).join('/')
  return relativeFile(relative)
}

const referenceModes = {
  中文: 'all_zh',
  英文: 'en',
  日文: 'all_ja',
  粤语: 'all_yue',
  韩文: 'all_ko',
  中英混合: 'zh',
  日英混合: 'ja',
  粤英混合: 'yue',
  韩英混合: 'ko',
  多语种混合: 'auto',
  '多语种混合(粤语)': 'auto_yue'
}
const referenceModeCodes = Object.values(referenceModes)
const referenceLanguageAliases = { 日语: 'all_ja', 英语: 'en', 韩语: 'all_ko', jp: 'ja' }
const referenceEmotions = ['happiness', 'sadness', 'anger', 'disgust', 'like', 'surprise', 'fear']

function referenceLanguage(text) {
  // Python 版只取首个非注释行；编号顺序与旧版设置一致，不能用注释里的语言名称判断。
  const value = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith('#'))
  if (!value) return null
  if (/^\d+$/.test(value)) return referenceModeCodes[Number(value) - 1] ?? null
  const normalized = value.toLowerCase().replace('（', '(').replace('）', ')')
  if (referenceModeCodes.includes(normalized)) return normalized
  if (Object.hasOwn(referenceModes, normalized)) return referenceModes[normalized]
  return Object.hasOwn(referenceLanguageAliases, normalized)
    ? referenceLanguageAliases[normalized]
    : null
}

function mediaPath(root, value, purpose) {
  return purpose === 'audio'
    ? legacyRelative(root, value.split('[', 1)[0])
    : legacyRelative(root, value, 'reference_audio')
}

async function optionalText(source, name, documents) {
  try {
    const file = await source.path(name)
    const value = await readFile(file, 'utf8')
    // 文本转换为角色字段而不复制，但仍须在执行前复核来源是否变化。
    documents[name] = await stamp(file)
    return value.trim()
  } catch (error) {
    if (error.code === 'ENOENT' || error.problem?.code === 'legacy_new_data_excluded') return ''
    throw error
  }
}

/**
 * @param {string} root 已授权的旧版根目录。
 * @param {string[]} excludedRoots 新版数据的排除范围。
 * @param {object[] | null} gsvFiles 随程序发布的公共资源清单；独立扫描默认读取内置清单。
 * @returns {Promise<object>} 保留私有转换材料的扫描结果，由调用方生成公开摘要。
 */
export async function scanLegacyDirectory(root, excludedRoots = [], gsvFiles = null) {
  const sharedFiles =
    gsvFiles ??
    JSON.parse(
      await readFile(new URL('../../../resources/setup/gsv-files.json', import.meta.url), 'utf8')
    )
  const warnings = []
  const excluded = new Set()
  const source = await createLegacySource(root, excludedRoots, (name) => {
    if (excluded.has(name)) return
    excluded.add(name)
    warnings.push({ code: 'legacy_new_data_excluded', file: name })
  })
  root = source.root
  async function sourceFiles(name) {
    try {
      return await source.walk(name)
    } catch (error) {
      if (error.code === 'ENOENT' || error.problem?.code === 'legacy_new_data_excluded') return []
      throw error
    }
  }
  async function newestFile(base, names) {
    let newest = null
    let latest = -Infinity
    for (const name of names) {
      const { mtime } = await stamp(await source.path(base + '/' + name))
      if (mtime <= latest) continue
      newest = name
      latest = mtime
    }
    return newest
  }
  const files = {}
  const documents = {}
  function text(name) {
    return optionalText(source, name, documents)
  }
  async function add(
    name,
    purpose = 'model',
    entry = null,
    { readOnlyWeight = false, optional = false } = {}
  ) {
    if (!name) return null
    const key = fingerprint({ name, purpose, entry }).slice(0, 32)
    if (files[key]) return key
    try {
      const file = await source.path(name, Boolean(entry))
      if (entry) {
        const members = await source.walk(name)
        requireValue(members.includes(entry), 'legacy_model_entry_missing')
        const stamps = {}
        for (const member of members)
          stamps[member] = await stamp(await source.path(name + '/' + member))
        files[key] = {
          key,
          name,
          purpose,
          entry,
          stamps,
          sizeBytes: Object.values(stamps).reduce((sum, item) => sum + item.size, 0),
          hardlink: false
        }
      } else {
        const value = await stamp(file)
        files[key] = {
          key,
          name,
          purpose,
          stamp: value,
          sizeBytes: value.size,
          // 由资源清单或角色权重引用确认用途，不能凭目录、后缀或文件大小推断只读性。
          hardlink: purpose === 'model' && readOnlyWeight
        }
      }
      return key
    } catch (error) {
      if (error.code !== 'ENOENT' && !error.problem) throw error
      if (error.code === 'ENOENT' && optional) return null
      if (error.problem?.code !== 'legacy_new_data_excluded')
        warnings.push({ code: error.problem?.code ?? 'legacy_file_missing', file: name })
      return null
    }
  }
  async function document(name) {
    try {
      const file = await source.path(name)
      const raw = await readFile(file, 'utf8')
      const parsed = JSON.parse(raw)
      documents[name] = await stamp(file)
      return parsed
    } catch (error) {
      if (error.code === 'ENOENT') return null
      if (error.problem?.code !== 'legacy_new_data_excluded')
        warnings.push({ code: 'legacy_document_invalid', file: name })
      return null
    }
  }
  const config = await document('d_sakiko_config.json')
  let chats = await document('reference_audio/all_conversation.json')
  if (!config && !chats) {
    const older = [
      'dsakiko_config.json',
      'reference_audio/history_messages_dp.json',
      'reference_audio/history_messages_qt.json',
      'reference_audio/small_theater_history.json'
    ]
    for (const name of older)
      if (await text(name)) warnings.push({ code: 'legacy_upgrade_required', file: name })
  }
  if (chats && !Array.isArray(chats.chat_list)) {
    warnings.push({
      code: 'legacy_upgrade_required',
      file: 'reference_audio/all_conversation.json'
    })
    chats = null
  }
  const chatRecords = []
  const chatIndexes = []
  if (chats) {
    if (chats.conversation_schema_version != null && chats.conversation_schema_version !== 1)
      warnings.push({
        code: 'legacy_chat_version_unsupported',
        file: 'reference_audio/all_conversation.json'
      })
    else {
      for (const [index, chat] of chats.chat_list.entries()) {
        try {
          validatePythonChat(chat)
          chatRecords.push(chat)
          chatIndexes.push(index)
        } catch {
          warnings.push({
            code: 'legacy_chat_invalid',
            file: 'reference_audio/all_conversation.json',
            position: index + 1
          })
        }
      }
    }
  }
  const roles = []
  const roots = await source.list('live2d_related').catch((error) => {
    if (error.code === 'ENOENT' || error.problem?.code === 'legacy_new_data_excluded') return []
    throw error
  })
  roots.sort((a, b) => Number(a.name > b.name) - Number(a.name < b.name))
  for (const folder of roots.filter(
    (entry) => entry.isDirectory() && !entry.name.startsWith('.')
  )) {
    const id = folder.name
    try {
      safeId(id)
    } catch {
      warnings.push({ code: 'legacy_character_id_invalid', file: id })
      continue
    }
    const base = 'live2d_related/' + id
    const name = await text(base + '/name.txt')
    const description = await text(base + '/character_description.txt')
    if (!name || !description) {
      warnings.push({ code: 'legacy_character_incomplete', file: base })
      continue
    }
    const roleFiles = await sourceFiles(base)
    const modelNames = roleFiles.filter((file) => /\.model3?\.json$/.test(file))
    const models = []
    for (const file of modelNames) {
      const directory = path.posix.dirname(file)
      const model = await add(base + '/' + directory, 'live2d', path.posix.basename(file))
      if (model)
        models.push({
          key: model,
          file: base + '/' + file,
          id: fingerprint(file).slice(0, 24),
          name: directory.split('/').at(-1)
        })
    }
    const voiceBase = 'reference_audio/' + id
    const voiceNames = await sourceFiles(voiceBase)
    const gpt = await newestFile(
      voiceBase,
      voiceNames.filter((file) => /^GPT-SoVITS_models\/[^/]+\.ckpt$/.test(file))
    )
    const sovits = await newestFile(
      voiceBase,
      voiceNames.filter((file) => /^GPT-SoVITS_models\/[^/]+\.pth$/.test(file))
    )
    const voiceFiles = {}
    for (const file of [gpt, sovits].filter(Boolean))
      voiceFiles[file] = await add(voiceBase + '/' + file, 'model', null, { readOnlyWeight: true })
    const languageFile = voiceBase + '/reference_audio_language.txt'
    const language = referenceLanguage(await text(languageFile))
    async function reference(audioPath, promptText) {
      if (typeof audioPath !== 'string' || typeof promptText !== 'string' || !promptText.trim())
        return null
      let relative
      try {
        relative = legacyRelative(root, audioPath)
      } catch {
        warnings.push({ code: 'legacy_path_outside_root', file: voiceBase })
        return null
      }
      const audio = await add(relative, 'audio')
      return audio ? { audio, text: promptText.trim(), language: language ?? 'all_ja' } : null
    }
    const emotionDocument = await document(voiceBase + '/reference_audio_and_text.json')
    const emotions = {}
    for (const [label, value] of Object.entries(
      emotionDocument?.emotion_refs ?? emotionDocument ?? {}
    )) {
      const key = label.toLowerCase()
      const alias = /^label_([0-6])$/.exec(key)
      const emotion = alias ? referenceEmotions[Number(alias[1])] : key
      if (!referenceEmotions.includes(emotion)) continue
      const ref = await reference(value?.audio_path, value?.text)
      if (ref) emotions[emotion] = ref
    }
    async function formReference(form) {
      if (form !== 'default') {
        const prompt = await text(voiceBase + '/reference_text_' + form + '_sakiko.txt')
        const configured = await text(voiceBase + '/default_ref_audio_' + form + '.txt')
        return (
          (configured && (await reference(configured, prompt))) ||
          (await reference('../' + voiceBase + '/' + form + '_sakiko.wav', prompt))
        )
      }
      const emotion = referenceEmotions.find((key) => emotions[key])
      if (emotion) return emotions[emotion]
      const prompt = await text(voiceBase + '/reference_text.txt')
      if (!prompt) return null
      const configured = await text(voiceBase + '/default_ref_audio.txt')
      const selected = configured && (await reference(configured, prompt))
      if (selected) return selected
      const fallback = await newestFile(
        voiceBase,
        voiceNames.filter((file) => /^[^/]+\.(wav|mp3)$/i.test(file))
      )
      return fallback ? reference('../' + voiceBase + '/' + fallback, prompt) : null
    }
    const forms = id === 'sakiko' ? ['black', 'white'] : ['default']
    const formRecords = []
    for (const form of forms) {
      const ref = await formReference(form)
      const matchingModels = models
        .filter(
          (model) =>
            form === 'default' ||
            model.file.includes('/extra_model/') ||
            (form === 'black'
              ? model.file.includes('live2D_model_costume/')
              : !model.file.includes('live2D_model_costume/'))
        )
        .map((model) => ({ ...model, id: form === 'default' ? model.id : form + '-' + model.id }))
      let selected
      try {
        selected = legacyRelative(root, config?.character_setting?.l2d_json_paths_dict?.[name])
      } catch {
        /* 未选择模型时使用该形态首个可用模型。 */
      }
      const preferred =
        matchingModels.find((model) => model.file === selected) ??
        matchingModels.find((model) => /\/live2D_model(?:_costume)?\//.test(model.file)) ??
        matchingModels[0]
      formRecords.push({
        id: form,
        displayName: name,
        description,
        models: matchingModels,
        defaultModelId: preferred?.id ?? null,
        voice:
          voiceFiles[gpt] && voiceFiles[sovits] && ref
            ? {
                gptWeights: voiceFiles[gpt],
                sovitsWeights: voiceFiles[sovits],
                defaultReference: ref,
                emotions: form === 'default' ? emotions : {}
              }
            : null
      })
    }
    if (!language && formRecords.some((form) => form.voice))
      warnings.push({ code: 'legacy_voice_language_unknown', file: languageFile })
    let avatar = null
    const avatarPath =
      config?.webui_setting?.character_avatars?.[id] ??
      config?.webui_setting?.character_avatars?.[name]
    if (typeof avatarPath === 'string') {
      try {
        avatar = await add(legacyRelative(root, avatarPath), 'avatar')
      } catch {
        warnings.push({ code: 'legacy_path_outside_root', file: base })
      }
    }
    if (!avatar) {
      const icon =
        (await newestFile(
          base,
          roleFiles.filter((file) => /^[^/]+\.png$/i.test(file))
        )) ?? roleFiles.find((file) => /(?:icon|avatar)\.png$/i.test(file))
      if (icon) avatar = await add(base + '/' + icon, 'avatar')
    }
    const style = await text(voiceBase + '/QT_style.json')
    roles.push({
      id,
      name,
      forms: formRecords,
      avatar,
      themeColor: style.match(/#[a-fA-F0-9]{6}/)?.[0] ?? null,
      voiceFiles
    })
  }
  const gsv = {}
  for (const resource of sharedFiles) {
    // 下载包按解压成员查找旧文件；不遍历 pretrained_models，避免混入 ASR、embedding 或训练资料。
    const members = resource.archive ? resource.members : [resource]
    for (const member of members) {
      const name = 'GPT_SoVITS/' + member.path.replace(/^G2PWModel\//, 'text/G2PWModel/')
      const key = await add(name, 'model', null, {
        readOnlyWeight: member.readOnlyWeight === true,
        optional: true
      })
      if (key) gsv[name] = key
    }
  }
  const media = {}
  for (const chat of chatRecords) {
    if (!isTimelineChat(chat)) continue
    for (const item of pythonMedia(chat)) {
      if (!item.path || Object.hasOwn(media, item.path)) continue
      try {
        media[item.path] = await add(mediaPath(root, item.path, item.purpose), item.purpose)
      } catch {
        warnings.push({
          code: 'legacy_path_outside_root',
          file: 'reference_audio/all_conversation.json'
        })
      }
    }
  }
  for (const chat of chatRecords) {
    if (isTimelineChat(chat)) continue
    for (const message of chat.message_list ?? []) {
      const candidates = [
        message.audio_path,
        ...(message.attachments ?? []).map((item) => item.local_path ?? item.path)
      ]
      for (const value of candidates.filter(
        (item) => typeof item === 'string' && item && item !== 'NO_AUDIO'
      )) {
        if (Object.hasOwn(media, value)) continue
        try {
          const purpose = value === message.audio_path ? 'audio' : 'attachment'
          media[value] = await add(mediaPath(root, value, purpose), purpose)
        } catch {
          warnings.push({ code: 'legacy_path_outside_root', file: '聊天附件' })
          media[value] = null
        }
      }
    }
  }
  const personas = []
  const savedPersonas = config?.character_setting?.user_characters
  for (const persona of Array.isArray(savedPersonas) ? savedPersonas : []) {
    if (!persona || typeof persona !== 'object' || Array.isArray(persona)) continue
    let avatar = null
    if (typeof persona.avatar_path === 'string' && persona.avatar_path) {
      try {
        avatar = await add(legacyRelative(root, persona.avatar_path), 'avatar')
      } catch {
        warnings.push({ code: 'legacy_path_outside_root', file: '用户人设头像' })
      }
    }
    personas.push({ ...persona, avatar })
  }
  requireValue(
    config || chats || roles.length || warnings.length || Object.values(gsv).some(Boolean),
    'legacy_directory_invalid',
    '所选目录中没有可识别的 Python 版本资料。'
  )
  return {
    root,
    excludedRoots: source.excludedRoots,
    config,
    chats: chatRecords,
    chatIndexes,
    roles,
    files,
    documents,
    media,
    personas,
    gsv,
    warnings: warnings.map((warning) => ({
      ...warning,
      message:
        {
          legacy_new_data_excluded: '资料位于新版数据目录，已跳过。',
          legacy_package_overlaps_data: '模型包内包含新版数据目录，已跳过该模型包。',
          legacy_upgrade_required: '请先用较新的 Python 版本打开程序并保存资料，再进行导入。',
          legacy_document_invalid: '资料文件损坏或格式无效，请检查后重新扫描。',
          legacy_chat_invalid: `第 ${warning.position} 条聊天结构无效，已跳过该条记录。`,
          legacy_chat_version_unsupported: '聊天存档版本尚不支持，请更新程序后重试。',
          legacy_voice_language_unknown: '无法识别参考音频语言，按旧版默认的日语导入。',
          legacy_file_missing: '文件不存在，将保留其余可迁移资料。',
          legacy_path_outside_root: '资源位于所选目录之外，未读取该文件。'
        }[warning.code] ?? '部分资源无法导入，请检查对应文件。'
    })),
    identity: fingerprint(root).slice(0, 32)
  }
}

/** 执行之前验证来源快照，包括目录中新增、删除及替换的文件。 */
export async function validateLegacySource(scan, excludedRoots = []) {
  const source = await createLegacySource(scan.root, [
    ...(scan.excludedRoots ?? []),
    ...excludedRoots
  ])
  for (const [name, original] of Object.entries(scan.documents))
    requireValue(
      fingerprint(await stamp(await source.path(name))) === fingerprint(original),
      'legacy_source_changed',
      '旧版本资料已改变，请重新扫描。'
    )
  for (const item of Object.values(scan.files)) {
    const file = await source.path(item.name, Boolean(item.entry))
    if (item.entry) {
      const names = await source.walk(item.name)
      requireValue(
        fingerprint(names) === fingerprint(Object.keys(item.stamps).sort()),
        'legacy_source_changed'
      )
      for (const name of names)
        requireValue(
          fingerprint(await stamp(await source.path(item.name + '/' + name))) ===
            fingerprint(item.stamps[name]),
          'legacy_source_changed'
        )
    } else
      requireValue(
        fingerprint(await stamp(file)) === fingerprint(item.stamp),
        'legacy_source_changed',
        '旧版本资料已改变，请重新扫描。'
      )
  }
}
