import { merge, fingerprint } from '../core/support.js'
import { providerPresets } from '../../shared/contracts/llm-providers.js'
import { normalizeConnection } from '../llm/connections.js'

const sharedFiles = {
  'chinese-roberta-wwm-ext-large/config.json': ['bert', 'config'],
  'chinese-roberta-wwm-ext-large/pytorch_model.bin': ['bert', 'weights'],
  'chinese-roberta-wwm-ext-large/tokenizer.json': ['bert', 'tokenizer'],
  'chinese-hubert-base/config.json': ['hubert', 'config'],
  'chinese-hubert-base/pytorch_model.bin': ['hubert', 'weights'],
  'chinese-hubert-base/preprocessor_config.json': ['hubert', 'featureExtractor'],
  'fast_langdetect/lid.176.bin': ['languageDetector', 'model'],
  'G2PWModel/g2pW.onnx': ['g2pw', 'model'],
  'G2PWModel/config.py': ['g2pw', 'config'],
  'G2PWModel/POLYPHONIC_CHARS.txt': ['g2pw', 'polyphonicCharacters'],
  'G2PWModel/MONOPHONIC_CHARS.txt': ['g2pw', 'monophonicCharacters'],
  'G2PWModel/bopomofo_to_pinyin_wo_tune_dict.json': ['g2pw', 'bopomofoToPinyin'],
  'G2PWModel/char_bopomofo_dict.json': ['g2pw', 'characterBopomofo']
}
const versionFiles = {
  'gsv-v4-pretrained/s2Gv4.pth': ['v4', 'baseSovits'],
  'gsv-v4-pretrained/vocoder.pth': ['v4', 'vocoder'],
  'v2Pro/s2Gv2ProPlus.pth': ['v2ProPlus', 'baseSovits'],
  'v2Pro/s2Gv2Pro.pth': ['v2Pro', 'baseSovits']
}

/** 依文件用途绑定现有设置；保留其他已迁移架构和未识别权重，不启动推理。 */
export async function bindGsv(settings, refs) {
  const resources = { shared: {}, versions: {} }
  for (const [name, ref] of Object.entries(refs)) {
    for (const [suffix, [group, role]] of Object.entries(sharedFiles))
      if (name.endsWith('/' + suffix) || name === suffix) {
        resources.shared[group] ??= {}
        resources.shared[group][role] = ref
      }
    for (const [suffix, [version, role]] of Object.entries(versionFiles))
      if (name.endsWith('/' + suffix) || name === suffix) {
        resources.versions[version] ??= {}
        resources.versions[version][role] = ref
      }
    if (name.endsWith('sv/pretrained_eres2netv2w24s4ep4.ckpt'))
      for (const version of ['v2Pro', 'v2ProPlus']) {
        resources.versions[version] ??= {}
        resources.versions[version].speakerEncoder = ref
      }
  }
  const current = await settings.get()
  await settings.set(
    'speechStartup.gptSovits.resources',
    merge(current.speechStartup.gptSovits.resources, resources),
    { expectedRevision: current.revision }
  )
}

/** 将扫描材料中的文件身份替换成统一媒体引用。 */
export function characterDefinition(role, refs, name = role.name, source = null) {
  function reference(value) {
    return { ...value, audio: refs[value.audio] }
  }
  return {
    id: role.id,
    name,
    revision: 0,
    defaultFormId: role.forms[0].id,
    forms: role.forms.map((form) => ({
      id: form.id,
      displayName: form.displayName === role.name ? name : form.displayName,
      description: form.description,
      avatar: refs[role.avatar] ?? null,
      themeColor: role.themeColor,
      models: form.models.map((model) => ({
        id: model.id,
        name: model.name,
        presentation: { model: refs[model.key] }
      })),
      defaultModelId: form.defaultModelId,
      voice: form.voice
        ? {
            gptWeights: refs[form.voice.gptWeights],
            sovitsWeights: refs[form.voice.sovitsWeights],
            defaultReference: reference(form.voice.defaultReference),
            emotions: Object.fromEntries(
              Object.entries(form.voice.emotions).map(([name, value]) => [name, reference(value)])
            )
          }
        : null
    })),
    knowledgeMappings: [],
    extensions: source ? { pythonImport: { source, roleId: role.id } } : {}
  }
}

const providerAliases = { google: 'gemini', doubao: 'volcengine', custom: 'openai-compatible' }

function dictionary(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null
}

function legacyProviderCandidates(legacy) {
  const modelMap = dictionary(legacy.llm_api_model)
  const keyMap = dictionary(legacy.llm_api_key)
  const urlMap = dictionary(legacy.llm_api_base_url)
  const active = legacy.llm_api_provider
  const selected = !legacy.use_default_deepseek_api && !legacy.enable_custom_llm_api_provider
  if (!modelMap && !keyMap && !urlMap)
    return [
      {
        name: '旧版连接',
        provider: active,
        model: legacy.llm_api_model,
        baseUrl: legacy.llm_api_base_url,
        secret: legacy.llm_api_key,
        selected
      }
    ]

  const providers = new Set([
    ...Object.keys(modelMap ?? {}),
    ...Object.keys(keyMap ?? {}),
    ...Object.keys(urlMap ?? {})
  ])
  if (typeof active === 'string') providers.add(active)
  function value(map, scalar, id) {
    if (map) return Object.hasOwn(map, id) ? map[id] : undefined
    return id === active ? scalar : undefined
  }
  return [...providers]
    .map((id) => ({
      provider: id,
      model: value(modelMap, legacy.llm_api_model, id),
      secret: value(keyMap, legacy.llm_api_key, id),
      baseUrl: value(urlMap, legacy.llm_api_base_url, id),
      selected: selected && id === active
    }))
    .filter(
      (item) =>
        item.secret ||
        (item.selected && item.baseUrl) ||
        (item.model && ['ollama', 'vllm', 'openai-compatible'].includes(item.provider))
    )
}

