import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AvatarPickerSheet } from './AvatarPickerSheet'
import { AvatarMenu } from './AvatarMenu'

afterEach(cleanup)
const character = { id: 'anon', name: '爱音' }
const options = [
  { id: 'builtin.png', name: '默认头像', image_url: '/default?v=1' },
  { id: 'downloaded.png', name: '下载头像', image_url: '/downloaded?v=1' },
]
const catalog = { avatar: { character_id: 'anon', selected_id: 'builtin.png', options } }

describe('avatar selection', () => {
  it('opens a one-item menu and supports dismissal without changing the avatar', () => {
    const onChange = vi.fn()
    render(<AvatarMenu character={character} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '爱音的头像' }))
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '爱音的头像' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '更改头像' }))
    expect(onChange).toHaveBeenCalledOnce()
  })

  it('highlights a draft selection and only persists when confirmed', async () => {
    const onSave = vi.fn().mockResolvedValue({})
    const onClose = vi.fn()
    render(<AvatarPickerSheet character={character} onLoad={vi.fn().mockResolvedValue(catalog)} onSave={onSave} onClose={onClose} />)
    const downloaded = await screen.findByRole('button', { name: '下载头像' })
    expect(screen.getByRole('button', { name: '默认头像' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(downloaded)
    expect(downloaded.getAttribute('aria-pressed')).toBe('true')
    expect(downloaded.className).toContain('is-selected')
    expect(onSave).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确定' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
    expect(onSave).toHaveBeenCalledWith({ avatar: { character_id: 'anon', avatar_id: 'downloaded.png' } })
  })

  it('reloads on every opening and does not save a cancelled draft', async () => {
    const onLoad = vi.fn().mockResolvedValueOnce(catalog).mockResolvedValueOnce({ avatar: { selected_id: null, options: [] } })
    const onSave = vi.fn()
    const props = { character, onLoad, onSave, onClose: vi.fn() }
    const first = render(<AvatarPickerSheet {...props} />)
    fireEvent.click(await screen.findByRole('button', { name: '下载头像' }))
    fireEvent.click(screen.getByRole('button', { name: '关闭头像选择' }))
    expect(onSave).not.toHaveBeenCalled()
    first.unmount()
    render(<AvatarPickerSheet {...props} />)
    await screen.findByText('暂无可用头像，请先通过下载器下载。')
    expect(screen.queryByRole('button', { name: '下载头像' })).toBeNull()
    expect(screen.getByRole('button', { name: '确定' }).disabled).toBe(true)
    expect(onLoad).toHaveBeenNthCalledWith(2, 'anon')
  })

  it('keeps the sheet open on a stale selection and allows a fresh scan', async () => {
    const onClose = vi.fn()
    const onLoad = vi.fn().mockResolvedValueOnce(catalog).mockResolvedValueOnce({ avatar: { selected_id: null, options: [] } })
    render(<AvatarPickerSheet character={character} onLoad={onLoad} onClose={onClose}
      onSave={vi.fn().mockRejectedValue(new Error('头像已不存在'))} />)
    fireEvent.click(await screen.findByRole('button', { name: '下载头像' }))
    fireEvent.click(screen.getByRole('button', { name: '确定' }))
    await screen.findByText('头像已不存在')
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await screen.findByText('暂无可用头像，请先通过下载器下载。')
    expect(onLoad).toHaveBeenCalledTimes(2)
  })
})
