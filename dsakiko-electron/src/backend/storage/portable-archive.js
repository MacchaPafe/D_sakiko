import { zipSync, unzipSync, strToU8, strFromU8 } from 'fflate'
import { createHash } from 'node:crypto'
import { uuid, requireValue, merge } from '../core/support.js'
import { chatSettings } from '../core/defaults.js'
import { extractAssetReferences } from '../resources/references.js'
import { spokenText } from '../speech/spoken-text.js'
import { historicalCharacterNames } from '../conversation/participants.js'
import { legacyScenario, validatePythonChat } from './python-format.js'
import wavefile from 'wavefile'

const FORMAT = 'd_sakiko.portable_chat'
const limit = 512 * 1024 * 1024
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
const ref = (kind, id) => 'backup-resource://' + kind + '/' + id
const resourceId = (value, kind) =>
  typeof value === 'string' && value.startsWith(ref(kind, ''))
    ? value.slice(ref(kind, '').length)
    : null

function replaceRefs(value, replace) {
  if (Array.isArray(value)) return value.map((item) => replaceRefs(item, replace))
  if (!value || typeof value !== 'object') return value
  if (Object.keys(value).length === 1 && typeof value.id === 'string') return replace(value.id)
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, replaceRefs(item, replace)])
  )
}

// Python 以可见消息下标显示工具。归档往返时把下标绑定到稳定消息，删除和分叉后重新计算。
function portableToolRecords(turns, rows) {
  const positions = new Map(rows.map((row, index) => [row.archive_id, index]))
  const records = []
  for (const turn of turns) {
    let index = rows.findIndex((row) => row.archive_turn === turn.id)
    if (index < 0) continue
    const calls = turn.messages
      .flatMap((msg) => msg.parts ?? [])
      .filter((part) => part.kind === 'tool-call')
    for (const msg of turn.messages) {
      if (positions.has(msg.id)) index = positions.get(msg.id)
      for (const part of msg.parts ?? [])
        for (const line of part.lines ?? []) index = positions.get(line.id) ?? index
      if (msg.kind !== 'tool-result') continue
      const original = msg.extensions?.portableRecord
      const call = calls.find((part) => part.callId === msg.callId)
      if (!call) continue
      const outcome = msg.outcome
      records.push({
        ...original,
        tool_call_id: msg.callId,
        tool_name: call.toolName,
        arguments: call.arguments,
        message_index: positions.get(msg.extensions?.portableRow) ?? index,
        status: original?.status ?? outcome?.status ?? 'interrupted',
        result_content:
          original?.result_content ?? JSON.stringify(outcome?.value ?? outcome?.problem ?? null)
      })
    }
  }
  return records
}

function restorePythonTools(chat, rows, records) {
  const known = new Set(
    chat.turns.flatMap((turn) =>
      turn.messages
        .filter((msg) => msg.kind === 'tool-result')
        .map((msg) => turn.id + ':' + msg.callId)
    )
  )
  for (const record of records ?? []) {
    const row = rows[record.message_index]
    if (!row || !record.tool_call_id || !record.tool_name) continue
    const turn = chat.turns.find((item) => item.id === row.archive_turn)
    if (!turn || known.has(turn.id + ':' + record.tool_call_id)) continue
    const position = turn.messages.findIndex(
      (msg) =>
        msg.id === row.archive_id ||
        msg.parts?.some((part) => part.lines?.some((line) => line.id === row.archive_id))
    )
    if (position < 0) continue
    const id = uuid()
    const call = {
      id: uuid(),
      kind: 'tool-call',
      callId: record.tool_call_id,
      toolName: record.tool_name,
      arguments: record.arguments ?? {}
    }
    let outcome = {
      status: 'interrupted',
      problem: { code: 'imported_call', message: '旧记录未确认工具已完成。' }
    }
    if (record.ok === true || ['success', 'succeeded', 'completed'].includes(record.status))
      outcome = { status: 'succeeded', value: record.result_content }
    if (record.ok === false || record.status === 'failed')
      outcome = {
        status: 'failed',
        problem: { code: 'imported_call_failed', message: record.result_content || '工具执行失败' }
      }
    const offset = turn.messages[position].kind === 'user' ? 1 : 0
    turn.messages.splice(
      position + offset,
      0,
      { id, kind: 'assistant', parts: [call], protocol: {}, extensions: {} },
      {
        id: uuid(),
        kind: 'tool-result',
        assistantMessageId: id,
        callId: call.callId,
        origin: 'tool',
        outcome,
        extensions: { portableRow: row.archive_id, portableRecord: record }
      }
    )
    known.add(turn.id + ':' + record.tool_call_id)
  }
}

