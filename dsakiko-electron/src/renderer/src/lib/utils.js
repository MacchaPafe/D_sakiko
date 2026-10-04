import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并条件类名，并解决 Tailwind 工具类冲突。
 * @param {...import('clsx').ClassValue} inputs 待合并的类名。
 * @returns {string} 可用于元素的类名。
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
