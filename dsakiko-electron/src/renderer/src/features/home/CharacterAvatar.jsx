import { useEffect, useState } from 'react'
import { previewCharacters } from '../onboarding/characters.js'

/**
 * 渲染角色头像，优先从后端资源租约读取，缺失时回退到本地内置头像或首字占位。
 * @param {{role?: {id?: string, avatar?: object|null, displayName?: string, name?: string}|null, client: import('../../backend/client.js').BackendClient, size?: number, className?: string}} props 角色摘要、后端连接及头像尺寸。
 * @returns {import('react').ReactElement} 角色头像元素。
 */
export function CharacterAvatar({ role, client, size = 40, className }) {
  const [loaded, setLoaded] = useState(null)
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
  const fallback = previewCharacters.find((item) => item.id === role?.id)?.avatar
  const imageSrc = url ?? fallback
  const customSizeStyle = size === 40 ? undefined : { width: size, height: size }

  if (imageSrc) {
    return (
      <img
        src={imageSrc}
        alt=""
        width={size}
        height={size}
        className={className}
        style={customSizeStyle}
      />
    )
  }

  const label = (role?.displayName ?? role?.name ?? '角').slice(0, 1)
  const placeholderClass = className ? 'avatar-placeholder ' + className : 'avatar-placeholder'
  const placeholderStyle =
    size === 40
      ? undefined
      : { width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.44)) + 'px' }

  return (
    <span className={placeholderClass} aria-hidden="true" style={placeholderStyle}>
      {label}
    </span>
  )
}
