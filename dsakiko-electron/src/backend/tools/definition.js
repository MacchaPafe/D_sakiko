/** 创建严格的对象参数结构，避免模型传入执行器不识别的选项。 */
export function parameters(properties = {}, required = []) {
  return { type: 'object', properties, required, additionalProperties: false }
}

export const textParameter = { type: 'string', minLength: 1, maxLength: 4000 }

/**
 * 创建普通后端工具；异步网络操作仍在当前模型轮次返回结果。
 * @param {object} definition 工具的声明。
 * @param {Function} execute 接收绑定上下文、参数、执行身份和取消信号。
 * @returns {object} 可交给工具目录的条目。
 */
export function syncTool(definition, execute) {
  return {
    definition: {
      dependencies: [],
      mode: 'sync',
      requiredCapabilities: [],
      resultSchema: { type: 'object' },
      ...definition
    },
    bind: (context) => async (input, source, signal) => {
      signal?.throwIfAborted()
      return { status: 'succeeded', value: await execute(context, input, source, signal) }
    }
  }
}
