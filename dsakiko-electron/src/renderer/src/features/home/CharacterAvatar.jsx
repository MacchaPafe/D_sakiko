import { useEffect, useState } from 'react'
import * as Avatar from '@radix-ui/react-avatar'
import { builtinCharacterAvatar } from '../../assets/characters/avatars.js'

/** @typedef {{ bangDreamId?: string|null, avatar?: import('../../../../shared/contracts/common.js').AssetRef|null, displayName?: string, name?: string }} AvatarRole 角色头像和经确认的目录身份。 */

/**
 * 渲染角色头像，优先从后端资源租约读取，缺失时回退到本地内置头像或首字占位。
 * @param {{role?: AvatarRole|null, client: import('../../backend/client.js').BackendClient, size?: number, className?: string}} props 角色摘要、后端连接及头像尺寸。
 * @returns {import('react').ReactElement} 角色头像元素。
 */
export function CharacterAvatar({ role, client, size = 40, className }) {
  const [loaded, setLoaded] = useState(null)
  const [failedUrl, setFailedUrl] = useState(null)
  const avatarRef = role?.avatar ?? null

  useEffect(() => {
    let current = true
    let lease
    if (avatarRef) {
      void client.assets
        .access(avatarRef)
        .then((access) => {
          lease = access.leaseId
          if (current) setLoaded({ ref: avatarRef, url: access.url })
          else void client.assets.releaseAccess(lease).catch(() => {})
        })
        .catch(() => {})
    }
    return () => {
      current = false
      if (lease) void client.assets.releaseAccess(lease).catch(() => {})
    }
  }, [client, avatarRef])

  const url = loaded?.ref === avatarRef ? loaded.url : null
  const hasSavedImage = Boolean(url && url !== failedUrl)
  const image = hasSavedImage ? url : builtinCharacterAvatar(role?.bangDreamId)
  const [initial] = (role?.displayName ?? role?.name ?? '').trim()
  const avatarClass = className ? 'character-avatar ' + className : 'character-avatar'

  function handleImageStatus(status) {
    if (status === 'error' && hasSavedImage) setFailedUrl(url)
  }

  return (
    <Avatar.Root
      className={avatarClass}
      aria-hidden="true"
      style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.44)) }}
    >
      {image && (
        <Avatar.Image key={image} src={image} alt="" onLoadingStatusChange={handleImageStatus} />
      )}
      <Avatar.Fallback className="avatar-placeholder">{initial ?? '角'}</Avatar.Fallback>
    </Avatar.Root>
  )
}
