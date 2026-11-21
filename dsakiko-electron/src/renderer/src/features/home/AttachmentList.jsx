import { useRef, useSyncExternalStore } from 'react'
import { Plus, X, FileText, RotateCw } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'

/** @param {object} props 当前草稿及原生选择器文案。 @returns {import('react').ReactElement} 附件多选入口。 */
export function AddAttachments({ draft, label = '添加附件', disabled = false, showLabel = false }) {
  const picker = useRef(null)
  function change(event) {
    draft.add([...event.target.files])
    event.target.value = ''
  }
  return (
    <>
      <input ref={picker} type="file" multiple hidden onChange={change} />
      <Button
        type="button"
        size={showLabel ? 'sm' : 'icon-sm'}
        variant="ghost"
        aria-label={label}
        disabled={disabled}
        onClick={() => picker.current.click()}
      >
        <Plus />
        {showLabel && label}
      </Button>
    </>
  )
}

/** @param {object} props 草稿附件集合。 @returns {import('react').ReactElement|null} 导入状态、缩略图和逐个重试操作。 */
export function AttachmentList({ draft, disabled = false }) {
  const items = useSyncExternalStore(draft.subscribe, draft.getSnapshot)
  if (!items.length) return null
  return (
    <div className="draft-attachments">
      {items.map((item) => (
        <div className="attachment-chip" key={item.id}>
          {item.preview ? <img src={item.preview} alt="" /> : <FileText size={16} />}
          <span title={item.error ?? item.name}>
            {item.name}
            {item.state === 'importing' && ' · 导入中'}
            {item.state === 'failed' && ' · ' + item.error}
          </span>
          {item.state === 'failed' && (
            <button
              type="button"
              aria-label={'重试 ' + item.name}
              onClick={() => draft.import(item)}
            >
              <RotateCw size={14} />
            </button>
          )}
          <button
            type="button"
            disabled={disabled}
            aria-label={'移除 ' + item.name}
            onClick={() => draft.remove(item)}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
