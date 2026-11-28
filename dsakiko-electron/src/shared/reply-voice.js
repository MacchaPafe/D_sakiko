/** 每种情绪的一条参考录音与文本、语言构成不可拆开的更新单位。 */
export const emotionNames = {
  happiness: '高兴',
  sadness: '悲伤',
  anger: '愤怒',
  surprise: '惊讶',
  fear: '恐惧',
  disgust: '厌恶',
  like: '喜欢'
}

/** @param {object|null} voice 形态已填写的材料。 @returns {string[]} 启用回复语音前仍缺少的项目。 */
export function missingVoiceMaterials(voice) {
  const missing = []
  if (!voice?.gptWeights) missing.push('GPT 模型')
  if (!voice?.sovitsWeights) missing.push('SoVITS 模型')
  function inspect(reference, label) {
    if (!reference?.audio) missing.push(label + '录音')
    if (!reference?.text?.trim()) missing.push(label + '文本')
    if (!reference?.language?.trim()) missing.push(label + '语言')
  }
  inspect(voice?.defaultReference, '默认参考')
  for (const [emotion, reference] of Object.entries(voice?.emotions ?? {}))
    inspect(reference, (emotionNames[emotion] ?? emotion) + '参考')
  return missing
}

/** @param {object} form 形态。 @returns {object|null} 包含未启用或部分填写的材料。 */
export function configuredVoice(form) {
  return form.voice ?? form.inactiveVoice ?? null
}

/** @param {object} form 形态草稿。 @param {object|null} materials 完整或部分材料。 @param {boolean} enabled 是否启用。 */
export function setReplyVoice(form, materials, enabled) {
  form.voice = enabled ? materials : null
  if (!enabled && materials) form.inactiveVoice = materials
  else delete form.inactiveVoice
  form.replyVoiceEnabled = enabled
  form.voiceExplicit = true
}

/**
 * 判断形态是否已配置任何语音模型、参考音频或语音包绑定。
 * @param {object|null} form 形态对象。
 * @returns {boolean} 存在任一语音材料或绑定时返回 true。
 */
export function hasAnyVoiceMaterials(form) {
  if (!form) return false
  if (form.voicePackageId) return true
  const voice = configuredVoice(form)
  if (!voice) return false
  if (voice.gptWeights || voice.sovitsWeights) return true
  if (voice.defaultReference?.audio) return true
  return Object.values(voice.emotions ?? {}).some((ref) => Boolean(ref?.audio))
}

/**
 * 清空形态的全部回复语音材料与语音包绑定，便于随后回收未引用的模型文件。
 * @param {object} form 形态草稿。
 */
export function clearReplyVoice(form) {
  form.voice = null
  delete form.inactiveVoice
  delete form.voicePackageId
  delete form.voiceBaseline
  form.replyVoiceEnabled = false
  form.voiceExplicit = true
}
