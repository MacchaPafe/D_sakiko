import { builtinCharacterAvatar } from '../../assets/characters/avatars.js'

/** @typedef {{id: 'sakiko'|'anon'|'soyo', name: string, avatar: string}} PreviewCharacter 内置预览的展示资料，不替代后端正式角色配置。 */
/** @type {PreviewCharacter[]} */
export const previewCharacters = [
  { id: 'sakiko', name: '祥子', avatar: builtinCharacterAvatar('sakiko') },
  { id: 'anon', name: '爱音', avatar: builtinCharacterAvatar('anon') },
  { id: 'soyo', name: '素世', avatar: builtinCharacterAvatar('soyo') }
]
