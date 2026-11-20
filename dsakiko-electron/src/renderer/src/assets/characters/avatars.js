// 文件名是角色目录中的 ID，图片来自 Python 桌面版的 assets/char_headprof。
const avatars = import.meta.glob('./*.webp', {
  eager: true,
  query: '?no-inline',
  import: 'default'
})

/**
 * @param {string|null|undefined} bangDreamId 已确认的邦角色目录身份。
 * @returns {string|undefined} 随应用打包的头像地址。
 */
export function builtinCharacterAvatar(bangDreamId) {
  return bangDreamId ? avatars[`./${bangDreamId}.webp`] : undefined
}