function exportChat(original, includeAudio) {
  const chat = structuredClone(original)
  const names = historicalCharacterNames(chat)
  const python = chat.meta.extensions?.portablePython ?? {}
  const rows = []
  for (const turn of chat.turns) {
    turn.meta.requestId = null
    delete turn.meta.extensions?.requestFingerprint
    for (const message of turn.messages) {
      if (message.kind === 'user') {
        const row = {
          ...message.extensions?.portablePython,
          archive_id: message.id,
          archive_turn: turn.id,
          character_name: 'User',
          text: message.content?.text ?? message.text ?? '',
          attachments: []
        }
        for (const item of message.content?.attachments ?? [])
          row.attachments.push({
            type: item.mediaType?.startsWith('image/') ? 'image' : 'file',
            original_name: item.name,
            mime_type: item.mediaType,
            path: ref('attachment', item.asset.id)
          })
        if (message.content) {
          delete message.content.text
          delete message.content.attachments
        }
        delete message.text
        delete message.extensions?.portablePython
        rows.push(row)
      }
      for (const part of message.parts ?? [])
        for (const line of part.lines ?? []) {
          rows.push({
            ...line.extensions?.portablePython,
            archive_id: line.id,
            archive_turn: turn.id,
            character_name: names[line.speakerId] ?? line.speakerName ?? line.speakerId,
            text: line.text,
            translation: line.translation ?? '',
            emotion: line.emotion ?? 'happiness',
            performance: line.performance,
            audio_path: includeAudio && line.audio ? ref('audio', line.audio.asset.id) : 'NO_AUDIO'
          })
          line.archiveTextHash = hash(line.text)
          delete line.text
          delete line.translation
          delete line.extensions?.portablePython
          if (line.audio) {
            if (includeAudio) delete line.audio.asset
            else line.audio = null
          }
        }
    }
  }
  const promptNames = chat.characterIds.map((id) => names[id] ?? id)
  let prompt = python.prompt_config
  if (!prompt)
    prompt =
      chat.mode === 'scripted-dialogue'
        ? { type: 'small_theater', character_names: promptNames }
        : { type: 'single_character', character_name: promptNames[0] ?? '' }
  const meta = { ...python.meta }
  meta.tool_call_records = portableToolRecords(chat.turns, rows)
  delete meta.rolling_summary
  let position = -1
  for (const turn of chat.turns) {
    position += rows.filter((row) => row.archive_turn === turn.id).length
    const summary = turn.messages.find((msg) => msg.kind === 'abstract')
    if (summary && position >= 0)
      meta.rolling_summary = {
        version: 1,
        text: summary.text,
        summary_until_message_index: position
      }
  }
  const persona = chat.userPersona
  if (persona && prompt.type === 'single_character')
    prompt.user_persona = {
      persona_id: persona.personaId,
      name: persona.displayName,
      description: persona.description,
      kind: persona.sourceCharacterId ? 'system_character' : 'custom',
      source_character_folder_name: persona.sourceCharacterId
    }
  const scenario = chat.meta.settings?.scenario
  if (scenario) {
    meta.theater = { situation: scenario.situation }
    for (const [index, id] of chat.characterIds.entries()) {
      const actor = scenario.characters?.[id]
      if (actor)
        meta.theater['character_' + index] = {
          talk_style: actor.speakingStyle,
          interaction_details: actor.interaction
        }
    }
  }
  const turns = chat.turns
  delete chat.turns
  delete chat.meta.extensions?.portablePython
  for (const key of ['acceptedRequests', 'reminderCalls', 'fileCalls'])
    delete chat.meta.extensions?.[key]
  return {
    source_chat_id: chat.id,
    chat: {
      ...python,
      chat_id: chat.id,
      name: chat.title,
      type: python.type ?? (chat.mode === 'scripted-dialogue' ? 2 : 1),
      prompt_config: prompt,
      meta,
      message_list: rows
    },
    turns,
    electron: chat
  }
}

