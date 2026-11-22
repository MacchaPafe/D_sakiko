import * as SwitchPrimitive from '@radix-ui/react-switch'
import { cn } from '../../lib/utils.js'

/**
 * 基于 Radix UI 的拨动开关组件，支持受控状态、关联标签点击与键盘切换。
 * @param {import('@radix-ui/react-switch').SwitchProps} props 开关组件属性。
 * @returns {import('react').ReactElement} Radix 拨动开关元素。
 */
export function Switch({ className, ...props }) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border-0 bg-muted-foreground/35 p-0.5 shadow-xs outline-none transition-colors motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=checked]:bg-primary disabled:cursor-default disabled:opacity-50',
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none block size-4 rounded-full bg-background shadow-xs ring-0 transition-transform motion-reduce:transition-none data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
      />
    </SwitchPrimitive.Root>
  )
}
