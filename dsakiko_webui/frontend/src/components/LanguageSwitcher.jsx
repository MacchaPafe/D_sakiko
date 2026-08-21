import { Languages } from 'lucide-react'
import { IconButton } from './IconButton'

export function LanguageSwitcher({ value, onChange, disabled = false }) {
  const nextValue = value === 'original' ? 'translation' : 'original'
  const label = value === 'original' ? '显示译文' : '显示原文'

  return (
    <IconButton
      label={label}
      className={`language-button ${value === 'translation' ? 'is-translation' : ''}`}
      disabled={disabled}
      onClick={() => onChange(nextValue)}
    >
      <Languages size={21} />
    </IconButton>
  )
}
