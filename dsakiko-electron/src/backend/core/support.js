import { randomUUID, createHash } from 'node:crypto'
import { SerialQueue, readJson, writeJson, safeId } from './files.js'

import { problemError as failure } from '../../shared/problems.js'

export { SerialQueue, readJson, writeJson, safeId, failure }
export const uuid = randomUUID

/**
 * 将内部异常转换为可传输的问题；不回显未知异常的路径或凭据。
 * @param {unknown} error 捕获的异常。
 * @returns {import('../../shared/contracts/common.js').Problem} 可公开的稳定错误信息。
 */
export function problem(error) {
  if (error?.name === 'AbortError')
    return { code: 'cancelled', message: '操作已取消。', retryable: false }
  return (
    error?.problem ?? {
      code: 'internal_error',
      message: '操作失败，请检查后端日志。',
      retryable: false
    }
  )
}

/**
 * 拒绝不符合业务条件的输入。
 * @param {unknown} condition 为假时拒绝操作。
 * @param {string} [code] 可供调用方分支处理的错误码。
 * @param {string} [message] 可公开的中文说明。
 * @returns {void} 条件满足时正常返回，否则抛出业务错误。
 */
export function requireValue(condition, code = 'invalid_input', message = '输入不符合接口约定。') {
  if (!condition) throw failure(code, message)
}

/**
 * 复制接口数据，避免观察者或调用方修改权威状态。
 * @template T
 * @param {T} value 支持结构化克隆的数据，不含函数或实例能力。
 * @returns {T} 与原对象独立的数据副本。
 */
export function copy(value) {
  return structuredClone(value)
}

/**
 * 按键排序以比较同一请求的语义材料，不依赖对象字段插入顺序。
 * @param {unknown} value 普通 JSON 材料；数组顺序仍有语义。
 * @returns {string} 用于请求去重和过期检查的摘要，不用作身份授权。
 */
export function fingerprint(value) {
  function order(item) {
    if (Array.isArray(item)) return item.map(order)
    if (item && typeof item === 'object') {
      return Object.fromEntries(
        Object.keys(item)
          .sort()
          .map((key) => [key, order(item[key])])
      )
    }
    return item
  }
  return createHash('sha256')
    .update(JSON.stringify(order(value)) ?? 'null')
    .digest('hex')
}

/**
 * 合并普通数据；数组整体替换，禁止原型键进入配置。
 * @param {...object} values 从默认值到覆盖值依次合并；undefined 表示继承。
 * @returns {object} 独立的合并结果，不修改任一输入。
 */
export function merge(...values) {
  const result = {}
  for (const value of values) {
    if (!value) continue
    for (const [key, item] of Object.entries(value)) {
      requireValue(!['__proto__', 'prototype', 'constructor'].includes(key))
      if (item === undefined) continue
      if (item && typeof item === 'object' && !Array.isArray(item))
        result[key] = merge(result[key], item)
      else result[key] = copy(item)
    }
  }
  return result
}

/**
 * 限制对象字段，避免通过扩展参数绕过公开接口。
 * @param {unknown} value 待校验对象。
 * @param {string[]} names 允许的字段；必填性由业务模块另行检查。
 * @returns {void} 含未知字段或不是普通对象时抛出业务错误。
 */
export function fields(value, names) {
  requireValue(value && typeof value === 'object' && !Array.isArray(value))
  requireValue(Object.keys(value).every((key) => names.includes(key)))
}

/**
 * 等待可以单独取消，取消不会影响被等待任务或其他等待者。
 * @template T
 * @param {Promise<T>} promise 已开始且可能共享的异步任务。
 * @param {AbortSignal} [signal] 只控制当前等待。
 * @returns {Promise<T>} 任务结果，或当前等待的 AbortError。
 */
export function abortable(promise, signal) {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(new DOMException('操作已取消', 'AbortError'))
  return new Promise((resolve, reject) => {
    function abort() {
      reject(new DOMException('操作已取消', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/** 本地快照通知隔离观察者异常，不能让页面监听阻断业务推进。 */
export class SnapshotBus {
  listeners = new Map()

  /**
   * 登记观察者并交付可选的初始快照；先登记可避免回调触发修改时漏通知。
   * @template T
   * @param {string} key 观察对象身份。
   * @param {(snapshot: T) => void | Promise<void>} listener 接收独立快照。
   * @param {T} [initial] 省略则等待下次发布。
   * @returns {() => void} 幂等取消函数，不改变被观察对象。
   */
  subscribe(key, listener, initial) {
    const group = this.listeners.get(key) ?? new Set()
    this.listeners.set(key, group)
    group.add(listener)
    if (initial !== undefined) this.deliver(listener, initial)
    return () => {
      group.delete(listener)
      if (!group.size) this.listeners.delete(key)
    }
  }

  /** 每位观察者单独克隆数据，且其同步或异步失败都不影响业务提交。 */
  deliver(listener, value) {
    try {
      Promise.resolve(listener(copy(value))).catch(() => {})
    } catch {
      /* 单个观察者不影响其他观察者。 */
    }
  }

  /**
   * 发布当前身份的完整快照。
   * @param {string} key 观察对象身份。
   * @param {unknown} value 可克隆的数据。
   * @returns {void} 不等待观察者完成。
   */
  emit(key, value) {
    for (const listener of this.listeners.get(key) ?? []) this.deliver(listener, value)
  }

  /** 释放所有订阅；不承担业务任务的取消。
   * @returns {void} 观察者不再收到后续通知。
   */
  clear() {
    this.listeners.clear()
  }
}
