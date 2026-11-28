import { useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Plus, Search, UserRound } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Input, Textarea } from '../../components/ui/fields.jsx'
import { Choice } from '../../components/ui/Choice.jsx'
import { desktopHost } from '../../host/desktop.js'
import { CharacterAvatar } from '../home/CharacterAvatar.jsx'
import { characterPreviewVars } from '../../styles/character-theme.js'
import { catalogTextSaveDelay, useCatalogEditor } from '../../hooks/use-catalog-editor.js'
import { PersonaPreview } from './PersonaPicker.jsx'
import { newPersona, personaOptions, selectPersonaCharacter } from './source.js'

function customSource() {
  return { kind: 'custom', displayName: '', description: '', avatar: null }
}

function keepNameInput(saved, submitted) {
  const source = submitted.source
  if (source.kind === 'custom' && source.displayName.trim() === saved.source.displayName)
    return { ...saved, source: { ...saved.source, displayName: source.displayName } }
  return saved
}

function SourceChoice({ label, value, options, onChange }) {
  // 来源丢失时，即使只剩一个候选项，也需要让用户明确选择后再保存。
  const only = options.length === 1 && options[0]
  if (only && only.value !== value)
    return (
      <Button variant="outline" onClick={() => onChange(only.value)}>
        {only.label}
      </Button>
    )
  return <Choice label={label} value={value} options={options} onChange={onChange} />
}

