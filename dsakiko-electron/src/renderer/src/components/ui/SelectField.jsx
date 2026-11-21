import { useId, useLayoutEffect, useRef, useState } from 'react'
import { Check, ChevronRight, ChevronsUpDown } from 'lucide-react'
import { Button } from './button.jsx'
import './select-field.css'

/** @typedef {{value: string, label: string, group?: string, keywords?: string[], disabled?: boolean}} SelectOption 可按分组和别名检索的选项。 */
/** @typedef {{label: string, onAction: () => void}} SelectFooterAction 下拉弹层底部固定操作入口。 */
/** @typedef {{options: SelectOption[], value: string, onChange: (value: string) => void, placeholder?: string, label?: string, disabled?: boolean, footer?: SelectFooterAction}} SelectFieldProps 选项和已确认的值。 */

function normalize(text) {
  return text.normalize('NFKC').trim().toLowerCase()
}

function groupOptions(options) {
  const groups = new Map()
  for (const option of options) {
    const group = option.group ?? ''
    if (!groups.has(group)) groups.set(group, [])
    groups.get(group).push(option)
  }
  return [...groups]
}

function SearchSelect({
  options,
  value,
  onChange,
  placeholder,
  label,
  disabled,
  searchable = true,
  footer
}) {
  const id = useId()
  const input = useRef(null)
  const list = useRef(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(null)
  const selected = options.find((option) => option.value === value)
  const term = searchable ? normalize(query) : ''
  const matches = searchable
    ? options.filter((option) =>
        [option.label, option.group ?? '', ...(option.keywords ?? [])].some((text) =>
          normalize(text).includes(term)
        )
      )
    : options
  const groups = groupOptions(matches)
  const enabled = groups.flatMap(([, items]) => items.filter((option) => !option.disabled))
  const active =
    enabled.find((option) => option.value === focused) ??
    enabled.find((option) => option.value === value) ??
    enabled[0]

  function optionId(option) {
    return id + '-' + options.indexOf(option)
  }
  const activeId = active ? optionId(active) : undefined

  useLayoutEffect(() => {
    if (!open) return
    const popover = list.current
    const anchor = input.current
    // 顶层弹层可越过设置窗的滚动边界，输入仍留在原对话框中。
    function position() {
      const rect = anchor.getBoundingClientRect()
      const below = window.innerHeight - rect.bottom - 12
      const above = rect.top - 12
      const top = below >= 230 || below >= above
      const width = Math.min(window.innerWidth - 24, Math.max(rect.width, 208))
      Object.assign(popover.style, {
        left: Math.min(rect.left, window.innerWidth - width - 12) + 'px',
        width: width + 'px',
        top: top ? rect.bottom + 5 + 'px' : 'auto',
        bottom: top ? 'auto' : window.innerHeight - rect.top + 5 + 'px',
        maxHeight: Math.max(0, Math.min(256, top ? below : above)) + 'px'
      })
    }
    position()
    popover.showPopover()
    window.addEventListener('scroll', position, true)
    window.addEventListener('resize', position)
    return () => {
      popover.hidePopover()
      window.removeEventListener('scroll', position, true)
      window.removeEventListener('resize', position)
    }
  }, [open])

  useLayoutEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' })
  }, [open, activeId])

  function show() {
    setOpen(true)
    setQuery('')
    setFocused(null)
  }
  function change(event) {
    if (!searchable) return
    setQuery(event.target.value)
    setFocused(null)
    setOpen(true)
  }
  function choose(option) {
    if (!option || option.disabled) return
    setOpen(false)
    onChange(option.value)
  }
  function triggerFooter() {
    setOpen(false)
    input.current?.blur()
    footer?.onAction()
  }
  function handleKey(event) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    } else if (event.key === 'Enter' && open) {
      event.preventDefault()
      choose(active)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        show()
        return
      }
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const next = enabled[enabled.indexOf(active) + direction]
      if (next) setFocused(next.value)
    }
  }
  function keepFocus(event) {
    // 滚动条和分组标题也会让输入框失焦，需在整个弹层内保留搜索焦点。
    event.preventDefault()
  }
  function renderOption(option) {
    const picked = option.value === value
    function pick(event) {
      event.preventDefault()
      choose(option)
    }
    return (
      <div
        role="option"
        id={optionId(option)}
        key={option.value}
        aria-selected={picked}
        aria-disabled={option.disabled || undefined}
        data-highlighted={option === active || undefined}
        className="search-select-option"
        onClick={pick}
      >
        <span>{option.label}</span>
        {picked && <Check size={15} aria-hidden="true" />}
      </div>
    )
  }

  let inputValue = selected?.label ?? ''
  if (searchable && open) inputValue = query

  let inputPlaceholder = placeholder
  if (searchable && open) {
    inputPlaceholder = selected ? `${selected.label}（输入以搜索…）` : '输入名称或别名搜索…'
  }

  return (
    <span className="search-select">
      <input
        className="field-input"
        type="text"
        role="combobox"
        aria-label={label}
        aria-autocomplete={searchable ? 'list' : 'none'}
        aria-expanded={open}
        aria-controls={id}
        aria-activedescendant={open ? activeId : undefined}
        value={inputValue}
        placeholder={inputPlaceholder}
        readOnly={!searchable}
        autoComplete="off"
        disabled={disabled}
        onFocus={show}
        onClick={open ? undefined : show}
        onBlur={() => setOpen(false)}
        onChange={change}
        onKeyDown={handleKey}
        ref={input}
      />
      <ChevronsUpDown className="search-select-icon" size={15} aria-hidden="true" />
      <div className="search-select-popover" popover="manual" ref={list} onMouseDown={keepFocus}>
        <div id={id} role="listbox" aria-label={label ?? '选项'}>
          {groups.map(([group, items]) =>
            group ? (
              <div key={group} role="group" aria-label={group}>
                <p aria-hidden="true">{group}</p>
                {items.map(renderOption)}
              </div>
            ) : (
              items.map(renderOption)
            )
          )}
        </div>
        {!matches.length && <p role="status">没有匹配的选项</p>}
        {footer && (
          <div className="search-select-footer">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="search-select-footer-action"
              onClick={triggerFooter}
            >
              <span>{footer.label}</span>
              <ChevronRight size={14} aria-hidden="true" />
            </Button>
          </div>
        )}
      </div>
    </span>
  )
}

/**
 * @param {SelectFieldProps} props 下拉选项及选择回调。
 * @returns {import('react').ReactElement} 长列表或带底部操作入口时使用自定义弹层，短列表使用原生选择框。
 */
export function SelectField({ options, value, onChange, placeholder, label, disabled, footer }) {
  const searchable = options.length > 10
  if (searchable || footer)
    return (
      <SearchSelect
        options={options}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        label={label}
        disabled={disabled}
        searchable={searchable}
        footer={footer}
      />
    )

  function renderOption(option) {
    return (
      <option key={option.value} value={option.value} disabled={option.disabled}>
        {option.label}
      </option>
    )
  }

  return (
    <select
      className="field-input"
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {placeholder && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {groupOptions(options).map(([group, items]) =>
        group ? (
          <optgroup key={group} label={group}>
            {items.map(renderOption)}
          </optgroup>
        ) : (
          items.map(renderOption)
        )
      )}
    </select>
  )
}