/** @param {object} assets 媒体库。 @param {object[]} chats 对话。 @param {boolean} audio 是否携带历史语音。 @returns {Promise<Uint8Array>} 通用 ZIP。 */
export async function encodeArchive(assets, chats, audio) {
  const entries = chats.map((chat) => exportChat(chat, audio))
  const resources = { audio: Object.create(null), attachment: Object.create(null) }
  const files = Object.create(null)
  const external = new Set()
  const referenced = new Set(extractAssetReferences(entries).map((asset) => asset.id))
  for (const entry of entries)
    for (const row of entry.chat.message_list) {
      const recording = resourceId(row.audio_path, 'audio')
      if (recording) referenced.add(recording)
      for (const item of row.attachments ?? []) {
        const id = resourceId(item.path, 'attachment')
        if (id) referenced.add(id)
      }
    }
  for (const id of referenced) {
    const source = { id }
    const info = await assets.inspect(source)
    if (['model', 'live2d'].includes(info.purpose) || info.kind !== 'file') {
      external.add(source.id)
      continue
    }
    const kind = info.purpose === 'audio' ? 'audio' : 'attachment'
    if (kind === 'audio' && !audio) continue
    const bytes = await assets.read(source)
    const digest = hash(bytes)
    const path = 'resources/' + digest
    files[path] = bytes
    resources[kind][source.id] = {
      zip_path: path,
      sha256: digest,
      size: bytes.byteLength,
      filename: info.name,
      purpose: info.purpose,
      mediaType: info.mediaType
    }
  }
  const portable = replaceRefs(entries, (id) => (external.has(id) ? { externalModel: id } : { id }))
  files['manifest.json'] = strToU8(
    JSON.stringify({
      format: FORMAT,
      version: 1,
      backup_id: uuid(),
      exported_at: new Date().toISOString(),
      chats: portable,
      resources
    })
  )
  requireValue(
    Object.values(files).reduce((total, bytes) => total + bytes.byteLength, 0) <= limit,
    'archive_too_large',
    '归档超过 512 MiB。'
  )
  return zipSync(files, { level: 0 })
}

function pythonFields(row) {
  const extra = { ...row }
  for (const key of [
    'archive_id',
    'archive_turn',
    'character_name',
    'text',
    'translation',
    'audio_path',
    'attachments',
    'emotion',
    'performance'
  ])
    delete extra[key]
  return extra
}

