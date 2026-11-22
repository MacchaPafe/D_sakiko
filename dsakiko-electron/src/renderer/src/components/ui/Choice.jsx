import { Button } from './button.jsx'
import { SelectField } from './SelectField.jsx'

/** @param {object} props 可选项、当前值与选择回调。 @returns {import('react').ReactElement} 按选项数量呈现的人格或角色选择器。 */
export function Choice({ options, value, label, disabled, onChange }) {
  if (options.length === 1 || disabled) {
    return (
      <strong className="theatre-choice-static">
        {options.find((item) => item.value === value)?.label}
      </strong>
    )
  }
  if (options.length < 4) {
    return (
      <div className="theatre-choice-group" role="group" aria-label={label}>
        {options.map((item) => (
          <Button
            key={item.value}
            variant="ghost"
            type="button"
            size="sm"
            aria-pressed={value === item.value}
            onClick={() => onChange(item.value)}
          >
            {item.label}
          </Button>
        ))}
      </div>
    )
  }
  return <SelectField options={options} value={value} label={label} onChange={onChange} />
}
