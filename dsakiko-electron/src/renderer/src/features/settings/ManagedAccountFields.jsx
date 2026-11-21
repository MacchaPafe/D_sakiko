import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Input, Textarea } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { desktopHost } from '../../host/desktop.js'

function unwrap(reply) {
  if (reply.problem) throw new Error(reply.problem.message)
  return reply.result
}

/** @param {object} props 服务账号草稿和修改入口。 @returns {import('react').ReactElement} JSON 选择器与可选手动输入。 */
export function VertexAccountFields({
  vertex,
  onChange,
  secret,
  onSecret,
  onImport,
  onOpenGuide,
  hasKey,
  onError
}) {
  async function chooseFile() {
    try {
      const imported = unwrap(await desktopHost().selectVertexAccount())
      if (imported) onImport(imported)
    } catch (error) {
      onError(error.message)
    }
  }
  function field(name, value) {
    onChange({ ...vertex, [name]: value })
  }
  return (
    <>
      <div className="settings-row-actions">
        <Button type="button" onClick={chooseFile}>
          选择服务账号 JSON
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onOpenGuide}>
          如何获取服务账号 JSON <ArrowUpRight />
        </Button>
      </div>
      {vertex.email && (
        <p>
          {vertex.email} · {vertex.project}
        </p>
      )}
      <label>
        区域
        <Input
          value={vertex.location}
          onChange={(event) => field('location', event.target.value)}
        />
      </label>
      <details className="settings-manual-details">
        <summary>手动编辑服务账号</summary>
        <label>
          项目 ID
          <Input
            value={vertex.project}
            onChange={(event) => field('project', event.target.value)}
          />
        </label>
        <label>
          服务账号邮箱
          <Input value={vertex.email} onChange={(event) => field('email', event.target.value)} />
        </label>
        <label>
          服务账号私钥
          <Textarea
            rows={4}
            value={secret}
            autoComplete="off"
            spellCheck={false}
            placeholder={hasKey ? '已配置，留空保留私钥' : '粘贴 PEM 私钥'}
            onChange={(event) => onSecret(event.target.value)}
          />
        </label>
      </details>
    </>
  )
}

/** @param {object} props 本次编辑绑定的注册及选择回调。 @returns {import('react').ReactElement} 订阅注册、登录和模型目录操作。 */
export function ChatGPTAccountFields({ boundId, account, onAccount }) {
  const [profiles, setProfiles] = useState([])
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const alive = useRef(false)
  const loggingIn = useRef(false)
  const callback = useRef(onAccount)
  useEffect(() => {
    callback.current = onAccount
  }, [onAccount])

  async function command(action, id) {
    return unwrap(await desktopHost().chatGPTAccount(action, id))
  }
  function display(profile) {
    if (!alive.current) return
    callback.current(profile)
    if (profile) setProfiles((items) => [...items.filter((p) => p.id !== profile.id), profile])
  }
  async function loadModels(profile) {
    const models = await command('models', profile.id)
    display({ ...profile, models })
    if (alive.current && !models.length) setMessage('此账号目前没有可选模型。')
  }
  useEffect(() => {
    let active = true
    alive.current = true
    function initialProfile(items) {
      if (boundId) return items.find((p) => p.id === boundId) ?? null
      return items.find((p) => p.fromLegacy) ?? null
    }
    async function load() {
      setBusy('list')
      try {
        const items = await command('list')
        if (!active) return
        setProfiles(items)
        const profile = initialProfile(items)
        callback.current(profile)
        if (profile?.connected) {
          const models = await command('models', profile.id)
          if (active) display({ ...profile, models })
        }
      } catch (error) {
        if (active) setMessage(error.message)
        const profiles = await command('list').catch(() => null)
        if (active && profiles) {
          setProfiles(profiles)
          callback.current(initialProfile(profiles))
        }
      } finally {
        if (active) setBusy('')
      }
    }
    void load()
    return () => {
      active = false
      alive.current = false
      if (loggingIn.current) void desktopHost().chatGPTAccount('cancel')
    }
  }, [boundId])
  async function run(action, profile = account) {
    if (busy) return
    setBusy(action)
    setMessage('')
    try {
      if (action === 'discover') {
        const matches = await command('discover')
        if (!alive.current) return
        if (!matches.length) {
          setMessage('没有发现之前已登录的账号。')
          return
        }
        setProfiles((current) => {
          const byId = new Map(current.map((item) => [item.id, item]))
          for (const item of matches) byId.set(item.id, item)
          return [...byId.values()]
        })
        if (!profile) {
          profile = matches[0]
          callback.current(profile)
          setMessage('已选中之前的账号。')
          if (profile.connected) await loadModels(profile)
        } else {
          setMessage('已找到之前的账号。')
        }
      } else if (action === 'logout') {
        const result = await command('logout', profile.id)
        display({ ...profile, signedIn: false, connected: false })
        if (alive.current) setMessage(result.warning || '已退出登录。')
      } else {
        if (action === 'login') {
          loggingIn.current = true
          profile = await command('login', profile?.id)
          if (!alive.current) return
          display(profile)
        }
        if (profile?.connected) await loadModels(profile)
        else if (alive.current) setMessage('请登录并授权使用 ChatGPT 订阅额度。')
      }
    } catch (error) {
      if (alive.current) setMessage(error.message)
      const items = await command('list').catch(() => null)
      if (alive.current && items) {
        setProfiles(items)
        let selected = items.find((p) => p.id === profile?.id)
        // 首次授权若在兑换阶段失败，服务端已签发的注册仍可用于下一次登录。
        if (!selected && action === 'login' && !profile)
          selected = items.find((item) => !profiles.some((previous) => previous.id === item.id))
        callback.current(selected ?? null)
      }
    } finally {
      loggingIn.current = false
      if (alive.current) setBusy('')
    }
  }
  function select(id) {
    const profile = profiles.find((p) => p.id === id) ?? null
    display(profile)
    setMessage('')
    if (profile?.connected) void run('models', profile)
  }
  const actionsDisabled = Boolean(busy)
  return (
    <>
      {boundId ? (
        <p>{account?.label ?? 'ChatGPT 账号'}</p>
      ) : (
        <SelectField
          label="ChatGPT 账号"
          value={account?.id ?? 'new'}
          onChange={select}
          disabled={actionsDisabled}
          options={[
            { value: 'new', label: '添加新账号' },
            ...profiles.map((p) => ({ value: p.id, label: p.label }))
          ]}
        />
      )}
      <div className="settings-row-actions">
        {account?.connected ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={actionsDisabled}
            onClick={() => run('models')}
          >
            刷新模型列表
          </Button>
        ) : (
          <Button type="button" disabled={actionsDisabled} onClick={() => run('login')}>
            {busy === 'login' ? '等待浏览器授权…' : '使用 ChatGPT 登录'}
          </Button>
        )}
        {(account?.connected || account?.signedIn) && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={actionsDisabled}
            onClick={() => run('logout')}
          >
            退出登录
          </Button>
        )}
        {busy === 'login' && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => desktopHost().chatGPTAccount('cancel')}
          >
            取消登录
          </Button>
        )}
        {!boundId && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={actionsDisabled}
            onClick={() => run('discover')}
          >
            查找之前的账号
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => desktopHost().openProviderKeyPage('chatgpt')}
        >
          管理订阅用量与授权
        </Button>
      </div>
      {message && <p role="status">{message}</p>}
    </>
  )
}
