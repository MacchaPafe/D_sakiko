/**
 * HTTP 唯一公开方法表；未列出的存储、凭据、工具执行和调度写能力绝不接受远程调用。
 * true 表示命令需要传输层 requestId 去重；false 表示不使用命令缓存，不代表完全没有内部资源分配。
 * @type {Record<string, Record<string, boolean>>}
 */
export const API_METHODS = {
  conversations: {
    listChats: false,
    getSnapshot: false,
    getReplayMaterials: false,
    inspectContext: false,
    listReminders: false,
    getReminder: false,
    createChat: true,
    createTurn: true,
    generateScenario: true,
    stopTurn: true,
    recreateTurn: true,
    editAndRecreateTurn: true,
    editHistory: true,
    truncateFromTurn: true,
    rollback: true,
    forkChat: true,
    regenerateAudio: true,
    compressHistory: true,
    retrySave: true,
    renameChat: true,
    setChatOrder: true,
    setScenario: true,
    setTools: true,
    setWorldbookInfo: true,
    setCharacterSelection: true,
    setGenerationOptions: true,
    setSpeechOptions: true,
    setPresentation: true,
    removeReminder: true,
    deleteChat: true,
    exportChats: true,
    importChats: true
  },
  settings: {
    get: false,
    set: true,
    update: true,
    listConnections: false,
    saveConnection: true,
    deleteConnection: true,
    checkConnection: false
  },
  characters: {
    delete: true,
    list: false,
    get: false,
    save: true,
    resolveCapabilities: false,
    setOrder: true,
    deletePersona: true,
    listPersonas: false,
    getPersona: false,
    savePersona: true,
    resolvePersona: false
  },
  tools: { list: false },
  interactions: { list: false, complete: true },
  performance: { getProgress: false, setModelState: true },
  assets: {
    inspect: false,
    upload: true,
    access: false,
    renewAccess: false,
    releaseAccess: false,
    createDraft: true,
    renewDraft: false,
    discardDraft: true,
    removeDraftAssets: true,
    commitDraft: true,
    removeLibraryItem: true,
    listLibraryItems: false
  }
}

/** @typedef {{ code: string, message: string, retryable: boolean }} ApiProblem */
/**
 * @typedef {object} DesktopHost Electron 专有能力，WebUI 使用自己的授权入口。
 * @property {() => Promise<BackendConnection>} getBackendConnection 获取本次运行的业务连接，不把令牌写入日志或持久 URL。
 * @property {() => Promise<{platform: string, appVersion: string}>} getRuntimeInfo 宿主平台与应用版本。
 * @property {(options: {purpose: import('./assets.js').AssetPurpose, directory?: boolean, entry?: string, draftId?: string}) => Promise<import('../../backend/resources/interface.js').AssetInfo[]>} importFiles 打开原生选择器并导入文件，只返回资源引用和元数据。
 */

/** @typedef {{ baseUrl: string, token: string }} BackendConnection 仅由可信桌面 bootstrap 或显式 WebUI 授权提供。 */
/** @typedef {{ requestId?: string, signal?: AbortSignal }} CallOptions requestId 是传输重试身份，与交互 requestId 和 TurnOptions.requestId 分开。 */