/** 转换旧版单连接及每厂商字典；未支持项留在私有记录，密钥不进入摘要。 */
export function legacyConnections(config) {
  const legacy = config?.llm_setting ?? {}
  const candidates = [
    ...legacyProviderCandidates(legacy),
    {
      name: '旧版自定义连接',
      provider: 'openai-compatible',
      model: legacy.custom_llm_api_model,
      baseUrl: legacy.custom_llm_api_url,
      secret: legacy.custom_llm_api_key,
      selected: !legacy.use_default_deepseek_api && legacy.enable_custom_llm_api_provider
    }
  ]
  const pending = []
  const definitions = []
  for (const item of candidates) {
    if (!item.model && !item.secret && !item.baseUrl) continue
    let model = typeof item.model === 'string' ? item.model.trim() : ''
    let provider = providerAliases[item.provider] ?? item.provider
    const prefix = model.split('/')[0]
    const prefixed = providerAliases[prefix] ?? prefix
    const knownPrefix = providerPresets.some((item) => item.id === prefixed)
    // OpenRouter 和魔搭的模型 ID 可能本来就以厂商名开头，只移除本连接的 LiteLLM 路由前缀。
    if (
      model.includes('/') &&
      knownPrefix &&
      (!provider ||
        provider === prefixed ||
        (provider === 'openai-compatible' && prefix === 'openai'))
    ) {
      model = model.slice(prefix.length + 1)
      provider ??= prefixed
    }
    if (provider === 'chatgpt' || provider === 'vertex') continue
    const preset = providerPresets.find((value) => value.id === provider)
    if (
      !preset ||
      !model.trim() ||
      (item.baseUrl && typeof item.baseUrl !== 'string') ||
      (item.secret && typeof item.secret !== 'string')
    ) {
      pending.push(item)
      continue
    }
    try {
      const id = 'legacy-' + fingerprint({ provider, baseUrl: item.baseUrl, model }).slice(0, 24)
      const definition = normalizeConnection({
        id,
        revision: 0,
        name: item.name ?? preset.name,
        provider,
        protocol: preset.protocol,
        baseUrl: item.baseUrl || preset.baseUrl,
        behavior: 'auto',
        models: [{ id: model, declared: {}, overrides: {} }]
      })
      definitions.push({ definition, secret: item.secret, selected: item.selected })
    } catch (error) {
      if (!error.problem) throw error
      pending.push(item)
    }
  }
  return { definitions, pending, useBuiltin: legacy.use_default_deepseek_api === true }
}

/**
 * @param {object} config 旧版导入时保存的设置。
 * @returns {import('../../shared/contracts/models.js').ConnectionDefinition|null} 可唯一定位的旧版选中连接，不含凭据。
 */
export function selectedLegacyConnection(config) {
  const { definitions, useBuiltin } = legacyConnections(config)
  if (useBuiltin) return null
  const selected = definitions.filter((item) => item.selected)
  return selected.length === 1 ? selected[0].definition : null
}

/** 连接配置写入私有设置后才计为已迁移。 */
export async function importConnections(settings, config, builtinConnection, onSaved) {
  const { definitions, pending, useBuiltin } = legacyConnections(config)
  let selected = null
  for (const { definition, secret, selected: use } of definitions) {
    const { id } = definition
    const [model] = definition.models
    try {
      const existing = (await settings.listConnections()).find((connection) => connection.id === id)
      if (!existing)
        await settings.saveConnection(
          definition,
          secret ? { kind: 'replace', secret } : { kind: 'remove' }
        )
    } catch (error) {
      if (!error.problem) throw error
      pending.push({ name: definition.name })
      continue
    }
    await onSaved(id)
    if (use) selected = { connectionId: id, modelId: model.id }
  }
  if (useBuiltin && builtinConnection) {
    selected = await installBuiltinConnection(settings, builtinConnection)
    await onSaved(selected.connectionId)
  }
  if (selected) await settings.update({ generation: selected })
  return { pending, selected }
}

/** 发布端提供 UP 凭据，后端只写入私有 Settings，不经任何前端接口传输。 */
export async function installBuiltinConnection(settings, connection) {
  const id = 'builtin-up'
  if (!(await settings.listConnections()).some((item) => item.id === id))
    await settings.saveConnection(
      {
        id,
        revision: 0,
        name: 'UP 的 DeepSeek API',
        provider: 'deepseek',
        protocol: 'chat-completions',
        baseUrl: connection.baseUrl ?? 'https://api.deepseek.com/v1',
        behavior: 'auto',
        models: [{ id: connection.modelId ?? 'deepseek-flash', declared: {}, overrides: {} }]
      },
      { kind: 'replace', secret: connection.secret }
    )
  return { connectionId: id, modelId: connection.modelId ?? 'deepseek-flash' }
}
