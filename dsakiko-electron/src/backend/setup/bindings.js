import { merge, fingerprint } from '../core/support.js'
import { providerPresets } from '../../shared/contracts/llm-providers.js'

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
export function characterDefinition(role, refs) {
  function reference(value) {
    return { ...value, audio: refs[value.audio] }
  }
  return {
    id: role.id,
    name: role.name,
    revision: 0,
    defaultFormId: role.forms[0].id,
    forms: role.forms.map((form) => ({
      id: form.id,
      displayName: form.displayName,
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
    extensions: {}
  }
}

/** 仅转换明确的当前配置字段；未支持项留在后端导入记录，不将密钥发给调用者。 */
export async function importConnections(settings, config, builtinConnection) {
  const legacy = config?.llm_setting ?? {}
  const candidates = [
    {
      name: '旧版连接',
      provider: legacy.llm_api_provider,
      model: legacy.llm_api_model,
      baseUrl: legacy.llm_api_base_url,
      secret: legacy.llm_api_key,
      selected: !legacy.use_default_deepseek_api && !legacy.enable_custom_llm_api_provider
    },
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
  let selected = null
  for (const item of candidates) {
    if (!item.model && !item.secret && !item.baseUrl) continue
    let model = String(item.model ?? '')
    let provider = item.provider
    const prefix = model.split('/')[0]
    const aliases = {
      google: 'gemini',
      gemini: 'gemini',
      doubao: 'volcengine',
      custom: 'openai-compatible',
      deepseek: 'deepseek',
      openai: 'openai',
      anthropic: 'anthropic',
      moonshot: 'moonshot',
      openrouter: 'openrouter',
      volcengine: 'volcengine'
    }
    provider = aliases[provider] ?? provider
    if (model.includes('/') && Object.hasOwn(aliases, prefix)) {
      model = model.slice(prefix.length + 1)
      provider ??= aliases[prefix]
    }
    const preset = providerPresets.find((value) => value.id === provider)
    if (!preset || !model) {
      pending.push(item)
      continue
    }
    const id = 'legacy-' + fingerprint({ provider, baseUrl: item.baseUrl, model }).slice(0, 24)
    try {
      const existing = (await settings.listConnections()).find((connection) => connection.id === id)
      if (!existing)
        await settings.saveConnection(
          {
            id,
            revision: 0,
            name: item.name,
            provider,
            protocol: preset.protocol,
            baseUrl: item.baseUrl || preset.baseUrl,
            behavior: 'auto',
            models: [{ id: model, declared: {}, overrides: {} }]
          },
          item.secret ? { kind: 'replace', secret: item.secret } : { kind: 'remove' }
        )
      if (item.selected) selected = { connectionId: id, modelId: model }
    } catch (error) {
      if (!error.problem) throw error
      pending.push(item)
    }
  }
  if (legacy.use_default_deepseek_api && builtinConnection)
    selected = await installBuiltinConnection(settings, builtinConnection)
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
