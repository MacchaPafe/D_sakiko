/** @typedef {{backendRunId: string, status: 'running' | 'stopping'}} BackendHealth 一次后端运行的存活快照。 */
/** @typedef {{url: string, token: string, backendRunId: string}} HostProbe 仅供子进程探测宿主的受限凭据。 */

/**
 * HTTP 唯一公开方法表；未列出的存储、凭据、工具执行和调度写能力绝不接受远程调用。
 * true 表示命令需要传输层 requestId 去重；false 表示不使用命令缓存，不代表完全没有内部资源分配。
 * @type {Record<string, Record<string, boolean>>}
 */
export const API_METHODS = {
  onboarding: {
    getState: false,
    getBuiltinPreview: false,
    begin: true,
    returnToWelcome: true,
    skipLegacyImport: true,
    prepareOptionalResources: true,
    finish: true
  },
  notices: { list: false, dismiss: true },
  backgrounds: {
    catalog: false,
    preview: false,
    start: true,
    control: true,
    importImages: true,
    rename: true,
    remove: true
  },
  outfits: {
    listCharacters: false,
    setCharacter: true,
    catalog: false,
    getThumbnail: false,
    rename: true,
    setDefault: true,
    previewRemoval: false,
    remove: true,
    listJobs: false,
    start: true,
    control: true
  },
  setup: {
    getCatalog: false,
    getFeatureState: false,
    scanLegacy: false,
    createPlan: true,
    start: true,
    getJob: false,
    listJobs: false,
    controlJob: true,
    reviewVoiceUpdate: false,
    applyVoiceUpdate: true,
    previewVoiceRemoval: false,
    removeVoice: true,
    previewRuntimeRemoval: false,
    uninstallRuntime: true
  },
  recognition: { prepare: false, transcribe: false, getState: false, cancel: false },
  conversations: {
    listChats: false,
    getSnapshot: false,
    getReplayMaterials: false,
    getStagePresentation: false,
    getStageBackground: false,
    getStagePreview: false,
    inspectContext: false,
    listReminders: false,
    getReminder: false,
    createChat: true,
    createChatWithTurn: true,
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
    listWorkspaceFiles: false,
    listLotteries: false,
    drawLottery: true,
    setWorldbookInfo: true,
    setCharacterSelection: true,
    setGenerationOptions: true,
    patchSettings: true,
    setSpeechOptions: true,
    setPresentation: true,
    removeReminder: true,
    deleteChat: true,
    previewChatCleanup: false,
    deleteChats: true,
    exportChats: true,
    importChats: true
  },
  settings: {
    get: false,
    set: true,
    update: true,
    listConnections: false,
    listReasoningModels: false,
    saveToolCredential: true,
    getToolCredentialStatus: false,
    listProviderPresets: false,
    saveConnection: true,
    deleteConnection: true,
    reorderConnections: true,
    checkConnection: false,
    checkConnectionDraft: false,
    connectionAvailability: false
  },
  characters: {
    previewDeletion: false,
    previewFormDeletion: false,
    deleteForm: true,
    delete: true,
    list: false,
    get: false,
    save: true,
    resolveCapabilities: false,
    getPresentation: false,
    setOrder: true,
    deletePersona: true,
    formPersonas: false,
    listPersonas: false,
    getPersona: false,
    savePersona: true,
    resolvePersona: false
  },
  tools: { list: false },
  interactions: { list: false, complete: true },
  performance: { getProgress: false, setModelState: true, setActivity: true },
  assets: {
    inspect: false,
    collectUnused: false,
    addDraftAssets: true,
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
 * @property {() => Promise<Record<string, object | string>>} getUiPreferences 读取本机界面偏好。
 * @property {(key: string, value: object | string) => Promise<void>} setUiPreference 原子保存一项界面偏好。
 * @property {() => Promise<object[]>} getStorageOverview 查询应用数据、资源和缓存占用。
 * @property {() => Promise<Record<string, object>>} getCharacterStorageUsage 查询各角色关联的空间占用与摘要。
 * @property {() => Promise<object[]>} previewStorageCleanup 预览宿主管理的清理项目。
 * @property {(ids: string[]) => Promise<object[]>} cleanStorage 清理所选类型并返回单项结果。
 * @property {(id: string) => Promise<void>} openStorageFolder 打开已配置的存储根目录。
 * @property {() => Promise<{result?:object,problem?:object}>} selectVertexAccount 通过系统选择器导入服务账号到后端草稿。
 * @property {(action:string,id?:string) => Promise<{result?:unknown,problem?:object}>} chatGPTAccount 管理订阅账号，返回公开状态。
 * @property {() => Promise<BackendConnection>} getBackendConnection 获取本次运行的业务连接，不把令牌写入日志或持久 URL。
 * @property {() => Promise<{platform: string, appVersion: string}>} getRuntimeInfo 宿主平台与应用版本。
 * @property {(provider: string) => Promise<void>} openProviderKeyPage 在默认浏览器打开受支持的密钥管理页。
 * @property {(href:string)=>Promise<void>} openWebSource 打开用户选择的网页来源。
 * @property {(input:{bytes:Uint8Array,name:string,mediaType:string,draftId:string})=>Promise<object>} importAttachment 导入已选文件到对应草稿。
 * @property {(options: {purpose: import('./assets.js').AssetPurpose, directory?: boolean, entry?: string, draftId?: string, single?: boolean, extensions?: string[]}) => Promise<import('../../backend/resources/interface.js').AssetInfo[]>} importFiles 打开原生选择器并导入文件，只返回资源引用和元数据。
 * @property {(asset: import('./common.js').AssetRef) => Promise<boolean>} saveChatArchive 选择归档保存位置，取消返回 false。
 * @property {() => Promise<{sourceId: string, name: string} | null>} selectLegacyDirectory 只授权扫描旧版目录，不复制整个目录。
 * @property {(target: import('../../backend/live2d-import/outfits.js').OutfitTarget) => Promise<{result?: {modelId: string, alreadyInstalled: boolean}, problem?: ApiProblem} | null>} importOutfitDirectory 选择并安装一个本地模型文件夹。
 * @property {() => Promise<'tray' | 'quit' | null>} getWindowCloseBehavior 读取 Windows 关闭窗口偏好。
 * @property {(behavior: 'tray' | 'quit') => Promise<'tray' | 'quit'>} setWindowCloseBehavior 保存 Windows 关闭窗口偏好。
 * @property {() => Promise<boolean>} getWindowVisibility 读取主窗口是否可见且未最小化。
 * @property {(listener: (visible: boolean) => void) => () => void} onWindowVisibility 订阅窗口可见性变更。
 */

/** @typedef {{ baseUrl: string, token: string }} BackendConnection 仅由可信桌面 bootstrap 或显式 WebUI 授权提供。 */
/** @typedef {{ requestId?: string, signal?: AbortSignal }} CallOptions requestId 是传输重试身份，与交互 requestId 和 TurnOptions.requestId 分开。 */
