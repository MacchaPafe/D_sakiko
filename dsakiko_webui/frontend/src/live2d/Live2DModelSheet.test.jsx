import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Live2DModelSheet } from './Live2DModelSheet'

const OPTIONS = {
  supported: true,
  character_name: '测试角色',
  message: null,
  options: [
    { option_id: 'default-id', name: '默认', available: true, is_current: true },
    { option_id: 'dress-id', name: '礼服2', available: true, is_current: false },
  ],
}

describe('Live2DModelSheet', () => {
  afterEach(cleanup)

  it('loads names only and closes after an accepted selection', async () => {
    const onLoad = vi.fn().mockResolvedValue(OPTIONS)
    const onSelect = vi.fn().mockResolvedValue({ accepted: true })
    const onClose = vi.fn()
    render(
      <Live2DModelSheet
        open
        presentationTargetId="target-1"
        runtimeState={{ status: 'ready' }}
        onClose={onClose}
        onLoad={onLoad}
        onSelect={onSelect}
      />,
    )

    expect(await screen.findByText('礼服2')).toBeTruthy()
    expect(screen.getByRole('button', { name: /默认/ }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /礼服2/ }))

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('dress-id'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('marks a persisted current option when browser rendering failed', async () => {
    render(
      <Live2DModelSheet
        open
        presentationTargetId="target-1"
        runtimeState={{ status: 'error' }}
        onClose={vi.fn()}
        onLoad={vi.fn().mockResolvedValue(OPTIONS)}
        onSelect={vi.fn()}
      />,
    )

    expect(await screen.findByText('加载失败')).toBeTruthy()
  })

  it('refreshes once and keeps the sheet open when the catalog became stale', async () => {
    const staleError = Object.assign(new Error('服装列表已变化'), {
      code: 'LIVE2D_OPTIONS_STALE',
    })
    const onLoad = vi.fn().mockResolvedValue(OPTIONS)
    const onClose = vi.fn()
    render(
      <Live2DModelSheet
        open
        presentationTargetId="target-1"
        runtimeState={{ status: 'ready' }}
        onClose={onClose}
        onLoad={onLoad}
        onSelect={vi.fn().mockRejectedValue(staleError)}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /礼服2/ }))

    await waitFor(() => expect(onLoad).toHaveBeenCalledTimes(2))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '选择角色服装' })).toBeTruthy()
  })
})
