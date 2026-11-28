import { useEffect, useId, useState } from 'react'
import { Play, Square, Trash2 } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Textarea } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { catalogTextSaveDelay } from '../../hooks/use-catalog-editor.js'
import { formatBytes } from '../setup/presentation.js'
import {
  configuredVoice,
  emotionNames,
  hasAnyVoiceMaterials,
  missingVoiceMaterials,
  setReplyVoice
} from '../../../../shared/reply-voice.js'

const languages = Object.entries({
  all_zh: '中文',
  en: '英文',
  all_ja: '日文',
  all_yue: '粤语',
  all_ko: '韩文',
  zh: '中英混合',
  ja: '日英混合',
  yue: '粤英混合',
  ko: '韩英混合',
  auto: '多语种混合',
  auto_yue: '多语种混合（粤语）'
}).map(([value, label]) => ({ value, label }))

function FileSlot({ client, asset, label, extensions, purpose, choose, onChange }) {
  const [name, setName] = useState(null)
  const [audio, setAudio] = useState(null)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const id = asset?.id

  useEffect(() => {
    let current = true
    if (id)
      client.assets.inspect({ id }).then(
        (info) => {
          if (current) setName(info.name)
        },
        () => {
          if (current) setName('文件不可用')
        }
      )
    return () => {
      current = false
    }
  }, [client, id])

  useEffect(() => {
    if (!playing || !id) return
    let current = true
    let lease
    client.assets.access({ id }).then(
      (value) => {
        if (current) {
          lease = value.leaseId
          setAudio(value)
        } else void client.assets.releaseAccess(value.leaseId).catch(() => {})
      },
      (failure) => {
        if (current) {
          setError(failure.message)
          setPlaying(false)
        }
      }
    )
    return () => {
      current = false
      if (lease) void client.assets.releaseAccess(lease).catch(() => {})
    }
  }, [client, id, playing])

  function stopPlayback() {
    setPlaying(false)
    setAudio(null)
  }

  function togglePlayback() {
    if (playing) {
      stopPlayback()
      return
    }
    setError('')
    setPlaying(true)
  }

  function handleAudioError() {
    setError('音频无法播放')
    stopPlayback()
  }

  function handleChoose() {
    if (playing) stopPlayback()
    choose(purpose, extensions, onChange)
  }

  return (
    <div className="role-file">
      <span>
        <strong>{label}</strong>
        <small>{id ? (name ?? '正在读取…') : '未选择'}</small>
      </span>
      <div className="role-file-actions">
        {purpose === 'audio' && id && (
          <Button
            type="button"
            size="xs"
            variant={playing ? 'secondary' : 'ghost'}
            aria-pressed={playing}
            onClick={togglePlayback}
          >
            {playing ? (
              <Square size={12} aria-hidden="true" />
            ) : (
              <Play size={12} aria-hidden="true" />
            )}
            {playing ? '停止' : '试听'}
          </Button>
        )}
        <Button type="button" size="xs" variant="outline" onClick={handleChoose}>
          {id ? '替换' : '选择文件'}
        </Button>
      </div>
      {/* 使用轻量的试听开关替代原生控件条，避免浏览器自带深色播放器破坏暖纸风格。 */}
      {playing && audio && (
        <audio
          hidden
          autoPlay
          aria-label={label}
          src={audio.url}
          onEnded={stopPlayback}
          onError={handleAudioError}
        />
      )}
      {error && (
        <small className="role-file-error" role="alert">
          {error}
        </small>
      )}
    </div>
  )
}

function Reference({ value, label, onChange, ...props }) {
  const textId = useId()
  const reference = value ?? { audio: null, text: '', language: '' }
  const options = [...languages]
  if (reference.language && !options.some((item) => item.value === reference.language))
    options.push({ value: reference.language, label: reference.language })

  function handleAudioChange(audio) {
    onChange({ ...reference, audio })
  }

  function handleTextChange(event) {
    onChange({ ...reference, text: event.target.value }, catalogTextSaveDelay)
  }

  function handleLanguageChange(language) {
    onChange({ ...reference, language })
  }

  return (
    <div className="role-reference">
      <FileSlot
        key={reference.audio?.id}
        {...props}
        label={label + '录音'}
        asset={reference.audio}
        purpose="audio"
        extensions={['wav', 'mp3', 'ogg', 'flac', 'm4a']}
        onChange={handleAudioChange}
      />
      <div className="role-field">
        <label className="role-field-label" htmlFor={textId}>
          录音对应文本
        </label>
        <Textarea id={textId} value={reference.text} rows={2} onChange={handleTextChange} />
      </div>
      <div className="role-field">
        <span className="role-field-label">录音语言</span>
        <SelectField
          label={label + '语言'}
          value={reference.language}
          placeholder="选择语言"
          options={options}
          onChange={handleLanguageChange}
        />
      </div>
    </div>
  )
}

