/** 情感标签 → Live2D 运动组名 */
export const EMOTION_MAP: Record<string, string> = {
  'LABEL_0': 'happiness',
  'LABEL_1': 'sadness',
  'LABEL_2': 'anger',
  'LABEL_3': 'disgust',
  'LABEL_4': 'like',
  'LABEL_5': 'surprise',
  'LABEL_6': 'fear',
  happiness: 'happiness',
  sadness: 'sadness',
  anger: 'anger',
  disgust: 'disgust',
  like: 'like',
  surprise: 'surprise',
  fear: 'fear',
}

/** 每个运动组包含的动作数量 */
export const MOTION_GROUP_SIZES: Record<string, number> = {
  happiness: 6,
  sadness: 4,
  anger: 7,
  disgust: 2,
  like: 4,
  surprise: 4,
  fear: 2,
  IDLE: 7,
  text_generating: 4,
  bye: 1,
  change_character: 3,
  idle_motion: 1,
  talking_motion: 1,
}

/** 按角色适配的动作组数量 */
export const MODEL_SPECIFIC_SIZES: Record<string, Record<string, number>> = {
  sakiko: {
    happiness: 6, sadness: 4, anger: 7, disgust: 2, like: 4, surprise: 4, fear: 2,
    IDLE: 7, text_generating: 4, bye: 1, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
  anon: {
    happiness: 6, sadness: 6, anger: 6, disgust: 6, like: 6, surprise: 6, fear: 6,
    IDLE: 9, text_generating: 3, bye: 2, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
  soyo: {
    happiness: 6, sadness: 6, anger: 6, disgust: 6, like: 6, surprise: 6, fear: 6,
    IDLE: 9, text_generating: 3, bye: 2, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
  kasumi: {
    happiness: 6, sadness: 6, anger: 6, disgust: 6, like: 6, surprise: 6, fear: 6,
    IDLE: 9, text_generating: 3, bye: 2, change_character: 3, idle_motion: 1, talking_motion: 1,
  },
}

/** 长音频运动循环参数 */
export const LONG_AUDIO_THRESHOLD_SECONDS = 6.0
export const LONG_AUDIO_REPEAT_DELAY_SECONDS = 2.5
export const LONG_AUDIO_MAX_REPEATS = 2

/** 空闲恢复延迟（毫秒） */
export const IDLE_RECOVER_DELAY_MS = 2500

/** 定时待机间隔（毫秒） */
export const TIMED_IDLE_INTERVAL_MS = 25000

/** 定时待机启动失败后的最小重试间隔（毫秒） */
export const TIMED_IDLE_RETRY_DELAY_MS = 2500

/** 思考动作间隔（首次 / 后续），单位秒 */
export const THINK_INTERVAL_FIRST = 1
export const THINK_INTERVAL_SUBSEQUENT = 15

/** 睁眼过渡时长（毫秒） */
export const EYE_OPEN_DURATION_MS = 100

/** bye 运动完成回调缺失时的异常关闭兜底（毫秒） */
export const BYE_TIMEOUT_MS = 15000

/** 点击节流（毫秒） */
export const CLICK_THROTTLE_MS = 200

/** WS 事件类型 */
export interface StateMachineEvent {
  type: 'assistant_segment' | 'emotion' | 'initial_model' | 'text_generating' | 'cancel' | 'cancel_turn'
    | 'user_text' | 'bye' | 'switch_character' | 'switch_live2d'
    | 'sakiko_state' | 'char_converted' | 'talking' | 'expression' | 'theme'
    // Bridge lifecycle is consumed by App before it reaches the local FSM.
    | 'bridge_ready' | 'renderer_recovery' | 'assistant_turn_complete'
  data: any
}
