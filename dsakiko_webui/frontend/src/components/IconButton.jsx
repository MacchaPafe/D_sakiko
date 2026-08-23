export function IconButton({
  label,
  children,
  className = '',
  variant = 'plain',
  ...buttonProps
}) {
  return (
    <button
      type="button"
      className={`icon-button icon-button--${variant} ${className}`.trim()}
      aria-label={label}
      title={label}
      {...buttonProps}
    >
      {children}
    </button>
  )
}