function importChat(entry, media, roles, warnings) {
  const py = entry.chat
  requireValue(Array.isArray(py?.message_list), 'invalid_archive')
  const names =
    py.prompt_config?.character_names ?? [py.prompt_config?.character_name].filter(Boolean)
  if (!names.length)
    for (const row of py.message_list)
      if (
        row.character_name &&
        row.character_name !== 'User' &&
        !names.includes(row.character_name)
      )
        names.push(row.character_name)
  const idFor = (name) =>
    roles.find((role) => role.displayName === name || role.name === name)?.id ??
    'missing-' + hash(name).slice(0, 16)
  const chat = entry.electron ?? {
    id: uuid(),
    revision: 0,
    characterIds: names.map(idFor),
    mode: py.type === 2 ? 'scripted-dialogue' : 'single-character',
    meta: { reminders: [], extensions: {} },
    userPersona: null
  }
  chat.title = py.name
  chat.meta.settings = merge(chatSettings(), chat.meta.settings ?? {})
  chat.meta.extensions ??= {}
  chat.meta.extensions.characterNames ??= Object.fromEntries(
    names.map((name) => [idFor(name), name])
  )
  if (!entry.electron && py.type === 2)
    chat.meta.settings.scenario = legacyScenario(
      py.meta?.theater,
      chat.characterIds,
      py.start_message
    )
  const importedPersona = py.prompt_config?.user_persona
  if (!chat.userPersona && importedPersona?.name)
    chat.userPersona = {
      personaId: importedPersona.persona_id ?? uuid(),
      sourceCharacterId: importedPersona.source_character_folder_name ?? null,
      sourceFormId: null,
      avatar: null,
      displayName: importedPersona.name,
      description: importedPersona.description ?? ''
    }
  const { message_list: rows, ...python } = py
  chat.meta.extensions.portablePython = python
  const byId = new Map()
  function take(row, kind) {
    const id = resourceId(row, kind)
    return id && media.get(id)
  }
  function attachments(row) {
    return (row.attachments ?? []).flatMap((item) => {
      const file = take(item.path, 'attachment')
      if (!file) {
        warnings.push({
          code: 'lossy-import',
          chatId: chat.id,
          message: '归档缺少附件：' + (item.original_name || '未命名附件')
        })
        return []
      }
      return [
        {
          asset: file.asset,
          name: item.original_name || file.name,
          mediaType: item.mime_type || file.mediaType
        }
      ]
    })
  }
  function applyLine(line, row) {
    const changed = line.archiveTextHash && line.archiveTextHash !== hash(row.text ?? '')
    const file = take(row.audio_path, 'audio')
    line.text = row.text ?? ''
    line.translation = row.translation || null
    line.performance ??= row.performance ?? { motion: 'auto', expression: 'auto' }
    if (!Object.hasOwn(line, 'emotion')) line.emotion = row.emotion ?? null
    if (changed || !line.spoken)
      line.spoken = {
        ...line.spoken,
        text: spokenText(line.text),
        language: line.spoken?.language ?? 'ja',
        pronunciationOverrides: []
      }
    line.audio = file
      ? {
          ...line.audio,
          durationMs: file.durationMs ?? line.audio?.durationMs ?? 0,
          asset: file.asset
        }
      : null
    line.extensions = { ...line.extensions, portablePython: pythonFields(row) }
    delete line.archiveTextHash
  }
  for (const row of rows) {
    row.archive_id ||= uuid()
    requireValue(!byId.has(row.archive_id), 'invalid_archive')
    byId.set(row.archive_id, row)
  }
  const templates = new Map()
  for (const turn of entry.turns ?? []) {
    const visible = []
    turn.messages = turn.messages.filter((msg) => {
      if (msg.kind === 'user') {
        const row = byId.get(msg.id)
        if (!row) return false
        visible.push(msg.id)
        if (msg.source?.kind === 'manual' || msg.content)
          msg.content = { ...msg.content, text: row.text, attachments: attachments(row) }
        else if (msg.source?.kind === 'reminder') msg.text = row.text
        msg.extensions = { ...msg.extensions, portablePython: pythonFields(row) }
      }
      for (const part of msg.parts ?? [])
        if (part.kind === 'dialogue') {
          part.lines = part.lines.filter((line) => byId.has(line.id))
          for (const line of part.lines) {
            visible.push(line.id)
            applyLine(line, byId.get(line.id))
          }
        }
      return true
    })
    if (turn.input.kind === 'message' && !byId.has(turn.input.messageId))
      turn.input = { kind: 'scenario' }
    templates.set(turn.id, { turn, visible: new Set(visible) })
  }
  chat.turns = [...templates.values()].map((item) => item.turn)
  let current = null
  for (const row of rows) {
    const known = templates.get(row.archive_turn)
    if (known) current = known.turn
    if (known?.visible.has(row.archive_id)) continue
    if (
      !known &&
      (!current ||
        row.character_name === 'User' ||
        (row.archive_turn && row.archive_turn !== current.id))
    ) {
      current = {
        id: row.archive_turn || uuid(),
        input: { kind: 'scenario' },
        messages: [],
        meta: {
          requestId: null,
          generation: {},
          speech: {},
          characters: {},
          tools: [],
          scenario: chat.meta.settings.scenario,
          worldbook: null,
          worldbookDiagnostics: [],
          extensions: {}
        }
      }
      chat.turns.push(current)
      templates.set(current.id, { turn: current, visible: new Set() })
    }
    row.archive_turn = current.id
    if (row.character_name === 'User') {
      current.input = { kind: 'message', messageId: row.archive_id }
      current.messages.push({
        kind: 'user',
        id: row.archive_id,
        source: { kind: 'manual' },
        content: { text: row.text, attachments: attachments(row) },
        extensions: { portablePython: pythonFields(row) }
      })
    } else {
      const speakerId =
        chat.characterIds.find(
          (id) => chat.meta.extensions.characterNames[id] === row.character_name
        ) ?? idFor(row.character_name || '未知')
      const line = {
        id: row.archive_id,
        speakerId,
        formId: roles.find((role) => role.id === speakerId)?.defaultFormId ?? null
      }
      applyLine(line, row)
      current.messages.push({
        id: uuid(),
        kind: 'assistant',
        parts: [{ id: uuid(), kind: 'dialogue', lines: [line] }],
        protocol: {},
        extensions: {}
      })
    }
  }
  restorePythonTools(chat, rows, py.meta?.tool_call_records)
  const summary = py.meta?.rolling_summary
  const boundary = rows[summary?.summary_until_message_index]?.archive_turn
  if (
    !entry.electron &&
    summary?.version === 1 &&
    typeof summary.text === 'string' &&
    boundary &&
    rows[summary.summary_until_message_index + 1]?.archive_turn !== boundary
  ) {
    chat.turns
      .find((turn) => turn.id === boundary)
      ?.messages.push({ id: uuid(), kind: 'abstract', text: summary.text, extensions: {} })
  }
  for (const id of chat.characterIds)
    if (!roles.some((role) => role.id === id))
      warnings.push({
        code: 'missing-character',
        chatId: chat.id,
        message: '未安装角色：' + (chat.meta.extensions.characterNames[id] ?? id)
      })
  return chat
}

