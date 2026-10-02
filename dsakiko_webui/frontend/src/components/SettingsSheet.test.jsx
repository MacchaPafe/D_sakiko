import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SettingsSheet } from './SettingsSheet'

const SETTINGS = {
  chat_id: 'sakiko-chat', character_form: 'black', mask_actions: ['off'],
  voice: { character_name: '祥子', speech_speed: 1, sentence_pause_seconds: 0.5 },
  llm: { selected_id: 'model', options: [{ id: 'model', label: '测试', model: 'test' }] },
}

describe('Sakiko settings', () => {
  afterEach(cleanup)

  it('disables only the unconfigured mask action and binds operations to the loaded chat', async () => {
    const onMaskAction = vi.fn().mockResolvedValue({ accepted: true })
    const onFormChange = vi.fn().mockResolvedValue({ accepted: true })
    const onLoad = vi.fn().mockResolvedValue(SETTINGS)
    render(<SettingsSheet open onLoad={onLoad} onClose={vi.fn()} onSave={vi.fn()}
      onFormChange={onFormChange} onMaskAction={onMaskAction} />)
    expect(await screen.findByRole('button', { name: '戴上面具' })).toHaveProperty('disabled', true)
    fireEvent.click(screen.getByRole('button', { name: '摘下面具' }))
    await waitFor(() => expect(onMaskAction).toHaveBeenCalledWith('off', 'sakiko-chat'))
    await waitFor(() => expect(screen.getByRole('button', { name: '切换为白祥' }).disabled).toBe(false))
    fireEvent.click(screen.getByRole('button', { name: '切换为白祥' }))
    await waitFor(() => expect(onFormChange).toHaveBeenCalledWith('white', 'sakiko-chat'))
    await waitFor(() => expect(onLoad).toHaveBeenCalledTimes(2))
  })

  it('prevents form and mask operations during generation or playback', async () => {
    const action = vi.fn()
    render(<SettingsSheet open busy onLoad={vi.fn().mockResolvedValue(SETTINGS)}
      onClose={vi.fn()} onSave={vi.fn()} onFormChange={action} onMaskAction={action} />)
    for (const name of ['切换为白祥', '戴上面具', '摘下面具']) {
      const button = await screen.findByRole('button', { name })
      expect(button.disabled).toBe(true)
      fireEvent.click(button)
    }
    expect(action).not.toHaveBeenCalled()
  })
})
