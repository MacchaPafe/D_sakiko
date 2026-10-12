/* eslint no-unused-vars: ["error", { "args": "none" }] -- 未实现的审查声明。 */

/** 工具目录统一依赖、配置及权限检查；世界书工具由范围绑定注入，不受普通开关替代。 */
export class ToolCatalog {
  /**
   * 列出工具目录中可供选择的工具及其声明。
   * @returns {Promise<import('../../shared/contracts/tools.js').ToolDefinition[]>} 可选择的工具定义；不执行工具。
   */
  async list() {}

  /**
   * 将对话选择的工具及其依赖解析为本次可执行的工具集合。
   * 按 Chat 绑定窄能力，拒绝越界读取与任意指定写路径；一次固定依赖闭包，不在 run 中读全局选择。
   * 所有工具都记录调用/结果，不提供 visibility 或隐藏通知分支。
   * @param {string} chatId 唯一作用对话。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} selection 用户选择。
   * @param {import('../../shared/contracts/tools.js').ToolAccessScope} access 宿主已授权范围。
   * @returns {Promise<import('../../shared/contracts/tools.js').AgentTool[]>} 后端可执行材料，由装配提供具体函数。
   */
  async resolve(chatId, selection, access) {}
}

/**
 * 已由装配绑定 Chat 的提醒创建能力，只交给工具，不向页面/Conversations 公共管理入口暴露手动添加。
 * 持久化与所属 Chat 保存串行；同调用去重，新 Turn 的重新生成可创建新的提醒。
 */
export class ReminderWriter {
  /**
   * 为绑定的对话创建并保存一条到期后触发角色回复的提醒。
   * 新提醒分配全应用唯一 UUID，同一次工具调用的通信重试返回同一提醒。
   * @param {{ dueAt: string, instruction: string }} options 到期时间与内容。
   * @param {import('../../shared/contracts/tools.js').ToolExecutionContext} source 创建调用身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder>} 已持久化提醒，不提前声称创建成功。
   */
  async create(options, source) {}
}

/**
 * 宿主交互的窄接口；实例绑定 Chat 与受控宿主。可靠登记时建立 request → Turn/call/组的映射。
 * async 的最终结果路由到 Conversations.acceptInteractionResult，block-async 只结算原等待，不能双重回流。
 * 重启不恢复未完成交互；旧宿主回调没有资格时拒收，不根据 payload 重建资格。
 */
export class Interactions {
  /**
   * 向宿主发起抽签、问答等交互，并按工具模式等待可靠受理或最终交互结果。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 固定请求身份与交互材料。
   * @param {AbortSignal} signal 当前执行的取消信号。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ToolOutcome>} async 仅可靠受理后返回 accepted；block-async 等待实际终态。
   */
  async request(request, signal) {}

  /**
   * 接收宿主交互的最终结果，交还当前等待的工具或转交对话模块安排后续回复。
   * 校验已登记请求与宿主来源，重复完成幂等；失败或取消不会制造后续回复。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 宿主最终回调。
   * @returns {Promise<void>} 已按请求模式路由，不代表后续 Turn 已受理。
   */
  async complete(completion) {}
}
