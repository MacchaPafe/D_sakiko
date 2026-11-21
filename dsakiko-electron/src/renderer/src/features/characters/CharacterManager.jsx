import { useEffect, useId, useRef, useState } from 'react'
import { ArrowUp, Plus, Search, Shirt, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Input, Textarea } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { desktopHost } from '../../host/desktop.js'
import { characterPreviewVars, characterSidebarGroupVars } from '../../styles/character-theme.js'
import { CharacterAvatar } from '../home/CharacterAvatar.jsx'
import { characterFormName } from '../home/character-form.js'
import { ReplyVoice } from './ReplyVoice.jsx'
import { mergeCharacterEdit, newCharacter } from './character-edit.js'
import './characters.css'

/** @param {object} props 角色目录、主页导航和资源服务。 @returns {import('react').ReactElement} 单层浮窗中的角色管理页面。 */
export function CharacterManager({
  client,
  roles,
  initialId,
  focusAffiliation,
  active,
  onRefresh,
  onClose,
  onChat,
  onOutfits,
  onPackage,
  packages,
  completedJob
}) {
  const nameInputId = useId()
  const promptInputId = useId()
  const themeInputId = useId()
  const keepPersonasId = useId()
  const [id, setId] = useState(
    () => roles.find((role) => role.id === initialId)?.id ?? roles[0]?.id ?? null
  )
  const [search, setSearch] = useState('')
  const [base, setBase] = useState(null)
  const [edited, setEdited] = useState(null)
  const [formId, setFormId] = useState(null)
  const [directory, setDirectory] = useState([])
  // 当角色原本为“其他角色”但用户切到 BanG Dream! 选项且尚未选定具体角色时，临时保持下拉框展开。
  const [affiliationOverride, setAffiliationOverride] = useState(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const [message, setMessage] = useState('')
  const [impact, setImpact] = useState(null)
  const [keep, setKeep] = useState(true)
  const [reload, setReload] = useState(0)
  const draft = useRef(null)
  const affiliation = useRef(null)
  const promptFile = useRef(null)
  const busyRef = useRef(false)
  const dirty = Boolean(edited && base && edited !== base)
  const isNew = !base?.revision
  const locked = dirty || busy
  const form = edited?.forms.find((item) => item.id === formId)
  const formThemeStyle = characterPreviewVars(form?.themeColor)
  const packageOption = packages?.find((item) => item.characterId === id)

  useEffect(() => {
    let live = true
    client.outfits.listCharacters().then(
      (items) => {
        if (live) setDirectory(items)
      },
      (error) => {
        if (live) setProblem(error.message)
      }
    )
    return () => {
      live = false
    }
  }, [client])

  function clearSelection() {
    setBase(null)
    setEdited(null)
    setImpact(null)
    setAffiliationOverride(null)
    setProblem('')
    setMessage('')
  }

  function selectId(next) {
    if (next === id) return
    clearSelection()
    setId(next)
  }

  useEffect(() => {
    let live = true
    async function read() {
      const value = id ? await client.characters.get(id) : newCharacter()
      if (!live) return
      if (!value) throw new Error('找不到该角色。')
      value.name ??= value.forms.find((item) => item.id === value.defaultFormId).displayName
      setBase(value)
      setEdited(value)
      setFormId(value.defaultFormId)
    }
    void read().catch((error) => {
      if (live) setProblem(error.message)
    })
    return () => {
      live = false
    }
  }, [client, id, reload])

  const lastCompletedJob = useRef(completedJob)
  const wasActive = useRef(active)
  useEffect(() => {
    const returned = active && !wasActive.current
    wasActive.current = active
    const updated = completedJob !== lastCompletedJob.current
    if (!active || dirty || !id || (!updated && !returned)) return
    lastCompletedJob.current = completedJob
    let live = true
    client.characters.get(id).then(
      (value) => {
        if (live && value) {
          setBase(value)
          setEdited(value)
        }
      },
      (error) => {
        if (live) setProblem(error.message)
      }
    )
    return () => {
      live = false
    }
  }, [active, dirty, id, client, completedJob])

  const loadedId = base?.id
  useEffect(() => {
    if (focusAffiliation && initialId === id && directory.length && loadedId)
      affiliation.current?.querySelector('input,button,select')?.focus()
  }, [focusAffiliation, initialId, id, directory, loadedId])

  useEffect(
    () => () => {
      if (draft.current) void client.assets.discardDraft(draft.current).catch(() => {})
    },
    [client]
  )

  async function releaseDraft() {
    const previous = draft.current
    draft.current = null
    if (previous) await client.assets.discardDraft(previous)
  }

  async function perform(operation) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setProblem('')
    setMessage('')
    try {
      await operation()
    } catch (error) {
      setProblem(error.message)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  function change(update) {
    setEdited((previous) => {
      const value = structuredClone(previous)
      update(value)
      return value
    })
    setMessage('')
    setImpact(null)
  }

  function changeForm(update) {
    change((value) => update(value.forms.find((item) => item.id === formId)))
  }

  function choose(purpose, extensions, apply) {
    return perform(async () => {
      if (!draft.current) draft.current = (await client.assets.createDraft()).draftId
      const [item] = await desktopHost().importFiles({
        purpose,
        draftId: draft.current,
        single: true,
        extensions
      })
      if (!item) return
      if (item.libraryItemId) await client.assets.removeLibraryItem(item.libraryItemId)
      if (!extensions.includes(item.name.split('.').pop().toLowerCase()))
        throw new Error('请选择 ' + extensions.join(' / ') + ' 文件。')
      apply(item.asset)
    })
  }

  function save() {
    return perform(async () => {
      const latest = id ? await client.characters.get(id) : base
      const next = mergeCharacterEdit(base, edited, latest)
      const previewedForms =
        edited.bangDreamId && edited.bangDreamId !== base.bangDreamId
          ? edited.forms.map((item) => item.id)
          : []
      const saved = await client.characters.save(next, previewedForms)
      setBase(saved)
      setEdited(saved)
      setId(saved.id)
      setAffiliationOverride(null)
      await releaseDraft()
      await onRefresh()
      setMessage('角色资料已保存')
    })
  }

  function discard() {
    return perform(async () => {
      await releaseDraft()
      clearSelection()
      setReload((value) => value + 1)
    })
  }

  function previewDeletion() {
    return perform(async () => {
      setImpact(await client.characters.previewDeletion(id))
      setKeep(true)
    })
  }

  function remove() {
    return perform(async () => {
      await client.characters.delete(id, base.revision, { stamp: impact.stamp, keepPersonas: keep })
      selectId(roles.find((item) => item.id !== id)?.id ?? null)
      await onRefresh(id)
    })
  }

  function importPrompt(event) {
    const file = event.target.files[0]
    event.target.value = ''
    if (file)
      void perform(async () => {
        const description = await file.text()
        changeForm((item) => {
          item.description = description.replace(/^\uFEFF/u, '')
        })
      })
  }

  function exportPrompt() {
    const url = URL.createObjectURL(
      new Blob([form.description], { type: 'text/plain;charset=utf-8' })
    )
    const link = document.createElement('a')
    link.href = url
    link.download = (edited.name || '角色') + '-' + form.id + '.txt'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const options = directory.map((entry) => ({
    value: entry.id,
    label: entry.displayName,
    group: entry.band ?? '其他 BanG Dream! 角色',
    keywords: [entry.name, entry.id, ...entry.aliases]
  }))

  const matches = roles.filter((item) => {
    const entry = directory.find((candidate) => candidate.id === item.bangDreamId)
    return [item.name, item.displayName, item.id, ...(entry?.aliases ?? [])].some((name) =>
      name?.toLowerCase().includes(search.trim().toLowerCase())
    )
  })

  function selectAffiliation(value) {
    setAffiliationOverride(null)
    const nextId = value || null
    if (edited.bangDreamId === nextId) return
    const entry = directory.find((item) => item.id === nextId)
    change((role) => {
      role.bangDreamId = nextId
      if (entry) {
        for (const item of role.forms) item.themeColor = entry.themeColor
      }
    })
  }

  function chooseAffiliationMode(event) {
    const next = event.currentTarget.value
    if (next === 'other') {
      setAffiliationOverride(null)
      if (edited?.bangDreamId !== null) selectAffiliation(null)
      return
    }
    setAffiliationOverride('bang-dream')
  }

  function handleNewRole() {
    setSearch('')
    selectId(null)
  }

  function handleSearchChange(event) {
    setSearch(event.target.value)
  }

  function handleOpenPromptFile() {
    promptFile.current?.click()
  }

  function selectRole(event) {
    selectId(event.currentTarget.value)
  }

  function selectForm(event) {
    setFormId(event.currentTarget.value)
  }

  function updateName(event) {
    const name = event.target.value
    change((role) => {
      role.name = name
    })
  }

  function updateDescription(event) {
    const text = event.target.value
    changeForm((item) => {
      item.description = text
    })
  }

  function pickAvatar() {
    choose('avatar', ['png', 'jpg', 'jpeg', 'webp', 'gif'], (avatar) =>
      changeForm((item) => {
        item.avatar = avatar
      })
    )
  }

  function setDefaultForm() {
    change((role) => {
      role.defaultFormId = formId
    })
  }

  function updateTheme(event) {
    const color = event.currentTarget.value
    changeForm((item) => {
      item.themeColor = color
    })
  }

  function resetTheme() {
    changeForm((item) => {
      item.themeColor = null
    })
  }

  function removeAvatar() {
    changeForm((item) => {
      item.avatar = null
    })
  }

  function openWardrobe() {
    onOutfits(id, formId, !form.models.length && edited.bangDreamId ? 'download' : 'installed')
  }

  function openPackage() {
    onPackage(packageOption, formId)
  }

  function handleKeepChange(checked) {
    setKeep(checked === true)
  }

  function handleCancelDelete() {
    setImpact(null)
  }

  function handleStartChat() {
    onChat(id)
  }

  let packageLabel = null
  if (packageOption)
    packageLabel = packageOption.updateAvailable ? '查看语音包更新' : '使用推荐语音包'

  const affiliationMode = edited?.bangDreamId ? 'bang-dream' : (affiliationOverride ?? 'other')
  const roleDisplayName = edited?.name?.trim() || base?.name || edited?.displayName || '该角色'
  const linkedEntry = directory.find((entry) => entry.id === edited?.bangDreamId)

  let profileSummary = ''
  if (!isNew && edited) {
    if (linkedEntry) {
      profileSummary = linkedEntry.band
        ? linkedEntry.displayName + ' · ' + linkedEntry.band
        : linkedEntry.displayName
    } else if (edited.bangDreamId) {
      profileSummary = edited.bangDreamId
    } else {
      profileSummary = '其他角色 · 可导入本地 Live2D 模型'
    }
  }

  let footerStatus = message
  if (busy) footerStatus = '正在处理…'
  else if (!footerStatus && dirty) footerStatus = '有未保存的更改，保存或放弃后可切换角色'

  return (
    <div className="role-manager" data-dialog-busy={locked || undefined}>
      <header>
        <h2 data-dialog-focus tabIndex={-1}>
          角色管理
        </h2>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={locked}
          onClick={onClose}
          aria-label="关闭角色管理"
        >
          <X />
        </Button>
      </header>
      <div className="role-columns">
        <aside>
          <Button
            type="button"
            variant="outline"
            className="role-add-button"
            disabled={locked}
            onClick={handleNewRole}
          >
            <Plus />
            新增角色
          </Button>
          <label className="role-search">
            <Search size={14} aria-hidden="true" />
            <Input
              aria-label="搜索角色"
              placeholder="搜索角色…"
              value={search}
              onChange={handleSearchChange}
            />
          </label>
          <nav aria-label="管理的角色">
            {matches.map((item) => {
              const itemThemeStyle = characterSidebarGroupVars(item.themeColor)
              return (
                <button
                  type="button"
                  key={item.id}
                  value={item.id}
                  style={itemThemeStyle}
                  data-has-role-theme={itemThemeStyle ? 'true' : undefined}
                  onClick={selectRole}
                  disabled={locked}
                  aria-current={id === item.id || undefined}
                >
                  <CharacterAvatar role={item} client={client} size={30} />
                  <span>{item.name || item.displayName}</span>
                </button>
              )
            })}
            {!matches.length && <small>{roles.length ? '没有匹配的角色' : '还没有角色'}</small>}
          </nav>
        </aside>
        <div className="role-detail">
          <div className="role-scroll" key={id}>
            {edited && form && (
              <fieldset disabled={busy} className="role-content">
                <div
                  className="role-profile"
                  style={formThemeStyle}
                  data-has-role-theme={formThemeStyle ? 'true' : undefined}
                >
                  <CharacterAvatar
                    role={{ ...edited, avatar: form.avatar }}
                    client={client}
                    size={56}
                  />
                  <div className="role-profile-meta">
                    <h3>{isNew ? '新增角色' : base.name}</h3>
                    {profileSummary && <p className="role-profile-summary">{profileSummary}</p>}
                    {!isNew && (
                      <div className="role-profile-actions">
                        <Button type="button" size="xs" variant="outline" onClick={pickAvatar}>
                          更换头像
                        </Button>
                        {form.avatar && (
                          <Button type="button" size="xs" variant="ghost" onClick={removeAvatar}>
                            恢复默认头像
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <section className="role-card">
                  <div className="role-field">
                    <label className="role-field-label" htmlFor={nameInputId}>
                      角色名称
                    </label>
                    <Input id={nameInputId} value={edited.name} onChange={updateName} />
                  </div>

                  {!isNew && edited.forms.length > 1 && (
                    <div className="role-field">
                      <span className="role-field-label">形态</span>
                      <div className="role-forms-row" style={formThemeStyle}>
                        <div className="role-forms" role="group" aria-label="角色形态">
                          {edited.forms.map((item) => (
                            <Button
                              type="button"
                              size="sm"
                              key={item.id}
                              variant="ghost"
                              aria-pressed={item.id === formId}
                              value={item.id}
                              onClick={selectForm}
                            >
                              {characterFormName(edited, item)}
                            </Button>
                          ))}
                        </div>
                        {formId === edited.defaultFormId ? (
                          <span className="role-default-badge">新对话默认</span>
                        ) : (
                          <Button
                            type="button"
                            size="xs"
                            variant="outline"
                            onClick={setDefaultForm}
                          >
                            设为新对话默认
                          </Button>
                        )}
                      </div>
                    </div>
                  )}

                  {!isNew && (
                    <div className="role-field">
                      <label className="role-field-label" htmlFor={themeInputId}>
                        形态主题色
                      </label>
                      <div className="role-theme-row">
                        <div className="role-color-picker">
                          <input
                            id={themeInputId}
                            type="color"
                            value={form.themeColor ?? '#776d75'}
                            onChange={updateTheme}
                          />
                          <span className="role-color-code">
                            {form.themeColor ? form.themeColor.toUpperCase() : '默认配色'}
                          </span>
                        </div>
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          disabled={!form.themeColor}
                          onClick={resetTheme}
                        >
                          恢复默认颜色
                        </Button>
                      </div>
                      {/* <div
                        className="role-theme-preview"
                        style={formThemeStyle}
                        aria-label="配色预览"
                      >
                        <div className="role-theme-preview-stage">
                          <span className="role-theme-preview-speaker">
                            {edited.name || form.displayName || '角色名'}
                          </span>
                          <span className="role-theme-preview-line">舞台台词预览</span>
                        </div>
                        <div className="role-theme-preview-chat">
                          <span className="role-theme-preview-name">
                            {edited.name || form.displayName || '角色名'}
                          </span>
                          <span className="role-theme-preview-bubble">你好呀</span>
                          <span className="role-theme-preview-send" aria-hidden="true">
                            <ArrowUp size={13} />
                          </span>
                        </div>
                      </div> */}
                    </div>
                  )}

                  <div className="role-field">
                    <div className="role-field-header">
                      <label className="role-field-label" htmlFor={promptInputId}>
                        介绍提示词
                      </label>
                      <div className="role-actions">
                        <input
                          type="file"
                          hidden
                          ref={promptFile}
                          accept=".txt,text/plain"
                          onChange={importPrompt}
                        />
                        <Button
                          type="button"
                          size="xs"
                          variant="outline"
                          onClick={handleOpenPromptFile}
                        >
                          从 .txt 导入
                        </Button>
                        <Button
                          type="button"
                          size="xs"
                          variant="ghost"
                          disabled={!form.description}
                          onClick={exportPrompt}
                        >
                          导出 .txt
                        </Button>
                      </div>
                    </div>
                    <Textarea
                      id={promptInputId}
                      value={form.description}
                      onChange={updateDescription}
                      rows={5}
                    />
                  </div>
                </section>

                {!isNew && (
                  <>
                    <section className="role-card outfit-affiliation-character" ref={affiliation}>
                      <div className="role-card-header">
                        <h3>角色归属</h3>
                        <p className="role-section-desc">
                          {affiliationMode === 'bang-dream'
                            ? '关联后可匹配头像、下载 Live2D 服装。首次关联或更换角色会将所有形态设为该角色的预设色，之后可单独调色。'
                            : `「${roleDisplayName}」设为其他角色时，可通过导入本地模型添加服装。`}
                        </p>
                      </div>
                      <fieldset className="role-mode-picker">
                        <legend className="sr-only">角色归属类型</legend>
                        <div className="role-mode-choices">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            value="bang-dream"
                            aria-pressed={affiliationMode === 'bang-dream'}
                            onClick={chooseAffiliationMode}
                          >
                            BanG Dream! 角色
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            value="other"
                            aria-pressed={affiliationMode === 'other'}
                            onClick={chooseAffiliationMode}
                          >
                            其他角色
                          </Button>
                        </div>
                      </fieldset>
                      {affiliationMode === 'bang-dream' && (
                        <div className="role-field">
                          <span className="role-field-label">对应角色</span>
                          <SelectField
                            value={edited.bangDreamId ?? ''}
                            label="对应角色"
                            placeholder="选择 BanG Dream! 角色"
                            options={options}
                            onChange={selectAffiliation}
                          />
                        </div>
                      )}
                    </section>

                    <section className="role-card role-row">
                      <div className="role-card-header">
                        <h3>Live2D</h3>
                        <p className="role-section-desc">
                          {form.models.length
                            ? `当前形态已安装 ${form.models.length} 套服装。`
                            : '当前形态尚未添加 Live2D 服装。'}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={dirty}
                        onClick={openWardrobe}
                      >
                        <Shirt />
                        {form.models.length ? '管理服装' : '添加 Live2D 服装'}
                      </Button>
                    </section>

                    <ReplyVoice
                      key={formId}
                      client={client}
                      form={form}
                      changeForm={changeForm}
                      choose={choose}
                      packageLabel={packageLabel}
                      packageAction={dirty ? null : openPackage}
                    />

                    <section className="role-card role-danger-zone">
                      <div className="role-row">
                        <div className="role-card-header">
                          <h3>删除角色</h3>
                          <p className="role-section-desc">删除后无法撤销，现有对话将只能阅读。</p>
                        </div>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="role-danger"
                          disabled={dirty}
                          onClick={previewDeletion}
                        >
                          删除角色
                        </Button>
                      </div>
                      {impact && (
                        <div className="role-delete">
                          <p>
                            删除「{base.name}」后，
                            {
                              impact.chats.filter((chat) => chat.mode === 'single-character').length
                            }{' '}
                            个单人对话和{' '}
                            {
                              impact.chats.filter((chat) => chat.mode === 'scripted-dialogue')
                                .length
                            }{' '}
                            个小剧场将仅供阅读。
                          </p>
                          {impact.chats.length > 0 && (
                            <details>
                              <summary>查看受影响的对话</summary>
                              <ul>
                                {impact.chats.map((chat) => (
                                  <li key={chat.id}>{chat.title}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                          {impact.personas.length > 0 && (
                            <div className="role-check">
                              <Checkbox
                                id={keepPersonasId}
                                checked={keep}
                                onCheckedChange={handleKeepChange}
                              />
                              <label htmlFor={keepPersonasId}>
                                保留为自定义身份（
                                {impact.personas.map((persona) => persona.name).join('、')}）
                              </label>
                            </div>
                          )}
                          {impact.busy && (
                            <p>请先结束该角色的生成、演出或安装任务，再重新查看删除影响。</p>
                          )}
                          <div className="role-actions">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={handleCancelDelete}
                            >
                              取消删除
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="destructive"
                              disabled={impact.busy}
                              onClick={remove}
                            >
                              确认删除角色
                            </Button>
                          </div>
                        </div>
                      )}
                    </section>
                  </>
                )}
              </fieldset>
            )}
          </div>
          {problem && (
            <div className="role-error" role="alert">
              {problem}
              <Button type="button" size="xs" variant="ghost" onClick={discard}>
                重新读取
              </Button>
            </div>
          )}
          {edited && (
            <footer>
              <small role="status" data-dirty={(!busy && dirty) || undefined}>
                {footerStatus}
              </small>
              {dirty && (
                <Button type="button" variant="ghost" disabled={busy} onClick={discard}>
                  放弃更改
                </Button>
              )}
              {!isNew && !dirty && (
                <Button type="button" variant="outline" disabled={busy} onClick={handleStartChat}>
                  开始对话
                </Button>
              )}
              <Button
                type="button"
                disabled={
                  !dirty || busy || !edited.name.trim() || (isNew && !form?.description.trim())
                }
                onClick={save}
              >
                {isNew ? '创建角色' : '保存更改'}
              </Button>
            </footer>
          )}
        </div>
      </div>
    </div>
  )
}
