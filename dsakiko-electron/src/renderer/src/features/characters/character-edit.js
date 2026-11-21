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
  mergeFields(base, edited, next, ['name', 'defaultFormId', 'bangDreamId'])
  next.name = next.name.trim()
  if (!next.name) throw new Error('请填写角色名称。')
  for (const form of edited.forms) {
    const previous = base.forms.find((item) => item.id === form.id)
    const target = next.forms.find((item) => item.id === form.id)
    if (!target) throw new Error('角色形态已变更，请重新读取。')
    mergeFields(previous, form, target, [
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
      target.displayName === previous.displayName &&
      previous.displayName === base.name
    )
      target.displayName = next.name
  }
  if (!base.revision && !next.forms[0].description.trim()) throw new Error('请填写介绍提示词。')
  return next
}
