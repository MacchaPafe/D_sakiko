import { List } from '@radix-ui/react-tabs'
import './segmented-tabs.css'

export { Root as Tabs, Content as TabPanel, Trigger as Tab } from '@radix-ui/react-tabs'

/** @param {import('@radix-ui/react-tabs').TabsListProps} props 页签列表及其布局类。 @returns {import('react').ReactElement} 共享样式的 Radix 页签列表。 */
export function TabList({ className, ...props }) {
  return (
    <List className={className ? 'segmented-tabs ' + className : 'segmented-tabs'} {...props} />
  )
}
