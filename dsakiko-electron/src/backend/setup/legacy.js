import path from 'node:path'
import { readFile, readdir, realpath } from 'node:fs/promises'
import { fingerprint, requireValue, safeId } from '../core/support.js'
import { relativeFile, sourcePath, stamp, walk } from './files.js'

/** 旧路径以 GPT_SoVITS 为工作目录，规范化后必须仍属于用户所选根目录。 */
export function legacyRelative(root, value) {
  requireValue(typeof value === 'string' && value.trim(), 'legacy_path_missing')
  const normalized = value.replaceAll('\\', '/')
  const absolute = path.isAbsolute(normalized)
    ? normalized
    : path.resolve(root, 'GPT_SoVITS', normalized)
  const relative = path.relative(root, absolute).split(path.sep).join('/')
  return relativeFile(relative)
}

async function optionalText(root, name) {
  try {
    return (await readFile(await sourcePath(root, name), 'utf8')).trim()
  } catch (error) {
    if (error.code === 'ENOENT') return ''
    throw error
  }
}

/** 扫描结果保留后端私有转换材料，对外只返回摘要与缺失项。 */
export async function scanLegacyDirectory(root) {
  root = await realpath(root)
  const warnings = []
  const files = {}
  const documents = {}
  async function add(name, purpose = 'model', entry = null) {
    if (!name) return null
    const key = fingerprint({ name, purpose, entry }).slice(0, 32)
    if (files[key]) return key
    try {
      const source = await sourcePath(root, name)
      if (entry) {
        const members = await walk(source)
        requireValue(members.includes(entry), 'legacy_model_entry_missing')
        const stamps = {}
        for (const member of members) stamps[member] = await stamp(path.join(source, member))
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
        const value = await stamp(source)
        files[key] = {
          key,
          name,
          purpose,
          stamp: value,
          sizeBytes: value.size,
          hardlink:
            purpose === 'model' &&
            name.startsWith('GPT_SoVITS/') &&
            /\.(pth|ckpt|bin|onnx|pt)$/i.test(name) &&
            value.size >= 32 * 1024 * 1024
        }
      }
      return key
    } catch (error) {
      if (error.code !== 'ENOENT' && !error.problem) throw error
      warnings.push({ code: error.problem?.code ?? 'legacy_file_missing', file: name })
      return null
    }
  }
  async function document(name) {
    try {
      const file = await sourcePath(root, name)
      const raw = await readFile(file, 'utf8')
      const parsed = JSON.parse(raw)
      documents[name] = await stamp(file)
      return parsed
    } catch (error) {
      if (error.code === 'ENOENT') return null
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
      if (await optionalText(root, name))
        warnings.push({ code: 'legacy_upgrade_required', file: name })
  }
  if (chats && !Array.isArray(chats.chat_list)) {
    warnings.push({
      code: 'legacy_upgrade_required',
      file: 'reference_audio/all_conversation.json'
    })
    chats = null
  }
  const roles = []
  const roots = await readdir(path.join(root, 'live2d_related'), { withFileTypes: true }).catch(
    (error) => {
      if (error.code === 'ENOENT') return []
      throw error
    }
  )
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
    const name = await optionalText(root, base + '/name.txt')
    const description = await optionalText(root, base + '/character_description.txt')
    if (!name || !description) {
      warnings.push({ code: 'legacy_character_incomplete', file: base })
      continue
    }
    // 人设等小文件也记录快照，确认页之后发生修改不能被悄悄覆盖。
    for (const file of ['name.txt', 'character_description.txt'])
      documents[base + '/' + file] = await stamp(await sourcePath(root, base + '/' + file))
    const modelNames = (await walk(await sourcePath(root, base))).filter((file) =>
      /\.model3?\.json$/.test(file)
    )
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
    const voiceNames = await walk(path.join(root, voiceBase)).catch((error) => {
      if (error.code === 'ENOENT') return []
      throw error
    })
    const gpt = voiceNames.find(
      (file) => file.startsWith('GPT-SoVITS_models/') && file.endsWith('.ckpt')
    )
    const sovits = voiceNames.find(
      (file) => file.startsWith('GPT-SoVITS_models/') && file.endsWith('.pth')
    )
    const voiceFiles = {}
    for (const file of voiceNames)
      voiceFiles[file] = await add(
        voiceBase + '/' + file,
        /\.(pth|ckpt)$/.test(file) ? 'model' : 'audio'
      )
    const languageText = await optionalText(root, voiceBase + '/reference_audio_language.txt')
    const language = /日|ja|jp/i.test(languageText) ? 'ja' : 'zh'
    async function reference(audioPath, text) {
      if (!audioPath || !text) return null
      let relative
      try {
        relative = legacyRelative(root, audioPath)
      } catch {
        warnings.push({ code: 'legacy_path_outside_root', file: voiceBase })
        return null
      }
      const audio = await add(relative, 'audio')
      return audio ? { audio, text, language } : null
    }
    const emotionDocument = await document(voiceBase + '/reference_audio_and_text.json')
    const emotions = {}
    for (const [emotion, value] of Object.entries(
      emotionDocument?.emotion_refs ?? emotionDocument ?? {}
    )) {
      if (
        !['happiness', 'sadness', 'anger', 'disgust', 'like', 'surprise', 'fear'].includes(emotion)
      )
        continue
      const ref = await reference(value.audio_path, value.text)
      if (ref) emotions[emotion] = ref
    }
    const defaultPath = await optionalText(root, voiceBase + '/default_ref_audio.txt')
    let fallback = defaultPath
    if (!fallback) {
      const candidates = voiceNames.filter((file) => /\.(wav|mp3)$/i.test(file))
      candidates.sort(
        (a, b) =>
          (files[voiceFiles[b]]?.stamp?.mtime ?? 0) - (files[voiceFiles[a]]?.stamp?.mtime ?? 0)
      )
      if (candidates[0]) fallback = '../' + voiceBase + '/' + candidates[0]
    }
    const forms = id === 'sakiko' ? ['black', 'white'] : ['default']
    const formRecords = []
    for (const form of forms) {
      const ref =
        form === 'default'
          ? (emotions.happiness ??
            Object.values(emotions)[0] ??
            (await reference(
              fallback,
              await optionalText(root, voiceBase + '/reference_text.txt')
            )))
          : await reference(
              '../' + voiceBase + '/' + form + '_sakiko.wav',
              await optionalText(root, voiceBase + '/reference_text_' + form + '_sakiko.txt')
            )
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
          gpt && sovits && ref
            ? {
                gptWeights: voiceFiles[gpt],
                sovitsWeights: voiceFiles[sovits],
                defaultReference: ref,
                emotions: form === 'default' ? emotions : {}
              }
            : null
      })
    }
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
    if (!avatar && modelNames.length) {
      const icon = (await walk(await sourcePath(root, base))).find((file) =>
        /(?:icon|avatar)\.png$/i.test(file)
      )
      if (icon) avatar = await add(base + '/' + icon, 'avatar')
    }
    const style = await optionalText(root, voiceBase + '/QT_style.json')
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
  for (const directory of [
    'GPT_SoVITS/pretrained_models',
    'GPT_SoVITS/text/G2PWModel',
    'GPT_SoVITS/nltk_data',
    'GPT_SoVITS/open_jtalk_dic_utf_8-1.11'
  ]) {
    let names = []
    try {
      names = await walk(await sourcePath(root, directory))
    } catch (error) {
      if (error.code !== 'ENOENT')
        warnings.push({ code: 'legacy_resource_unreadable', file: directory })
    }
    for (const name of names)
      gsv[directory + '/' + name] = await add(directory + '/' + name, 'model')
  }
  const media = {}
  for (const chat of chats?.chat_list ?? [])
    for (const message of chat.message_list ?? []) {
      const candidates = [
        message.audio_path,
        ...(message.attachments ?? []).map((item) => item.local_path ?? item.path)
      ]
      for (const value of candidates.filter((item) => item && item !== 'NO_AUDIO')) {
        if (Object.hasOwn(media, value)) continue
        try {
          const audio = value === message.audio_path
          const relative =
            audio || path.isAbsolute(value)
              ? legacyRelative(root, value)
              : relativeFile(
                  path
                    .relative(root, path.resolve(root, 'reference_audio', value))
                    .split(path.sep)
                    .join('/')
                )
          media[value] = await add(relative, audio ? 'audio' : 'attachment')
        } catch {
          warnings.push({ code: 'legacy_path_outside_root', file: '聊天附件' })
          media[value] = null
        }
      }
    }
  const personas = []
  for (const persona of config?.character_setting?.user_characters ?? []) {
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
    config,
    chats: chats?.chat_list ?? [],
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
          legacy_upgrade_required: '请先用较新的 Python 版本打开程序并保存资料，再进行导入。',
          legacy_document_invalid: '资料文件损坏或格式无效，请检查后重新扫描。',
          legacy_file_missing: '文件不存在，将保留其余可迁移资料。',
          legacy_path_outside_root: '资源位于所选目录之外，未读取该文件。'
        }[warning.code] ?? '部分资源无法导入，请检查对应文件。'
    })),
    identity: fingerprint(root).slice(0, 32)
  }
}

/** 执行之前验证来源快照，包括目录中新增、删除及替换的文件。 */
export async function validateLegacySource(scan) {
  for (const [name, original] of Object.entries(scan.documents))
    requireValue(
      fingerprint(await stamp(await sourcePath(scan.root, name))) === fingerprint(original),
      'legacy_source_changed',
      '旧版本资料已改变，请重新扫描。'
    )
  for (const item of Object.values(scan.files)) {
    const source = await sourcePath(scan.root, item.name)
    if (item.entry) {
      const names = await walk(source)
      requireValue(
        fingerprint(names) === fingerprint(Object.keys(item.stamps).sort()),
        'legacy_source_changed'
      )
      for (const name of names)
        requireValue(
          fingerprint(await stamp(path.join(source, name))) === fingerprint(item.stamps[name]),
          'legacy_source_changed'
        )
    } else
      requireValue(
        fingerprint(await stamp(source)) === fingerprint(item.stamp),
        'legacy_source_changed',
        '旧版本资料已改变，请重新扫描。'
      )
  }
}