/** @param {object} assets 媒体库。 @param {Uint8Array} bytes ZIP 内容。 @param {string} owner 临时所有者。 @param {object[]} roles 本地角色。 @returns {Promise<object>} 对话草稿和警告。 */
export async function decodeArchive(assets, bytes, owner, roles) {
  let size = 0
  const files = unzipSync(bytes, {
    filter(entry) {
      size += entry.originalSize
      requireValue(size <= limit, 'archive_too_large', '解压后的归档超过 512 MiB。')
      return true
    }
  })
  requireValue(files['manifest.json'], 'invalid_archive', '归档缺少 manifest.json。')
  const data = JSON.parse(strFromU8(files['manifest.json']))
  requireValue(
    [FORMAT, 'd_sakiko.chat_backup'].includes(data.format) &&
      data.version === 1 &&
      Array.isArray(data.chats),
    'invalid_archive',
    '无法识别对话归档版本。'
  )
  const media = new Map()
  const warnings = (data.warnings ?? []).map((message) => ({ code: 'lossy-import', message }))
  const attachmentTypes = new Map()
  for (const entry of data.chats)
    for (const row of entry.chat?.message_list ?? [])
      for (const item of row.attachments ?? [])
        if (item.mime_type) attachmentTypes.set(resourceId(item.path, 'attachment'), item.mime_type)
  for (const kind of ['attachment', 'audio'])
    for (const [id, item] of Object.entries(data.resources?.[kind] ?? {})) {
      const content = files[item.zip_path]
      requireValue(
        content && hash(content) === item.sha256 && !media.has(id),
        'invalid_archive',
        '归档资源缺失或校验失败。'
      )
      const purpose = item.purpose ?? kind
      requireValue(
        ['chat-image', 'avatar', 'attachment', 'workspace', 'audio'].includes(purpose),
        'invalid_archive'
      )
      const info = await assets.importAsset(
        {
          kind: 'bytes',
          bytes: content,
          name: item.filename,
          mediaType: item.mediaType ?? attachmentTypes.get(id) ?? 'application/octet-stream'
        },
        owner,
        purpose === 'workspace' ? 'attachment' : purpose
      )
      if (kind === 'audio') {
        try {
          const wav = new wavefile.WaveFile(content)
          info.durationMs = (1000 * wav.data.samples.length) / wav.fmt.byteRate
        } catch {
          /* 外部压缩录音保留归档中的时长。 */
        }
      }
      media.set(id, info)
    }
  const chats = []
  for (const item of data.chats) {
    const entry = replaceRefs(item, (id) => media.get(id)?.asset ?? null)
    const chat = importChat(entry, media, roles, warnings)
    validatePythonChat(chat)
    chats.push(chat)
  }
  return { chats, warnings, resourceOwner: owner }
}
