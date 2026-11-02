import { fingerprint } from '../core/support.js'
import { chatSettings, defaultSettings } from '../core/defaults.js'
import wavefile from 'wavefile'
import { legacyRelative } from '../setup/legacy.js'

/** 当前 Python chat_list 的一次性转换器；不执行工具，不恢复提醒和进行中的轮次。 */
export class PythonChatImporter {
  constructor({ assets, store, characters }) {
    Object.assign(this, { assets, store, characters })
  }

  /** 使用来源及旧 chat_id 固定身份，提交后崩溃再导入仍只有一份存档。 */
  async read(scan, refs, mapping, signal) {
    const chats = []
    const warnings = []
    for (const [index, original] of scan.chats.entries()) {
      signal?.throwIfAborted()
      const identity = `python-chat:${original.chat_id ?? fingerprint(original)}`
      const id = 'python-' + fingerprint(identity).slice(0, 32)
      if ((await this.store.load(id)).status !== 'missing') continue
      const stable = (suffix) => fingerprint(identity + ':' + suffix).slice(0, 32)
      const names =
        original.prompt_config?.character_names ??
        [original.prompt_config?.character_name].filter(Boolean)
      const roleIds = names.map(
        (name) => mapping[name] ?? 'missing-' + fingerprint(name).slice(0, 16)
      )
      const chat = {
        id,
        revision: 0,
        title: original.name ?? '旧版对话 ' + (index + 1),
        characterIds: roleIds,
        mode: original.type === 2 ? 'scripted-dialogue' : 'single-character',
        userPersona: null,
        meta: {
          settings: chatSettings(),
          reminders: [],
          extensions: { pythonImport: { source: scan.identity, chatId: original.chat_id ?? null } }
        },
        turns: []
      }
      const persona = original.prompt_config?.user_persona
      for (const name of names) {
        const role = scan.roles.find((item) => item.name === name)
        if (!role) continue
        const roleId = mapping[name] ?? role.id
        const actual = await this.characters.get(roleId)
        const selected = original.meta?.character_forms?.[role.id]
        const form = role.forms.find((item) => item.id === selected) ?? role.forms[0]
        const overrides = {}
        for (const option of role.forms) {
          const configured =
            original.meta?.live2d_form_models?.[role.id]?.[option.id] ??
            original.meta?.live2d_models?.[name]
          if (!configured) continue
          try {
            const relative = legacyRelative(scan.root, configured)
            const model = option.models.find((item) => item.file === relative)
            if (
              model &&
              actual?.forms.some(
                (form) =>
                  form.id === option.id && form.models.some((entry) => entry.id === model.id)
              )
            )
              overrides[option.id] = model.id
          } catch {
            warnings.push({ code: 'legacy_chat_model_unavailable', chatId: id })
          }
        }
        let formId = form.id
        if (actual && !actual.forms.some((item) => item.id === form.id))
          formId = actual.defaultFormId
        chat.meta.settings.characters[roleId] = { formId, modelOverrides: overrides }
      }
      if (original.type === 2) {
        const theater = original.meta?.theater ?? {}
        chat.meta.settings.scenario = {
          situation: theater.situation ?? original.start_message ?? '',
          characters: Object.fromEntries(
            roleIds.map((roleId, position) => [
              roleId,
              {
                speakingStyle: theater['character_' + position]?.talk_style ?? '',
                interaction: theater['character_' + position]?.interaction_details ?? ''
              }
            ])
          )
        }
      }
      if (persona && typeof persona === 'object')
        chat.userPersona = {
          personaId: persona.persona_id ?? stable('persona'),
          sourceCharacterId: null,
          sourceFormId: null,
          displayName: persona.name ?? 'User',
          description: persona.description ?? '',
          avatar: null
        }
      let turn
      const endPositions = new Map()
      function newTurn(position) {
        const next = {
          id: stable('turn:' + position),
          input: { kind: 'scenario' },
          meta: {
            requestId: null,
            generation: structuredClone(defaultSettings.generation),
            speech: structuredClone(defaultSettings.speech),
            scenario: structuredClone(chat.meta.settings.scenario),
            characters: structuredClone(chat.meta.settings.characters),
            tools: [],
            worldbook: null,
            worldbookDiagnostics: [],
            extensions: {}
          },
          messages: []
        }
        chat.turns.push(next)
        return next
      }
      for (const [position, message] of (original.message_list ?? []).entries()) {
        const speaker = message.character_name
        if (speaker === 'User' || message.role === 'user') {
          turn = newTurn(position)
          const attachments = []
          for (const attachment of message.attachments ?? []) {
            const key = scan.media[attachment.local_path ?? attachment.path]
            const asset = refs[key]
            if (asset)
              attachments.push({
                asset,
                name: attachment.original_name ?? attachment.name ?? attachment.filename ?? '附件',
                mediaType:
                  attachment.mime_type ?? attachment.media_type ?? 'application/octet-stream'
              })
          }
          const input = {
            id: stable('message:' + position),
            kind: 'user',
            source: { kind: 'manual' },
            content: { text: message.text ?? '', attachments },
            extensions: {}
          }
          turn.input = { kind: 'message', messageId: input.id }
          turn.messages.push(input)
        } else if (
          ['System', 'system', 'abstract'].includes(speaker) ||
          message.type === 'abstract'
        ) {
          turn ??= newTurn(position)
          turn.messages.push({
            id: stable('message:' + position),
            kind: 'abstract',
            text: message.text ?? '',
            extensions: {}
          })
        } else {
          turn ??= newTurn(position)
          const speakerId =
            mapping[speaker] ??
            roleIds[0] ??
            'missing-' + fingerprint(speaker ?? 'unknown').slice(0, 16)
          if (!chat.characterIds.includes(speakerId)) chat.characterIds.push(speakerId)
          const audioRef = refs[scan.media[message.audio_path]]
          let audio = null
          if (audioRef) {
            try {
              const info = await this.assets.inspect(audioRef)
              if (info.sizeBytes <= 64 * 1024 * 1024) {
                const wav = new wavefile.WaveFile(await this.assets.read(audioRef))
                const durationMs = (wav.data.samples.length / wav.fmt.byteRate) * 1000
                if (Number.isFinite(durationMs) && durationMs > 0)
                  audio = { asset: audioRef, durationMs }
              }
            } catch {
              warnings.push({ code: 'legacy_audio_not_playable', chatId: id })
            }
          }
          const role = scan.roles.find((item) => item.name === speaker)
          let formId = null
          if (role?.forms.length === 1) formId = role.forms[0].id
          if (
            typeof message.form_id === 'string' &&
            role?.forms.some((item) => item.id === message.form_id)
          )
            formId = message.form_id
          const line = {
            id: stable('line:' + position),
            speakerId,
            formId,
            text: message.text ?? '',
            translation: message.translation || null,
            emotion: message.emotion ?? null,
            spoken: { text: message.text ?? '', language: 'ja', pronunciationOverrides: [] },
            performance: {
              motion: message.performance?.motion ?? null,
              expression: message.performance?.expression ?? null
            },
            audio,
            extensions: {}
          }
          turn.messages.push({
            id: stable('message:' + position),
            kind: 'assistant',
            parts: [{ id: stable('part:' + position), kind: 'dialogue', lines: [line] }],
            protocol: {},
            extensions: {}
          })
        }
        endPositions.set(turn.id, position)
      }
      const summary = original.meta?.rolling_summary
      if (
        summary?.version === 1 &&
        typeof summary.text === 'string' &&
        Number.isInteger(summary.summary_until_message_index)
      ) {
        const covered = chat.turns.find(
          (item) => endPositions.get(item.id) === summary.summary_until_message_index
        )
        if (covered)
          covered.messages.push({
            id: stable('summary'),
            kind: 'abstract',
            text: summary.text,
            extensions: {}
          })
        else {
          chat.meta.extensions.pythonImport.summary = summary.text
          warnings.push({ code: 'legacy_summary_boundary_unmapped', chatId: id })
        }
      }
      chats.push(chat)
    }
    return { chats, warnings }
  }
}
