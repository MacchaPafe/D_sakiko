export function LanguageSwitcher({ value, onChange, disabled = false }) {
  return (
    <div className="language-switcher" aria-label="文本显示">
      <button
        type="button"
        className={value === 'original' ? 'is-active' : ''}
        aria-pressed={value === 'original'}
        onClick={() => onChange('original')}
      >
        原文
      </button>
      <button
        type="button"
        className={value === 'translation' ? 'is-active' : ''}
        aria-pressed={value === 'translation'}
        disabled={disabled}
        onClick={() => onChange('translation')}
      >
        译文
      </button>
    </div>
  )
}
