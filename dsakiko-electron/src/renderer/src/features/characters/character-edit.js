/** @returns {import('../../../../shared/contracts/characters.js').CharacterDefinition} 只含一个默认形态的新角色草稿。 */
export function newCharacter() {
  return {
    id: crypto.randomUUID(),
    revision: 0,
    name: '',
    defaultFormId: 'default',
    forms: [
      {
        id: 'default',
        displayName: '',
        description: '',
        avatar: null,
        themeColor: null,
        voice: null,
        models: [],
        defaultModelId: null
      }
    ],
    knowledgeMappings: [],
    extensions: {}
  }
}

/** @param {object} source 当前人格。 @returns {object} 使用独立身份的新草稿，仅复制头像和配色。 */
export function newForm(source) {
  return {
    id: crypto.randomUUID(),
    displayName: '',
    description: '',
    avatar: source.avatar,
    themeColor: source.themeColor,
    models: [],
    defaultModelId: null,
    voice: null
  }
}

function equal(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

function mergeFields(base, edited, latest, keys) {
  for (const key of keys) {
    if (equal(base[key], edited[key])) continue
    if (!equal(base[key], latest[key]) && !equal(edited[key], latest[key]))
      throw new Error('同一项资料已被修改，请重新读取后再编辑。')
    if (edited[key] === undefined) delete latest[key]
    else latest[key] = structuredClone(edited[key])
  }
}

/** @param {object} base 打开时的定义。 @param {object} edited 用户草稿。 @param {object} current 最新定义。 @returns {object} 只合并用户实际编辑的字段，保留同期安装的服装等资源。 */
export function mergeCharacterEdit(base, edited, current) {
  if (!current) throw new Error('角色已删除，请重新打开角色管理。')
  const next = structuredClone(current)
  next.name ??= next.forms.find((form) => form.id === next.defaultFormId).displayName
  mergeFields(base, edited, next, ['name', 'defaultFormId', 'bangDreamId', 'selfNameMode'])
  next.name = next.name.trim()
  if (!next.name) throw new Error('请填写角色名称。')
  for (const form of edited.forms) {
    const previous = base.forms.find((item) => item.id === form.id)
    const target = next.forms.find((item) => item.id === form.id)
    if (!previous && !target) {
      next.forms.push(structuredClone(form))
      continue
    }
    if (!previous || !target) throw new Error('角色人格已变更，请重新读取。')
    mergeFields(previous, form, target, [
      'displayName',
      'description',
      'avatar',
      'themeColor',
      'voice',
      'inactiveVoice',
      'replyVoiceEnabled',
      'voiceExplicit'
    ])
    if (
      (base.name !== next.name || !base.revision) &&
      form.displayName === previous.displayName &&
      target.displayName === previous.displayName &&
      previous.displayName === base.name
    )
      target.displayName = next.name
  }
  const names = new Set()
  for (const form of next.forms) {
    form.displayName =
      form.displayName.trim() ||
      (next.forms.length === 1 && next.selfNameMode !== 'form' ? next.name : '')
    if (!form.displayName || names.has(form.displayName))
      throw new Error('请为每个人格填写不同的名称。')
    names.add(form.displayName)
  }
  if (!base.revision && !next.forms[0].description.trim()) throw new Error('请填写介绍提示词。')
  return next
}
