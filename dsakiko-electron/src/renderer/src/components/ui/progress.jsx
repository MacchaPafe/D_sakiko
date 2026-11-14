import * as ProgressPrimitive from '@radix-ui/react-progress'
import { cn } from '../../lib/utils.js'

/** @param {import('@radix-ui/react-progress').ProgressProps} props 下载百分比，未知时传 null。 @returns {import('react').ReactElement} shadcn/ui 进度条，不承担就绪判断。 */
export function Progress({ className, value, ...props }) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={value}
      className={cn('relative h-2 w-full overflow-hidden rounded-full bg-border', className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className="h-full w-full flex-1 bg-primary transition-transform motion-reduce:transition-none"
        style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}
