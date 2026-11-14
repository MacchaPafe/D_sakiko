import sakikoAvatar from '../../assets/characters/sakiko.png'
import anonAvatar from '../../assets/characters/anon.png'
import soyoAvatar from '../../assets/characters/soyo.png'

/** @typedef {{id: 'sakiko'|'anon'|'soyo', name: string, avatar: string}} PreviewCharacter 内置预览的展示资料，不替代后端正式角色配置。 */
/** @type {PreviewCharacter[]} */
export const previewCharacters = [
  { id: 'sakiko', name: '祥子', avatar: sakikoAvatar },
  { id: 'anon', name: '爱音', avatar: anonAvatar },
  { id: 'soyo', name: '素世', avatar: soyoAvatar }
]