/** @param {object} props 当前形态、草稿资源入口及修改回调。 @returns {import('react').ReactElement} 当前形态的语音编辑区域。 */
export function ReplyVoice({
  client,
  form,
  changeForm,
  choose,
  packageLabel,
  packageAction,
  onDeleteVoice
}) {
  const enableId = useId()
  const [activeEmotion, setActiveEmotion] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteEstimateBytes, setDeleteEstimateBytes] = useState(null)
  const [deleteStatus, setDeleteStatus] = useState('')
  const voice = configuredVoice(form) ?? {
    gptWeights: null,
    sovitsWeights: null,
    defaultReference: null,
    emotions: {}
  }
  const enabled = Boolean(form.voice)
  const hasVoiceData = hasAnyVoiceMaterials(form)
  const missing = missingVoiceMaterials(voice)
  const emotions = voice.emotions ?? {}
  const emotionEntries = [
    ...Object.entries(emotionNames),
    ...Object.keys(emotions)
      .filter((key) => !(key in emotionNames))
      .map((key) => [key, key])
  ]
  const configuredCount = Object.keys(emotions).length
  const activeReference = activeEmotion ? emotions[activeEmotion] : null

  async function handleRequestDeleteVoice() {
    setDeleteStatus('')
    setConfirmDelete(true)
    setDeleteEstimateBytes(null)
    const ids = new Set()
    if (voice.gptWeights?.id) ids.add(voice.gptWeights.id)
    if (voice.sovitsWeights?.id) ids.add(voice.sovitsWeights.id)
    if (voice.defaultReference?.audio?.id) ids.add(voice.defaultReference.audio.id)
    for (const ref of Object.values(emotions)) {
      if (ref?.audio?.id) ids.add(ref.audio.id)
    }
    if (!ids.size) {
      setDeleteEstimateBytes(0)
      return
    }
    const inspected = await Promise.all(
      [...ids].map((assetId) => client.assets.inspect({ id: assetId }).catch(() => null))
    )
    const totalBytes = inspected.reduce((sum, item) => sum + (item?.sizeBytes ?? 0), 0)
    setDeleteEstimateBytes(totalBytes)
  }

  async function handleConfirmDeleteVoice() {
    if (!onDeleteVoice) return
    setDeleteBusy(true)
    setDeleteStatus('')
    try {
      const feedback = await onDeleteVoice()
      setConfirmDelete(false)
      setActiveEmotion(null)
      if (feedback) setDeleteStatus(feedback)
    } finally {
      setDeleteBusy(false)
    }
  }

  function write(next, active = enabled, delay) {
    changeForm(
      (draft) => setReplyVoice(draft, next, active && !missingVoiceMaterials(next).length),
      delay
    )
  }

  function changeEmotion(key, reference, delay) {
    const nextEmotions = { ...emotions }
    if (reference) nextEmotions[key] = reference
    else delete nextEmotions[key]
    write({ ...voice, emotions: nextEmotions }, enabled, delay)
  }

  function addEmotion(key) {
    changeEmotion(key, { audio: null, language: voice.defaultReference?.language ?? '', text: '' })
  }

  function handleToggleVoice(checked) {
    write(voice, checked === true)
  }

  function handleGptWeightsChange(gptWeights) {
    write({ ...voice, gptWeights })
  }

  function handleSovitsWeightsChange(sovitsWeights) {
    write({ ...voice, sovitsWeights })
  }

  function handleDefaultReferenceChange(defaultReference, delay) {
    write({ ...voice, defaultReference }, enabled, delay)
  }

  function handleSelectEmotion(event) {
    const key = event.currentTarget.value
    if (activeEmotion === key) {
      setActiveEmotion(null)
      return
    }
    if (!emotions[key]) addEmotion(key)
    setActiveEmotion(key)
  }

  function handleRemoveActiveEmotion() {
    if (!activeEmotion) return
    changeEmotion(activeEmotion, null)
    setActiveEmotion(null)
  }

  function handleCloseActiveEmotion() {
    setActiveEmotion(null)
  }

  function handleUpdateActiveEmotion(nextReference, delay) {
    if (!activeEmotion) return
    changeEmotion(activeEmotion, nextReference, delay)
  }

  const fileProps = { client, choose }
  return (
    <section className="role-card role-voice-section">
      <div className="role-row">
        <div>
          <h3>语音合成</h3>
          <p className="role-section-desc">
            配置 GPT-SoVITS 模型与参考音频后，可以为角色合成语音。
          </p>
        </div>
        {packageLabel && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!packageAction}
            onClick={packageAction}
          >
            {packageLabel}
          </Button>
        )}
      </div>

      <div className="role-voice-toggle-row">
        <div className="role-voice-toggle">
          <Checkbox
            id={enableId}
            aria-label="启用语音合成"
            checked={enabled}
            disabled={Boolean(missing.length) || deleteBusy}
            onCheckedChange={handleToggleVoice}
          />
          <label htmlFor={enableId}>启用语音合成</label>
        </div>
        {onDeleteVoice && hasVoiceData && !confirmDelete && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="role-danger"
            disabled={deleteBusy}
            onClick={() => void handleRequestDeleteVoice()}
          >
            <Trash2 size={13} aria-hidden="true" />
            删除语音模型
          </Button>
        )}
      </div>

      {confirmDelete && (
        <div className="role-voice-confirm" role="group" aria-label="删除">
          <p>
            删除后将无法为该角色合成语音。
            {deleteEstimateBytes > 0 && `预计释放 ${formatBytes(deleteEstimateBytes)} 空间`}
          </p>
          <div className="role-voice-confirm-actions">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={deleteBusy}
              onClick={() => setConfirmDelete(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={deleteBusy}
              onClick={() => void handleConfirmDeleteVoice()}
            >
              {deleteBusy ? '正在删除…' : '删除'}
            </Button>
          </div>
        </div>
      )}

      {deleteStatus && (
        <p className="role-voice-status" role="status">
          {deleteStatus}
        </p>
      )}

      {missing.length > 0 && (
        <p className="role-voice-hint" role="note">
          启用前还需填写{missing.join('、')}。
        </p>
      )}

      <div className="role-file-group">
        <FileSlot
          key={'gpt-' + voice.gptWeights?.id}
          {...fileProps}
          label="GPT 模型"
          asset={voice.gptWeights}
          purpose="model"
          extensions={['ckpt']}
          onChange={handleGptWeightsChange}
        />
        <FileSlot
          key={'sovits-' + voice.sovitsWeights?.id}
          {...fileProps}
          label="SoVITS 模型"
          asset={voice.sovitsWeights}
          purpose="model"
          extensions={['pth']}
          onChange={handleSovitsWeightsChange}
        />
      </div>

      <Reference
        {...fileProps}
        label="默认参考"
        value={voice.defaultReference}
        onChange={handleDefaultReferenceChange}
      />

      <div className="role-emotions-block">
        <div className="role-row">
          <div className="role-emotions-heading">
            <h4>情绪参考音频</h4>
            <span className="role-emotion-count">
              已配置 {configuredCount}/{emotionEntries.length}
            </span>
          </div>
        </div>
        <p className="role-section-desc">可选配置不同情绪的参考录音。</p>
        <div className="role-emotion-pills" role="group" aria-label="情绪参考音频列表">
          {emotionEntries.map(([key, label]) => {
            const configured = Boolean(emotions[key])
            const selected = activeEmotion === key
            return (
              <button
                key={key}
                type="button"
                value={key}
                className="role-emotion-pill"
                data-configured={configured || undefined}
                aria-pressed={selected}
                onClick={handleSelectEmotion}
              >
                <span className="role-emotion-dot" aria-hidden="true" />
                <span>{label}</span>
              </button>
            )
          })}
        </div>
        {activeReference && (
          <div className="role-emotion-editor">
            <div className="role-row">
              <h5>{emotionNames[activeEmotion] ?? activeEmotion}参考音频</h5>
              <div className="role-actions">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="role-danger"
                  onClick={handleRemoveActiveEmotion}
                >
                  移除
                </Button>
                <Button type="button" variant="ghost" size="xs" onClick={handleCloseActiveEmotion}>
                  收起
                </Button>
              </div>
            </div>
            <Reference
              {...fileProps}
              label={emotionNames[activeEmotion] ?? activeEmotion}
              value={activeReference}
              onChange={handleUpdateActiveEmotion}
            />
          </div>
        )}
      </div>
    </section>
  )
}
