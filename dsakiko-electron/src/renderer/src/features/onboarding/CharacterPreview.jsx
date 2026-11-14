import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { LoaderCircle, RotateCcw } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { createBackendClient } from '@/backend/client.js'
import { desktopHost } from '../../host/desktop.js'
import { Live2DPreview } from '../../performance/live2d-preview.js'
import { WelcomePreview } from './welcome-preview.js'
import { previewCharacters } from './characters.js'

async function connectBackend() {
  return createBackendClient(await desktopHost().getBackendConnection())
}

function CharacterChoice({ character, selected, onSelect }) {
  function handleSelect() {
    onSelect(character.id)
  }
  return (
    <Button
      variant="ghost"
      className="welcome-character"
      aria-pressed={selected}
      aria-label={`预览${character.name}`}
      onClick={handleSelect}
    >
      <img src={character.avatar} alt="" width="28" height="28" />
      {character.name}
    </Button>
  )
}

/** @param {{showCharacterPicker?: boolean, referenceWidth?: number}} props 角色选择器与舞台构图；整段引导保留当前角色和追踪。 @returns {import('react').ReactElement} 引导共用的只读舞台。 */
export function CharacterPreview({ showCharacterPicker = false, referenceWidth = 864 }) {
  const canvasHost = useRef(null)
  const [preview] = useState(
    () =>
      new WelcomePreview({
        connect: connectBackend,
        createSurface: (element) => new Live2DPreview(element),
        host: desktopHost
      })
  )
  const state = useSyncExternalStore(preview.subscribe, preview.getSnapshot)
  const name = previewCharacters.find((character) => character.id === state.characterId).name

  useEffect(() => {
    preview.mount(canvasHost.current)
    return () => preview.dispose()
  }, [preview])

  useEffect(() => {
    preview.setReferenceWidth(referenceWidth)
  }, [preview, referenceWidth])

  function handleSelect(characterId) {
    void preview.select(characterId)
  }

  return (
    <section className="welcome-stage" aria-label={`${name}的角色预览`}>
      <div
        ref={canvasHost}
        className="welcome-canvas"
        data-preview-state={state.phase}
        data-character={state.characterId}
        aria-busy={state.phase === 'loading'}
      />
      {state.phase === 'loading' && (
        <div className="welcome-status" role="status">
          <LoaderCircle className="welcome-spinner" size={16} aria-hidden="true" />
          正在加载{name}…
        </div>
      )}
      {state.phase === 'error' && (
        <div className="welcome-status welcome-error" role="alert">
          <p>{state.problem}</p>
          <Button variant="outline" size="sm" onClick={preview.retry}>
            <RotateCcw aria-hidden="true" />
            重试
          </Button>
        </div>
      )}
      {showCharacterPicker && (
        <div className="welcome-characters" role="group" aria-label="切换预览角色">
          {previewCharacters.map((character) => (
            <CharacterChoice
              key={character.id}
              character={character}
              selected={character.id === state.characterId}
              onSelect={handleSelect}
            />
          ))}
        </div>
      )}
    </section>
  )
}
