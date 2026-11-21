import { LinkifyIt } from 'linkify-it'
import remarkGfm from 'remark-gfm'
import remarkParse from 'remark-parse'
import tlds from 'tlds' with { type: 'json' }
import { unified } from 'unified'

const linkifier = new LinkifyIt({ fuzzyLink: true, fuzzyEmail: false, tlds })
const brackets = new Map([
  ['（', '）'],
  ['(', ')'],
  ['[', ']'],
  ['【', '】']
])
const closing = new Set(brackets.values())
const command = /^(?:git|npm|pnpm|yarn|pip|uv|python[\d.]*|node|curl|docker|cd|ls|sudo)\s+\S/i
const processor = unified().use(remarkParse).use(remarkGfm).use(remarkDialogueLinks)
const recent = new Map()

/** @typedef {{text: string, audible: boolean, href?: string}} SubtitleRun 同一朗读状态和链接目标的一段可见字幕。 */

function textNode(value) {
  return { type: 'text', value }
}

function linkNode(value, url) {
  return { type: 'link', url, children: [textNode(value)] }
}

/** @param {string} value 链接目标。 @returns {string|null} 可交给宿主浏览器的绝对网页地址。 */
export function webHref(value) {
  if (!/^https?:\/\//i.test(value)) return null
  try {
    const url = new globalThis.URL(value)
    return url.hostname && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

function address(value) {
  const match = linkifier.match(value)?.[0]
  if (match?.raw === value) return match.url
  if (/^[a-z][a-z\d+.-]*:\/\/\S+$/i.test(value)) return value
  return null
}

function textLinks(value) {
  const nodes = []
  let start = 0
  for (const match of linkifier.match(value) ?? []) {
    if (match.index > start) nodes.push(textNode(value.slice(start, match.index)))
    nodes.push(linkNode(match.raw, match.url))
    start = match.lastIndex
  }
  if (start < value.length) nodes.push(textNode(value.slice(start)))
  return nodes
}

function visibleText(node) {
  return node.value ?? node.children?.map(visibleText).join('') ?? ''
}

function imageLabel(node) {
  return node.alt ? '查看图片：' + node.alt : '查看图片'
}

function linkLabel(node) {
  // 图片已经处于链接中时保留外层目标；避免嵌套链接和隐式图片请求。
  if (node.type === 'image' || node.type === 'imageReference')
    return { ...textNode(imageLabel(node)), imageDescription: true }
  if (node.children) node.children = node.children.map(linkLabel)
  return node
}

/** 历史和舞台共用链接识别，避免行内代码网址在两处有不同的点击行为。
 * @returns {Function} 处理 Markdown 语法树的 remark 插件转换函数。
 */
export function remarkDialogueLinks() {
  return (tree, file) => {
    const definitions = new Map()
    function collect(node) {
      if (node.type === 'definition' && !definitions.has(node.identifier))
        definitions.set(node.identifier, node)
      node.children?.forEach(collect)
    }
    collect(tree)
    function transform(node) {
      if (node.type === 'linkReference' || node.type === 'imageReference') {
        const target = definitions.get(node.identifier)
        if (target) node = { ...node, type: node.type.replace('Reference', ''), url: target.url }
      }
      if (node.type === 'image') {
        return { ...linkNode(imageLabel(node), node.url), imageDescription: true }
      }
      if (node.type === 'link') {
        // GFM 的裸链接会吞掉紧邻的中日文句号和后续台词；显式链接中的标点则属于作者指定的目标。
        const label = visibleText(node)
        const source = node.position
          ? String(file).slice(node.position.start.offset, node.position.end.offset)
          : label
        const boundary = label.search(/[。，、；！？「」『』（）]/u)
        if (source === label && boundary > 0) {
          const prefix = label.slice(0, boundary)
          const url = address(prefix)
          if (url) return [linkNode(prefix, url), ...textLinks(label.slice(boundary))]
        }
        node.namedLink = source.startsWith('[')
        return linkLabel(node)
      }
      if (node.type === 'inlineCode') {
        const url = address(node.value)
        if (url) return linkNode(node.value, url)
      }
      if (node.type === 'text') return textLinks(node.value)
      if (node.children) node.children = node.children.flatMap(transform)
      return node
    }
    transform(tree)
  }
}

function sourceSuffix(children) {
  let end = children.length
  // 只折叠句末括号内用域名标注的来源；“（看官方公告）”之类有名称的链接仍然可点。
  while (end >= 3) {
    const [before, link, after] = children.slice(end - 3, end)
    if (before.type !== 'text' || !link.namedLink || after.type !== 'text') break
    const open = before.value.trimEnd().at(-1)
    if (!['(', '（'].includes(open) || brackets.get(open) !== after.value.trim()) break
    const href = webHref(link.url)
    if (
      !href ||
      visibleText(link).toLowerCase() !== new globalThis.URL(href).hostname.toLowerCase()
    )
      break
    before.value = before.value.trimEnd().slice(0, -1).trimEnd()
    end -= 2
  }
  return children.slice(0, end)
}

function subtitleRuns(tree) {
  const runs = []
  function append(text, audible, href) {
    if (text) runs.push({ text, audible, href })
  }
  function visit(node, audible = true, href) {
    switch (node.type) {
      case 'definition':
      case 'footnoteDefinition':
      case 'footnoteReference':
      case 'html':
      case 'thematicBreak':
        return
      case 'break':
        return append('\n', audible)
      case 'text':
        if (href && audible && !node.imageDescription) {
          for (const part of textLinks(node.value)) {
            if (part.type === 'link') append('链接', false, href)
            else append(part.value, true, href)
          }
          return
        }
        return append(node.value, audible && !node.imageDescription, href)
      case 'inlineCode': {
        const technical = /(?:[a-z][\w+.-]*:\/\/|(?:^|\s)(?:[~.]?\/|[a-z]:\\)|\s--?\w)/i.test(
          node.value
        )
        return append(node.value, audible && !technical && !command.test(node.value), href)
      }
      case 'code':
        return append(node.value, false)
      case 'link': {
        const label = visibleText(node)
        const target = webHref(node.url)
        if (node.imageDescription) return append(label, false, target)
        if (address(label) || label === node.url) return append('链接', false, target)
        for (const child of node.children) visit(child, audible, target)
        return
      }
    }
    const children = node.type === 'paragraph' ? sourceSuffix(node.children) : (node.children ?? [])
    const block = ['root', 'list', 'listItem', 'blockquote', 'table', 'tableRow'].includes(
      node.type
    )
    let previous = false
    for (const child of children) {
      const offset = runs.length
      visit(child, audible && node.type !== 'table', href)
      if (runs.length === offset) continue
      if (block && previous)
        runs.splice(offset, 0, {
          text: node.type === 'tableRow' ? ' · ' : '\n',
          audible: audible && node.type !== 'table'
        })
      previous = true
    }
  }
  visit(tree)
  return runs
}

function markActions(runs) {
  // 代码块和表格里的字面括号不能跨块吞掉下一句真正的台词。
  const text = runs.map((run) => (run.audible ? run.text : ' '.repeat(run.text.length))).join('')
  const stack = []
  const ends = new Map()
  for (let i = 0; i < text.length; i++) {
    if (brackets.has(text[i])) stack.push(i)
    else if (closing.has(text[i])) {
      if (stack.length && brackets.get(text[stack.at(-1)]) === text[i]) ends.set(stack.pop(), i)
      else stack.length = 0
    }
  }
  const segments = []
  let offset = 0
  let actionEnd = -1
  for (const run of runs) {
    for (const char of run.text) {
      actionEnd = Math.max(actionEnd, ends.get(offset) ?? -1)
      const audible = run.audible && offset > actionEnd && !'「」『』'.includes(char)
      const last = segments.at(-1)
      if (last && last.audible === audible && last.href === run.href) last.text += char
      else segments.push({ text: char, audible, href: run.href })
      offset += char.length
    }
  }
  return segments
}

/** @param {string} source 存档中的 Markdown 台词。
 * @returns {{segments: SubtitleRun[], speech: string, count: number, visibleCount: number}} 舞台片段、朗读原稿、可高亮字数与无音频时用于估算阅读时长的可见字数。
 */
export function dialogueMarkdown(source = '') {
  if (recent.has(source)) return recent.get(source)
  const tree = processor.runSync(processor.parse(source), source)
  const segments = markActions(subtitleRuns(tree))
  // 网址前用于引出地址的冒号和空格仍显示，但不占用末尾的朗读时间。
  if (segments.at(-1)?.audible === false && segments.at(-1)?.href) {
    const index = segments.findLastIndex((run) => run.audible)
    const run = segments[index]
    const suffix = run?.text.match(/(?:[:：]\s*|\s+)$/u)?.[0]
    if (suffix) {
      run.text = run.text.slice(0, -suffix.length)
      segments.splice(index + 1, 0, { text: suffix, audible: false })
    }
  }
  let speech = segments
    .filter((run) => run.audible)
    .map((run) => run.text)
    .join('')
    .trim()
  if (!/[\p{L}\p{N}]/u.test(speech)) {
    speech = ''
    for (const run of segments) run.audible = false
  }
  const count = segments.reduce((total, run) => total + (run.audible ? [...run.text].length : 0), 0)
  const visibleCount = segments.reduce((total, run) => total + [...run.text].length, 0)
  const result = { segments, speech, count, visibleCount }
  // 播放器每帧读取字数，只缓存少量近期台词。
  if (recent.size >= 32) recent.delete(recent.keys().next().value)
  recent.set(source, result)
  return result
}
