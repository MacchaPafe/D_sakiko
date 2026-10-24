import { randomUUID, createHash } from 'node:crypto'
import { SerialQueue, readJson, writeJson, safeId } from './files.js'

import { problemError as failure } from '../../shared/problems.js'

export { SerialQueue, readJson, writeJson, safeId, failure }
export const uuid = randomUUID

/**
 * 将捕获到的异常转换为可对外返回的标准化 Problem 对象。
 * @param {unknown} error 捕获到的异常。
 * @returns {import('../../shared/contracts/common.js').Problem} 脱敏后的错误信息对象。
 */
export function problem(error) {
  if (error?.name === 'AbortError')
    return { code: 'cancelled', message: '操作已取消。', retryable: false }
  // 未知内部异常统一返回通用提示，避免向前端暴露本地文件路径或密钥等敏感信息。
  return (
    error?.problem ?? {
      code: 'internal_error',
      message: '操作失败，请检查后端日志。',
      retryable: false
    }
  )
}

/**
 * 断言条件成立，不成立时抛出带错误码和错误信息的业务异常。
 * @param {unknown} condition 要检查的条件。
 * @param {string} [code] 错误码，默认 'invalid_input'。
 * @param {string} [message] 错误描述信息。
 * @returns {void}
 */
export function requireValue(condition, code = 'invalid_input', message = '输入不符合接口约定。') {
  if (!condition) throw failure(code, message)
}

/**
 * 深拷贝一个纯数据对象。
 * @template T
 * @param {T} value 要复制的数据。
 * @returns {T} 独立的数据副本。
 */
export function copy(value) {
  return structuredClone(value)
}

/**
 * 计算 JSON 数据的稳定 SHA-256 哈希摘要（忽略对象键的排列顺序）。
 * @param {unknown} value 要计算摘要的数据。
 * @returns {string} 十六进制哈希字符串。
 */
export function fingerprint(value) {
  function order(item) {
    if (Array.isArray(item)) return item.map(order)
    if (item && typeof item === 'object') {
      // 递归对普通对象的键名排序，确保语义相同但键顺序不同的对象算出相同的哈希。
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
 * 按顺序深合并多个配置对象（undefined 字段会被忽略，数组直接替换而非按下标合并）。
 * @param {...object} values 从默认配置到高优先级覆盖配置依次传入的对象。
 * @returns {object} 合并后的新对象。
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
 * 校验对象是否为普通对象，且只包含允许列表中的字段名。
 * @param {unknown} value 待校验的对象。
 * @param {string[]} names 允许出现的字段名列表。
 * @returns {void}
 */
export function fields(value, names) {
  requireValue(value && typeof value === 'object' && !Array.isArray(value))
  requireValue(Object.keys(value).every((key) => names.includes(key)))
}

/**
 * 包装一个 Promise，使调用方可以通过 AbortSignal 提前取消当前等待。
 * @template T
 * @param {Promise<T>} promise 被等待的异步任务。
 * @param {AbortSignal} [signal] 控制本次等待的取消信号。
 * @returns {Promise<T>} 原任务结果（若取消则以 AbortError 拒绝）。
 */
export function abortable(promise, signal) {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(new DOMException('操作已取消', 'AbortError'))
  return new Promise((resolve, reject) => {
    function abort() {
      reject(new DOMException('操作已取消', 'AbortError'))
    }
    // 仅结束当前调用方的等待，不中断底层共享的 promise 本身，避免影响其他共同等待同一任务的调用者。
    signal.addEventListener('abort', abort, { once: true })
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}

/**
 * 状态快照发布总线，用于向订阅者推送数据快照。
 */
export class SnapshotBus {
  listeners = new Map()

  /**
   * 订阅指定键的状态变更通知。
   * @template T
   * @param {string} key 订阅目标的标识。
   * @param {(snapshot: T) => void | Promise<void>} listener 状态更新时的回调函数。
   * @param {T} [initial] 可选的初始快照；若提供，会在订阅时立即触发一次回调。
   * @returns {() => void} 取消订阅的函数。
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

  /**
   * 向单个监听者投递深拷贝后的快照。
   * 隔离每个监听者的同步或异步异常，并单独深拷贝数据，防止某个监听者报错或篡改对象影响主流程及其他监听者。
   */
  deliver(listener, value) {
    try {
      Promise.resolve(listener(copy(value))).catch(() => {})
    } catch {
      /* 忽略单个观察者的异常 */
    }
  }

  /**
   * 向指定键的所有订阅者广播最新状态快照。
   * @param {string} key 订阅目标的标识。
   * @param {unknown} value 要广播的状态数据。
   * @returns {void}
   */
  emit(key, value) {
    for (const listener of this.listeners.get(key) ?? []) this.deliver(listener, value)
  }

  /**
   * 清空所有订阅者。
   * @returns {void}
   */
  clear() {
    this.listeners.clear()
  }
}
