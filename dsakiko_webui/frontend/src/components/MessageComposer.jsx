import { Camera, Images, LoaderCircle, Send, Square, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import cameraIcon from '../../相机.svg?url'
import microphoneIcon from '../../麦克风.svg?url'
import { IconButton } from './IconButton'

const SUPPORTED_IMAGE_TYPES = 'image/png,image/jpeg,image/webp,image/gif'

export function MessageComposer({
  value,
  onChange,
  onSend,
  busy,
  onCancel,
  characterName,
  attachments = [],
  imageInputSupported = false,
  onAddImages,
  onRemoveAttachment,
}) {
  const textareaRef = useRef(null)
  const albumInputRef = useRef(null)
  const cameraInputRef = useRef(null)
  const cameraAnchorRef = useRef(null)
  const [imageMenuOpen, setImageMenuOpen] = useState(false)
  const uploading = attachments.some((attachment) => attachment.status === 'uploading')
  const canSend = Boolean(value.trim() || attachments.length)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 96)}px`
  }, [value])

  useEffect(() => {
    if (!imageMenuOpen) return undefined
    const closeOnOutsidePress = (event) => {
      if (!cameraAnchorRef.current?.contains(event.target)) setImageMenuOpen(false)
    }
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setImageMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeOnOutsidePress)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePress)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [imageMenuOpen])

  const submit = (event) => {
    event.preventDefault()
    if (!busy && !uploading && canSend) onSend()
  }

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      if (!busy && !uploading && canSend) onSend()
    }
  }

  const selectImages = (event) => {
    const files = event.target.files
    if (files?.length) onAddImages(files)
    event.target.value = ''
    setImageMenuOpen(false)
  }

  return (
    <form className="message-composer" onSubmit={submit}>
      {attachments.length > 0 && (
        <div className="composer-attachments" aria-label="待发送图片">
          {attachments.map((attachment) => (
            <div className="composer-attachment" key={attachment.localId}>
              <img src={attachment.previewUrl} alt={attachment.name} />
              {attachment.status === 'uploading' && (
                <span className="composer-attachment__loading" aria-label="正在上传">
                  <LoaderCircle size={16} />
                </span>
              )}
              <IconButton
                label={`移除${attachment.name}`}
                className="composer-attachment__remove"
                onClick={() => onRemoveAttachment(attachment.localId)}
              >
                <X size={13} />
              </IconButton>
            </div>
          ))}
        </div>
      )}

      <div ref={cameraAnchorRef} className="composer-camera-anchor">
        <IconButton
          label="添加图片"
          className="composer-tool composer-camera"
          disabled={busy}
          aria-expanded={imageMenuOpen}
          onClick={() => setImageMenuOpen((open) => !open)}
        >
          <img src={cameraIcon} alt="" />
        </IconButton>

        {imageMenuOpen && (
          <div className="image-source-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              disabled={!imageInputSupported || attachments.length >= 4}
              onClick={() => albumInputRef.current?.click()}
            >
              <Images size={19} />
              <span>从相册选择</span>
            </button>
            <button
              type="button"
              role="menuitem"
              disabled={!imageInputSupported || attachments.length >= 4}
              onClick={() => cameraInputRef.current?.click()}
            >
              <Camera size={19} />
              <span>拍照</span>
            </button>
            {!imageInputSupported && (
              <p>当前模型不支持发送图片</p>
            )}
          </div>
        )}

        <input
          ref={albumInputRef}
          className="visually-hidden"
          type="file"
          accept={SUPPORTED_IMAGE_TYPES}
          multiple
          tabIndex={-1}
          onChange={selectImages}
        />
        <input
          ref={cameraInputRef}
          className="visually-hidden"
          type="file"
          accept={SUPPORTED_IMAGE_TYPES}
          capture="environment"
          tabIndex={-1}
          onChange={selectImages}
        />
      </div>
      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder={`发消息给${characterName || '角色'}`}
        aria-label="消息"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      {/*<IconButton*/}
      {/*  label="语音输入（暂未启用）"*/}
      {/*  className="composer-tool composer-mic"*/}
      {/*  onClick={() => undefined}*/}
      {/*>*/}
      {/*  <img src={microphoneIcon} alt="" />*/}
      {/*</IconButton>*/}
      {busy ? (
        <IconButton
          label="停止生成"
          className="composer-action composer-action--stop"
          onClick={onCancel}
        >
          <Square size={19} fill="currentColor" />
        </IconButton>
      ) : (
        <IconButton
          label="发送"
          className="composer-action composer-action--send"
          disabled={!canSend || uploading}
          onClick={onSend}
        >
          <Send size={20} />
        </IconButton>
      )}
    </form>
  )
}
