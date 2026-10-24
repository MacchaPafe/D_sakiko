import { copy, requireValue } from './support.js'

/** @typedef {{valid: boolean, message: string | null}} ValidationResult 配置项的校验结果。 */
/**
 * @typedef {object} ConfigItemOptions
 * @property {unknown} [default] 配置项缺失时采用的默认值。
 * @property {boolean} [optional] 是否允许省略该配置项。
 * @property {(value: unknown) => boolean} validate 判断配置值是否合法的函数。
 * @property {string} message 校验失败时的错误说明。
 * @property {(value: unknown) => unknown} [correct] 修复非法配置值的转换函数。
 */

/**
 * 单个配置项的定义与校验规则。
 */
export class ConfigItem {
  #default
  #check
  #repair
  #message

  /**
   * 创建一个配置项定义。
   * @param {ConfigItemOptions} options 配置项的校验规则、默认值及修复函数。
   */
  constructor(options) {
    this.hasDefault = Object.hasOwn(options, 'default')
    this.optional = options.optional ?? false
    this.#default = copy(options.default)
    this.#check = options.validate
    this.#repair = options.correct
    this.#message = options.message
    if (this.hasDefault)
      requireValue(this.validate(this.#default).valid, 'invalid_config_definition', options.message)
    // ConfigItem 实例会被全局共享，冻结自身并深拷贝默认值可防止调用方意外篡改公共规则。
    Object.freeze(this)
  }

  /**
   * 获取该配置项的默认值。
   * @returns {unknown} 默认值的独立副本。
   */
  get defaultValue() {
    return copy(this.#default)
  }

  /**
   * 检查给定的值是否符合该配置项的规则。
   * @param {unknown} value 待检查的配置值。
   * @returns {ValidationResult} 校验结果与错误说明。
   */
  validate(value) {
    const valid = this.#check(value)
    return { valid, message: valid ? null : this.#message }
  }

  /**
   * 尝试将不合法的配置值修复为合法值。如果无法修复，则会抛出异常。
   * @param {unknown} value 原始配置值。
   * @returns {unknown} 修复后的配置值副本。
   */
  correct(value) {
    let next = value
    if (!this.validate(value).valid) {
      if (this.#repair) next = this.#repair(copy(value))
      else if (this.hasDefault) next = this.defaultValue
    }
    requireValue(this.validate(next).valid, 'invalid_settings', this.#message)
    return copy(next)
  }

  /**
   * 校验并返回配置值。当开启自动修复时，会尝试修正不合法的输入。
   * @param {unknown} value 待校验的配置值。
   * @param {string} path 配置项的路径名称，用于在报错信息中指明出错位置。
   * @param {boolean} [correct] 是否尝试自动修复不合法的值。
   * @returns {unknown} 校验通过的配置值副本。
   */
  parse(value, path, correct = false) {
    const result = this.validate(value)
    if (result.valid) return copy(value)
    if (correct) {
      try {
        return this.correct(value)
      } catch {
        // 捕获内部修复失败，统一由下方抛出附带字段路径 path 的错误，方便调用方定位具体出错字段。
      }
    }
    requireValue(false, 'invalid_settings', `${path}：${result.message}`)
  }
}

/**
 * @typedef {object} NumberConfigOptions
 * @property {number} [default] 默认值。
 * @property {boolean} [optional] 是否允许省略。
 * @property {number} [min] 允许的最小值（包含边界）。
 * @property {number} [max] 允许的最大值（包含边界）。
 * @property {number} [exclusiveMin] 允许的最小值（不包含边界）。
 * @property {boolean} [integer] 是否必须为整数。
 */
/**
 * 创建一个数字类型的配置项定义。
 * @param {NumberConfigOptions} [options] 数字范围与整数约束。
 * @returns {ConfigItem} 数字配置项定义。
 */
function number(options = {}) {
  const { min = -Infinity, max = Infinity, exclusiveMin = -Infinity, integer = false } = options
  const requirements = [`必须为${integer ? '整数' : '有限数字'}`]
  if (Number.isFinite(min)) requirements.push(`不小于 ${min}`)
  if (Number.isFinite(max)) requirements.push(`不大于 ${max}`)
  if (Number.isFinite(exclusiveMin)) requirements.push(`大于 ${exclusiveMin}`)
  return new ConfigItem({
    ...options,
    validate: (value) =>
      Number.isFinite(value) &&
      (!integer || Number.isInteger(value)) &&
      value >= min &&
      value <= max &&
      value > exclusiveMin,
    message: requirements.join('，'),
    correct(value) {
      // 仅对数字进行四舍五入和上下界截断，不将字符串隐式转为数字，避免掩盖传参类型错误。
      if (!Number.isFinite(value)) return value
      return Math.min(max, Math.max(min, integer ? Math.round(value) : value))
    }
  })
}

/**
 * 创建一个布尔类型的配置项定义。
 * @param {{default?: boolean, optional?: boolean}} [options] 默认值与可选配置。
 * @returns {ConfigItem} 布尔配置项定义。
 */
function boolean(options = {}) {
  return new ConfigItem({
    ...options,
    validate: (value) => typeof value === 'boolean',
    message: '必须为布尔值'
  })
}

/**
 * 创建一个字符串类型的配置项定义。
 * @param {{default?: string, optional?: boolean, minLength?: number}} [options] 默认值与最小长度约束。
 * @returns {ConfigItem} 字符串配置项定义。
 */
function string(options = {}) {
  const minLength = options.minLength ?? 0
  return new ConfigItem({
    ...options,
    validate: (value) => typeof value === 'string' && value.length >= minLength,
    message: `必须为长度至少 ${minLength} 的字符串`
  })
}

/**
 * 创建一个枚举类型的配置项定义。
 * @param {{values: (string | number | boolean | null)[], default?: unknown, optional?: boolean}} options 允许的枚举值列表与默认值。
 * @returns {ConfigItem} 枚举配置项定义。
 */
function enumeration(options) {
  const values = [...options.values]
  return new ConfigItem({
    ...options,
    validate: (value) => values.includes(value),
    message: `必须为 ${values.map(String).join('、')} 之一`
  })
}

/** 用于快速创建基础类型配置项的工厂对象。 */
export const config = Object.freeze({ number, boolean, string, enum: enumeration })

/** @typedef {{[key: string]: ConfigItem | ConfigDefinition}} ConfigDefinition 嵌套的配置结构定义。 */

/**
 * 判断给定的值是否为普通的配置对象。
 * @param {unknown} value 待检查的值。
 * @returns {boolean} 是否为普通对象。
 */
export function isConfigObject(value) {
  // 仅允许字面量对象或无原型对象，拒绝数组、日期等特殊实例混入配置树。
  return (
    value !== null &&
    typeof value === 'object' &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
  )
}

/**
 * 根据配置定义校验并解析配置对象。
 * @param {ConfigDefinition} definition 配置结构定义。
 * @param {unknown} value 待解析的原始配置对象。
 * @param {{defaults?: boolean, partial?: boolean, deepPartial?: boolean, correct?: boolean, path?: string}} [options] 解析选项：defaults 控制是否填充默认值，partial/deepPartial 控制是否允许只传入部分字段，correct 控制是否自动修复非法值。
 * @returns {object} 校验通过的配置对象副本。
 */
export function parseConfig(definition, value, options = {}) {
  const {
    defaults = false,
    partial = false,
    deepPartial = false,
    correct = false,
    path = 'settings'
  } = options
  requireValue(isConfigObject(value), 'invalid_settings', `${path}：必须为配置对象`)
  for (const key of Object.keys(value))
    requireValue(
      !['__proto__', 'constructor', 'prototype'].includes(key) && Object.hasOwn(definition, key),
      'invalid_settings',
      `${path}.${key}：未知配置项`
    )
  const result = {}
  for (const [key, item] of Object.entries(definition)) {
    const present = Object.hasOwn(value, key)
    const fieldPath = `${path}.${key}`
    if (!present && partial) continue
    // 如果是具体的配置项（ConfigItem），直接校验或填充默认值。
    if (item instanceof ConfigItem) {
      if (!present && defaults && item.hasDefault) result[key] = item.defaultValue
      else if (!present && item.optional) continue
      else result[key] = item.parse(value[key], fieldPath, correct)
    } else {
      // 如果是嵌套的配置分组，则递归解析下级字段；partial 只放行顶层缺失字段，deepPartial 才会放行嵌套分组内的缺失字段。
      result[key] = parseConfig(item, !present && defaults ? {} : value[key], {
        defaults,
        partial: deepPartial,
        deepPartial,
        correct,
        path: fieldPath
      })
    }
  }
  return result
}

/**
 * 校验并将点分隔的配置路径拆分为键名数组。
 * @param {ConfigDefinition} definition 配置结构定义。
 * @param {string} path 点分隔的配置项路径（如 'generation.temperature'）。
 * @returns {string[]} 拆分后的键名数组。
 */
export function configPath(definition, path) {
  requireValue(typeof path === 'string' && path.length > 0, 'invalid_settings', '配置路径不能为空')
  const keys = path.split('.')
  let item = definition
  for (const key of keys) {
    requireValue(
      !(item instanceof ConfigItem) &&
        !['__proto__', 'constructor', 'prototype'].includes(key) &&
        Object.hasOwn(item, key),
      'invalid_settings',
      `${path}：未知配置项`
    )
    item = item[key]
  }
  // 限制路径必须精确到具体的配置项，防止调用方把整个分组当成单个配置项直接读写。
  requireValue(
    item instanceof ConfigItem,
    'invalid_settings',
    `${path}：路径必须指向配置项，不能指向分组`
  )
  return keys
}

/**
 * 将单个配置项的路径和新值转换为嵌套的配置更新对象。
 * @param {ConfigDefinition} definition 配置结构定义。
 * @param {string} path 点分隔的配置项路径（如 'generation.temperature'）。
 * @param {unknown} value 要设置的新值。
 * @returns {object} 转换后的嵌套更新对象（如 `{ generation: { temperature: value } }`）。
 */
export function configPatch(definition, path, value) {
  const keys = configPath(definition, path)
  let patch = value
  // 从最内层的键向外逐层包裹，将扁平路径还原为嵌套对象结构。
  for (const key of keys.reverse()) patch = { [key]: patch }
  return patch
}

/**
 * 将校验后的部分更新合并到当前配置中，并返回更新后的新配置对象。
 * @param {ConfigDefinition} definition 配置结构定义。
 * @param {object} current 当前的完整配置对象。
 * @param {object} patch 已校验的部分配置更新对象。
 * @returns {object} 合并更新后的新配置对象副本。
 */
export function applyConfigPatch(definition, current, patch) {
  const next = copy(current)
  for (const [key, value] of Object.entries(patch)) {
    // 为什么用 ConfigItem 实例判断而不是看 value 是否为对象：
    // 像 bgm.audio 或 speech.sampling 这类本身是对象的配置项属于单个原子配置，更新时应当整体替换，只有配置分组才需要递归合并。
    next[key] =
      definition[key] instanceof ConfigItem
        ? copy(value)
        : applyConfigPatch(definition[key], current[key], value)
  }
  return next
}
