/** @typedef {'chat-image' | 'avatar' | 'attachment' | 'model' | 'live2d' | 'audio' | 'archive' | 'temporary'} AssetPurpose 资源的业务用途，不包含磁盘位置。 */

/** 客户端可以申请的导入用途；临时工作材料仅供后端创建。 */
export const IMPORT_PURPOSES = [
  'chat-image',
  'avatar',
  'attachment',
  'model',
  'live2d',
  'audio',
  'archive'
]
