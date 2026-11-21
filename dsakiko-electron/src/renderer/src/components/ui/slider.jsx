import * as SliderPrimitive from '@radix-ui/react-slider'
import './slider.css'

/**
 * 基于 Radix UI 的单值滑块控件，将无障碍标签透传给实际承载 `role="slider"` 的滑块手柄。
 * @param {import('@radix-ui/react-slider').SliderProps} props 滑块取值、范围、步长与无障碍属性。
 * @returns {import('react').ReactElement} 支持键盘与指针拖动的滑块组件。
 */
export function Slider({
  className,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledby,
  'aria-valuetext': ariaValuetext,
  ...props
}) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      className={className ? 'ui-slider ' + className : 'ui-slider'}
      {...props}
    >
      <SliderPrimitive.Track data-slot="slider-track" className="ui-slider-track">
        <SliderPrimitive.Range data-slot="slider-range" className="ui-slider-range" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        data-slot="slider-thumb"
        className="ui-slider-thumb"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledby}
        aria-valuetext={ariaValuetext}
      />
    </SliderPrimitive.Root>
  )
}
