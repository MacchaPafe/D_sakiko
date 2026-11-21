import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  dialogueMarkdown,
  remarkDialogueLinks,
  webHref
} from '../../../../shared/dialogue-markdown.js'

const plugins = [remarkGfm, remarkDialogueLinks]

function openLink(event) {
  if (!window.dsakikoHost?.openWebSource) return
  event.preventDefault()
  void window.dsakikoHost.openWebSource(event.currentTarget.href)
}

/** @param {{href?: string, children: import('react').ReactNode}} props 网页地址和可见内容。
 * @returns {import('react').ReactNode} 经校验后由系统浏览器打开的链接。
 */
export function WebLink({ href, children }) {
  const target = webHref(href)
  if (!target) return children
  return (
    <a href={target} target="_blank" rel="noreferrer" title={target} onClick={openLink}>
      {children}
    </a>
  )
}
const components = { a: WebLink }

/** @param {{text: string}} props 存档原文。 @returns {import('react').ReactElement} 历史气泡正文。 */
export function MessageText({ text }) {
  return (
    <div className="message-text">
      <Markdown remarkPlugins={plugins} components={components} skipHtml>
        {text ?? ''}
      </Markdown>
    </div>
  )
}

/** @param {{text: string, revealed?: number}} props 原文与当前已朗读字数，译文不传进度。
 * @returns {import('react').ReactNode} 按朗读进度着色的字幕，链接保留键盘操作。
 */
export function SubtitleText({ text, revealed }) {
  const { segments } = dialogueMarkdown(text)
  let offset = 0
  return segments.map((run, index) => {
    let content = run.text
    if (run.audible && revealed !== undefined) {
      const start = offset
      const chars = Array.from(run.text)
      offset += chars.length
      content = chars.map((char, i) => (
        <span key={i} className={start + i < revealed ? 'spoken' : undefined}>
          {char}
        </span>
      ))
    }
    if (run.href)
      return (
        <WebLink key={index} href={run.href}>
          {content}
        </WebLink>
      )
    return content
  })
}
