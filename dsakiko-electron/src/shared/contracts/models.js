/** 连接的可保存定义与一次请求的后端私有解析结果分开，凭据不进入 Chat、归档或前端观察。 */

/**
 * @typedef {object} CapabilityDeclaration
 * @property {'supported' | 'unsupported' | 'unknown'} status 未知不可当作支持。
 * @property {string | null} source 声明来源，例如目录条目或用户显式覆盖。
 */

/**
 * @typedef {object} ModelCapabilities
 * @property {CapabilityDeclaration} imageInput 图片输入支持。
 * @property {CapabilityDeclaration} fileInput 一般文件输入支持，与图片区分。
 * @property {CapabilityDeclaration} fileUpload 上传接口支持，不能从输入能力或 supportedUrls 推测。
 * @property {CapabilityDeclaration} reasoning 推理能力。
 * @property {string[]} reasoningEfforts 支持的强度枚举。
 * @property {number | null} contextTokens 已知上下文上限。
 * @property {string | null} contextSource 上限来源。
 * @property {string[]} mediaTypes 已声明支持的附件类型。
 */

/**
 * @typedef {object} ModelDefinition
 * @property {string} id 实际请求模型名。
 * @property {{ provider: string, model: string } | null} catalogMapping 明确目录映射，不能用同名模型自动匹配。
 * @property {ModelCapabilities} declared 原始能力声明。
 * @property {Partial<ModelCapabilities>} overrides 明确覆盖，解析时保留各字段来源。
 */

/**
 * @typedef {object} ConnectionDefinition
 * @property {string} id 稳定连接身份。
 * @property {number} revision 所基于的版本，保存时原子递增。
 * @property {string} name 显示名称。
 * @property {string} provider 供应商适配器名，使用已注册适配器。
 * @property {string} baseUrl 服务地址。
 * @property {string} protocol 协议选择，不能假设所有兼容端点相同。
 * @property {boolean} credentialConfigured 只读凭据存在标记，不返回密钥或可供页面读取的句柄。
 * @property {'inline' | 'file-api'} attachmentTransport 附件传送方案，按模型/协议分别校验。
 * @property {ModelDefinition[]} models 模型列表及准确能力来源。
 */

/**
 * @typedef {object} ResolvedModelConnection
 * @property {string} connectionId 来源连接。
 * @property {number} revision 解析时版本。
 * @property {string} modelId 固定模型。
 * @property {string} provider 固定适配器。
 * @property {string} baseUrl 固定地址。
 * @property {string} protocol 固定协议。
 * @property {string} credentialScope 后端内部凭据作用域，实际密钥通过装配的私有能力取得。
 * @property {'inline' | 'file-api'} attachmentTransport 固定传送方案。
 * @property {ModelCapabilities} capabilities 合并且保留来源的能力。
 */

export {}
