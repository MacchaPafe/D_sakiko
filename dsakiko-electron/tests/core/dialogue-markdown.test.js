import { expect, test } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  dialogueMarkdown,
  remarkDialogueLinks,
  webHref
} from '../../src/shared/dialogue-markdown.js'

const video = 'https://youtu.be/03EkuyWOJ9g'
const source = 'https://bushiroad-music.com/music/?utm_source=openai'
const sentence =
  '日本時間で、2026年11月21日21時にプレミア公開、12月5日23時59分まで見られるよ。視聴ページはここね'
const reply = sentence + '： `' + video + '` ([bushiroad-music.com](' + source + '))'

function html(text) {
  return renderToStaticMarkup(
    createElement(
      Markdown,
      {
        remarkPlugins: [remarkGfm, remarkDialogueLinks],
        skipHtml: true
      },
      text
    )
  )
}

test('爱音的观看地址在历史里可点击，只有原文中的台词参与朗读和高亮', () => {
  const { segments, speech, count } = dialogueMarkdown(reply)
  expect(html(reply)).toContain('href="' + video + '"')
  expect(html(reply)).toContain('href="' + source + '"')
  expect(segments.filter((run) => run.href)).toEqual([
    { text: '链接', href: video, audible: false }
  ])
  expect(speech).toBe(sentence)
  expect(count).toBe(Array.from(sentence).length)
})

test('先解析引用式链接再过滤动作，保留命名链接、短术语和列表换行', () => {
  const text =
    '（轻轻**点头**）去看[**官方公告**][官方]。\n\n- `Live2D`\n- 「MyGO!!!!!」\n\n[官方]: https://example.org/event'
  const { segments, speech } = dialogueMarkdown(text)
  expect(speech).toBe('去看官方公告。\nLive2D\nMyGO!!!!!')
  expect(segments.find((run) => run.text === '官方公告')).toMatchObject({
    audible: true,
    href: 'https://example.org/event'
  })
  expect(segments.find((run) => run.text.includes('点头')).audible).toBe(false)
})

test('跳过代码块、表格、脚注和纯网址，不吞掉网址后的中日文台词', () => {
  expect(dialogueMarkdown('https://example.org/a。然后继续').speech).toBe('。然后继续')
  expect(dialogueMarkdown('看：www.example.com。下一句').speech).toContain('下一句')
  expect(dialogueMarkdown('看[音源 https://example.com/a](https://example.org)').speech).toBe(
    '看音源'
  )
  expect(dialogueMarkdown('（https://example.org）').segments.some((run) => run.href)).toBe(true)
  for (const text of [
    '`https://example.org`',
    'example.dev/archive',
    '`npm install react`',
    'https://example.org',
    '```js\nhello()\n```',
    '| 地点 | 时间 |\n|---|---|\n| 东京 | 九点 |',
    '[^1]\n\n[^1]: 资料来源'
  ]) {
    expect(dialogueMarkdown(text)).toMatchObject({ speech: '', count: 0 })
  }
  expect(dialogueMarkdown('[官网][a]\n\n[a]: https://example.com').speech).toBe('官网')
})

test('图片及嵌入链接中的图片只生成文字，不触发图片请求', () => {
  for (const text of [
    '![海报](https://example.org/poster.jpg)',
    '[![海报][图]](https://example.org/live)\n\n[图]: https://example.org/poster.jpg'
  ]) {
    const result = html(text)
    expect(result).toContain('查看图片：海报')
    expect(result).not.toMatch(/<(img|link)\b/i)
    expect(dialogueMarkdown(text).speech).toBe('')
  }
  for (const target of [
    'file:///a',
    '/local',
    'javascript:alert(1)',
    'https://alice@example.org'
  ]) {
    expect(webHref(target)).toBeNull()
  }
})
