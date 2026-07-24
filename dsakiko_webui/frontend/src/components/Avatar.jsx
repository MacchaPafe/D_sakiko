export function Avatar({ character, size = 'medium' }) {
  const name = character?.name || '？'
  return (
    <span
      className={`avatar avatar--${size}`}
      style={{ '--avatar-accent': character?.accent || '#64748b' }}
      aria-hidden="true"
    >
      {character?.avatar_url ? (
        <img src={character.avatar_url} alt="" />
      ) : (
        <span>{name.slice(0, 1)}</span>
      )}
    </span>
  )
}