/** @param {object} props 身份摘要、角色目录、保存回调和用于打开定义的初始编号或草稿。 @returns {import('react').ReactElement} 共用角色管理浮窗的身份页签。 */
export function PersonaManager({
  ref,
  client,
  roles,
  personas,
  initial,
  onLock,
  onRefresh,
  catalogProblem
}) {
  const [search, setSearch] = useState('')
  const [pending, setPending] = useState(false)
  const [composing, setComposing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [problem, setProblem] = useState('')
  const [message, setMessage] = useState('')
  const [loaded, setLoaded] = useState(null)
  const [reload, setReload] = useState(0)
  const [initialRequest, setInitialRequest] = useState(typeof initial === 'string' ? initial : null)
  const assetDraft = useRef(null)
  const working = useRef(false)
  const editor = useCatalogEditor({
    initial: typeof initial === 'string' ? null : initial,
    paused: pending || composing,
    persist: persistPersona,
    accept: keepNameInput
  })
  const { base, edited, dirty, isNew, saving, select: setDefinition } = editor
  const source = edited?.source
  const roleId = source?.kind === 'character' ? source.characterId : null
  const role = roles.find((item) => item.id === roleId)
  const details = role && loaded?.role === role ? loaded : null
  const form = details?.value?.forms.find((item) => item.id === source.formId)
  const preview = source?.kind === 'custom' ? source : form
  const valid = preview?.displayName?.trim()
  const options = personaOptions(personas).filter((option) =>
    option.label.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())
  )

  useEffect(() => {
    onLock(pending)
    return () => onLock(false)
  }, [onLock, pending])
  useImperativeHandle(ref, () => ({ flush: editor.flush }))
  useEffect(() => {
    if (!initialRequest) return
    let live = true
    client.characters.getPersona(initialRequest).then(
      (value) => {
        if (!live) return
        setInitialRequest(null)
        if (value) setDefinition(value)
      },
      (error) => {
        if (!live) return
        setInitialRequest(null)
        setProblem(error.message)
      }
    )
    return () => {
      live = false
    }
  }, [client, initialRequest, setDefinition])
  useEffect(() => {
    let live = true
    if (role)
      client.characters.get(role.id).then(
        (value) => {
          if (live) setLoaded({ role, value })
        },
        (error) => {
          if (live) setLoaded({ role, error: error.message })
        }
      )
    return () => {
      live = false
    }
  }, [client, role, reload])
  useEffect(
    () => () => {
      if (assetDraft.current) void client.assets.discardDraft(assetDraft.current).catch(() => {})
    },
    [client]
  )
  async function releaseAvatar() {
    const previous = assetDraft.current
    assetDraft.current = null
    if (previous) await client.assets.discardDraft(previous)
  }
  async function perform(action) {
    if (working.current) return
    working.current = true
    setPending(true)
    setProblem('')
    setMessage('')
    try {
      await editor.waitForSave()
      await action()
    } catch (error) {
      setProblem(error.message)
    } finally {
      working.current = false
      setPending(false)
    }
  }
  function select(value) {
    editor.select(value)
    setDeleting(false)
    setComposing(false)
  }

  function navigate(action) {
    return perform(async () => {
      if (!(await editor.flush())) return
      if (isNew) await releaseAvatar()
      await action()
    })
  }

  function open(event) {
    const id = event.currentTarget.value
    if (id === base?.id) return
    setInitialRequest(null)
    return navigate(async () => {
      const value = await client.characters.getPersona(id)
      if (!value) throw new Error('该身份已删除，请刷新目录。')
      select(value)
    })
  }
  function create() {
    setInitialRequest(null)
    return navigate(() => {
      select(null)
      editor.change(() => newPersona(customSource()))
      setSearch('')
    })
  }
  function changeSource(next, delay) {
    editor.change((value) => ({ ...value, source: next }), delay)
    setDeleting(false)
    setMessage('')
  }
  function changeKind(kind) {
    if (source.kind === kind) return
    // 切换现有自定义身份时，先等用户指定关联对象，再提交新的来源。
    changeSource(
      kind === 'custom'
        ? customSource()
        : selectPersonaCharacter(source, isNew ? roles[0] : undefined)
    )
  }
  function chooseRole(id) {
    changeSource(
      selectPersonaCharacter(
        source,
        roles.find((item) => item.id === id)
      )
    )
  }
  function chooseForm(formId) {
    changeSource({ ...source, formId })
  }
  function editField(event) {
    const { name, value } = event.currentTarget
    editor.change(
      (current) => ({ ...current, source: { ...current.source, [name]: value } }),
      catalogTextSaveDelay
    )
    setDeleting(false)
    setMessage('')
  }
  function importAvatar() {
    return perform(async () => {
      if (!assetDraft.current) assetDraft.current = (await client.assets.createDraft()).draftId
      const [item] = await desktopHost().importFiles({
        purpose: 'avatar',
        draftId: assetDraft.current,
        single: true,
        extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif']
      })
      if (!item) return
      if (item.libraryItemId) await client.assets.removeLibraryItem(item.libraryItemId)
      changeSource({ ...source, avatar: item.asset })
    })
  }
  function copyForm() {
    return navigate(async () => {
      if (form.avatar) assetDraft.current = (await client.assets.createDraft([form.avatar])).draftId
      select(null)
      editor.change(() =>
        newPersona({
          kind: 'custom',
          displayName: form.displayName,
          description: form.description,
          avatar: form.avatar
        })
      )
    })
  }
  async function persistPersona(_previous, submitted) {
    const source = submitted.source
    let next = submitted
    if (source.kind === 'custom') {
      const displayName = source.displayName.trim()
      if (!displayName) throw new Error('请填写身份名称。')
      next = { ...submitted, source: { ...source, displayName } }
    } else if (!source.characterId || !source.formId) {
      throw new Error('请选择身份来源角色与人格。')
    }
    const saved = await client.characters.savePersona(next)
    try {
      await releaseAvatar()
    } catch (error) {
      setProblem(error.message)
    }
    await onRefresh()
    setMessage('身份已保存')
    return saved
  }
  function createPersona() {
    return perform(editor.commit)
  }
  function retrySave() {
    return perform(editor.flush)
  }
  function discard() {
    return perform(async () => {
      await releaseAvatar()
      select(base ? await client.characters.getPersona(base.id) : null)
      await onRefresh()
    })
  }
  function remove() {
    return perform(async () => {
      await client.characters.deletePersona(base.id, base.revision)
      select(null)
      await onRefresh()
      setMessage('身份已删除')
    })
  }
  function refresh() {
    setReload((value) => value + 1)
    void onRefresh()
  }
  const sourceProblem = roleId && (!role || (details && !form))
  const personaById = new Map(personas.map((item) => [item.id, item]))
  const profileThemeStyle =
    source?.kind === 'character'
      ? characterPreviewVars(form?.themeColor ?? role?.themeColor)
      : undefined
  const profileName = preview?.displayName?.trim() || '未命名身份'
  let sourceSummary = ''
  if (source?.kind === 'custom') {
    sourceSummary = '自定义身份 · 独立头像与设定'
  } else if (source?.kind === 'character') {
    const roleLabel = role ? role.name || role.displayName : '来源待关联'
    const formLabel = form ? form.displayName || '默认人格' : ''
    sourceSummary = formLabel
      ? `扮演已有角色 · ${roleLabel} / ${formLabel}`
      : `扮演已有角色 · ${roleLabel}`
  }

  let footerStatus = message
  if (saving) footerStatus = '正在保存…'
  else if (editor.error) footerStatus = '更改未保存'
  else if (!isNew && dirty) footerStatus = '等待自动保存'

  return (
    <div className="role-columns persona-columns" data-dialog-busy={pending || undefined}>
      <aside>
        <Button variant="outline" className="role-add-button" disabled={pending} onClick={create}>
          <Plus />
          添加身份
        </Button>
        <label className="role-search">
          <Search size={14} />
          <Input
            aria-label="搜索身份"
            placeholder="搜索身份…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <nav aria-label="管理的对话身份">
          {options.map((option) => {
            const entry = personaById.get(option.value)
            const hasProblem = Boolean(entry?.problem)
            return (
              <button
                type="button"
                key={option.value}
                value={option.value}
                disabled={pending}
                aria-label={option.label}
                aria-current={base?.id === option.value || undefined}
                data-problem={hasProblem || undefined}
                onClick={open}
              >
                {entry?.avatar ? (
                  <CharacterAvatar role={entry} client={client} size={30} />
                ) : (
                  <span className="persona-list-avatar-fallback" aria-hidden="true">
                    <UserRound size={15} />
                  </span>
                )}
                <span>{option.label}</span>
              </button>
            )
          })}
          {!options.length && (
            <small>{personas.length ? '没有匹配的身份' : '还没有对话身份'}</small>
          )}
        </nav>
      </aside>
      <div className="role-detail">
        <div className="role-scroll">
          {initialRequest && (
            <div className="role-content" role="status">
              正在读取身份…
            </div>
          )}
          {!initialRequest && !edited && (
            <div className="persona-empty-state">
              <span
                className="persona-avatar-fallback persona-avatar-fallback-lg"
                aria-hidden="true"
              >
                <UserRound size={24} />
              </span>
              <div className="persona-empty-copy">
                <h3>{personas.length ? '选择或添加对话身份' : '还没有对话身份'}</h3>
                <p>
                  自定义昵称与背景设定，在对话中以特定身份与角色交流。
                </p>
              </div>
              {!personas.length && (
                <Button variant="outline" disabled={pending} onClick={create}>
                  <Plus size={15} aria-hidden="true" />
                  创建第一个身份
                </Button>
              )}
            </div>
          )}
          {edited && (
            <fieldset
              key={edited.id}
              className="role-content persona-content-enter"
              disabled={pending}
              onCompositionStart={() => setComposing(true)}
              onCompositionEnd={() => setComposing(false)}
            >
              <div
                className="role-profile persona-profile"
                style={profileThemeStyle}
                data-has-role-theme={profileThemeStyle ? 'true' : undefined}
              >
                {preview?.avatar ? (
                  <CharacterAvatar
                    role={{ avatar: preview.avatar, displayName: profileName }}
                    client={client}
                    size={56}
                  />
                ) : (
                  <span
                    className="persona-avatar-fallback persona-avatar-fallback-lg"
                    aria-hidden="true"
                  >
                    <UserRound size={24} />
                  </span>
                )}
                <div className="role-profile-meta">
                  <h3>{base ? profileName : '新增身份'}</h3>
                  {sourceSummary && <p className="role-profile-summary">{sourceSummary}</p>}
                </div>
              </div>

              <section className="role-card">
                <div className="role-card-header">
                  <h3>身份来源</h3>
                  <p className="role-section-desc">
                    {source.kind === 'custom'
                      ? '填写独立的名称、设定与头像，适合以原创或专属身份与角色交流。'
                      : '直接引用已有角色及人格的名称、设定与头像，也可随时复制为独立身份。'}
                  </p>
                </div>
                <Choice
                  label="身份来源"
                  value={source.kind}
                  options={[
                    { value: 'custom', label: '自定义身份' },
                    { value: 'character', label: '扮演已有角色' }
                  ]}
                  onChange={changeKind}
                />
              </section>

              <section key={source.kind} className="role-card persona-section-enter">
                <div className="role-card-header">
                  <h3>{source.kind === 'custom' ? '基本资料' : '关联角色与人格'}</h3>
                </div>
                {source.kind === 'custom' ? (
                  <>
                    <div className="role-persona-toolbar">
                      <div className="role-persona-avatar">
                        {source.avatar ? (
                          <CharacterAvatar
                            role={{ avatar: source.avatar, displayName: profileName }}
                            client={client}
                            size={40}
                          />
                        ) : (
                          <span className="persona-avatar-fallback" aria-hidden="true">
                            <UserRound size={18} />
                          </span>
                        )}
                        <div className="role-actions">
                          <Button type="button" size="xs" variant="outline" onClick={importAvatar}>
                            {source.avatar ? '更换头像' : '选择头像'}
                          </Button>
                          {source.avatar && (
                            <Button
                              type="button"
                              size="xs"
                              variant="ghost"
                              onClick={() => changeSource({ ...source, avatar: null })}
                            >
                              移除头像
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                    <label className="role-field">
                      <span className="role-field-label">身份名称</span>
                      <Input
                        name="displayName"
                        value={source.displayName}
                        placeholder="输入对话中的称呼…"
                        onChange={editField}
                      />
                    </label>
                    <label className="role-field">
                      <span className="role-field-label">身份描述</span>
                      <Textarea
                        name="description"
                        rows={5}
                        value={source.description}
                        placeholder="描述你的身份、关系或说话风格（可选）…"
                        onChange={editField}
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <div className="role-field">
                      <span className="role-field-label">角色</span>
                      <SourceChoice
                        label="身份来源角色"
                        value={roleId}
                        options={roles.map((item) => ({
                          value: item.id,
                          label: item.name || item.displayName
                        }))}
                        onChange={chooseRole}
                      />
                    </div>
                    {role && (
                      <div className="role-field">
                        <span className="role-field-label">人格</span>
                        <SourceChoice
                          label="身份来源人格"
                          value={source.formId}
                          options={role.forms.map((item) => ({
                            value: item.id,
                            label: item.displayName
                          }))}
                          onChange={chooseForm}
                        />
                      </div>
                    )}
                    {!roles.length && (
                      <p className="role-section-desc">请先在「角色」页签创建角色。</p>
                    )}
                    {sourceProblem && (
                      <p className="persona-inline-alert" role="alert">
                        {details?.error || '来源角色或人格不可用，请重新选择。'}{' '}
                        <Button variant="ghost" size="sm" onClick={refresh}>
                          重新读取
                        </Button>
                      </p>
                    )}
                    {form && !dirty && (
                      <div className="role-actions">
                        <Button variant="outline" size="sm" onClick={copyForm}>
                          复制为自定义身份
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </section>

              {preview && (
                <section
                  className="persona-preview-section"
                  style={profileThemeStyle}
                  data-has-role-theme={profileThemeStyle ? 'true' : undefined}
                >
                  <div className="role-card-header">
                    <h3>名片预览</h3>
                    <p className="role-section-desc">新建对话时将固定记录此身份内容。</p>
                  </div>
                  <PersonaPreview
                    persona={preview}
                    client={client}
                    subtitle={source.kind === 'custom' ? '自定义身份' : '扮演角色'}
                  />
                </section>
              )}
            </fieldset>
          )}
        </div>
        {(editor.error || catalogProblem || problem) && (
          <p className="role-error" role="alert">
            {editor.error || problem || catalogProblem}{' '}
            {editor.error && !isNew ? (
              <Button variant="ghost" disabled={pending} onClick={retrySave}>
                重试保存
              </Button>
            ) : (
              !editor.error && (
                <Button variant="ghost" onClick={refresh}>
                  刷新目录
                </Button>
              )
            )}
          </p>
        )}
        {deleting && (
          <div className="persona-delete-wrap">
            <div className="role-delete persona-delete-enter">
              <p>删除这个身份？已有聊天仍然会保留身份记录。</p>
              <div className="role-actions">
                <Button size="sm" variant="destructive" disabled={pending} onClick={remove}>
                  确认删除身份
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={pending}
                  onClick={() => setDeleting(false)}
                >
                  取消
                </Button>
              </div>
            </div>
          </div>
        )}
        <footer>
          <small
            key={footerStatus}
            className={footerStatus === message && message ? 'persona-status-message' : undefined}
            role="status"
            data-dirty={Boolean(editor.error) || undefined}
          >
            {footerStatus}
          </small>
          {base && !dirty && !deleting && (
            <Button variant="ghost" disabled={pending} onClick={() => setDeleting(true)}>
              删除身份
            </Button>
          )}
          {editor.error && !isNew && (
            <Button variant="ghost" disabled={pending} onClick={discard}>
              放弃更改
            </Button>
          )}
          {edited && isNew && (
            <Button disabled={!valid || pending} onClick={createPersona}>
              创建身份
            </Button>
          )}
        </footer>
      </div>
    </div>
  )
}
