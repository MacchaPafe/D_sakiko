import { cn } from '@/lib/utils'

/**
 * 表单输入沿用 shadcn 的令牌、焦点和禁用约定。
 * @param {object} props 标准输入属性。
 * @returns {import('react').ReactElement} 输入控件。
 */
export function Input({ className, ...props }) {
  return <input className={cn('field-input', className)} {...props} />
}

/**
 * 多行输入使用与单行输入一致的语义样式。
 * @param {object} props 标准多行输入属性。
 * @returns {import('react').ReactElement} 多行输入控件。
 */
export function Textarea({ className, ...props }) {
  return <textarea className={cn('field-input field-textarea', className)} {...props} />
}
