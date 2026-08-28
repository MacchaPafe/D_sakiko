/** Versioned bridge envelope shared by the renderer and Python controller. */
export interface ProtocolMessage {
  v: number
  type: string
  event_id: string
  session_id: string
  source: string
  timestamp?: number
  seq?: number
  data: Record<string, any>
}

/** Commands decided by the shared authoritative owner and executed by Electron. */
export type RendererCommandType =
  | 'motion' | 'play_motion' | 'audio' | 'play_audio'
  | 'stop_audio' | 'stop_motion' | 'text' | 'segment_started'
  | 'user_text' | 'thinking' | 'thinking_changed' | 'reset'
  | 'reset_renderer' | 'set_expression' | 'mouth_amplitude' | 'bye' | 'close_renderer'

export interface RendererCommand {
  type: RendererCommandType
  event_id?: string
  session_id?: string
  data: Record<string, any>
}

export interface RendererFact {
  type: 'renderer_ready' | 'renderer_intent' | 'motion_started'
    | 'motion_finished' | 'audio_started' | 'audio_ended' | 'mouth_amplitude' | 'command_failed'
  event_id?: string
  data: Record<string, any>
}

/** Text-only renderer event; it contains no behavior policy. */
export interface RendererControllerEvent {
  type: string
  data: Record<string, any>
}
