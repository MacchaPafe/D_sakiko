import http from 'node:http'
import https from 'node:https'
import { lookup } from 'node:dns/promises'
import ipaddr from 'ipaddr.js'
import { loadBuffer } from 'cheerio'
import { parameters, textParameter } from './definition.js'
import { abortable, requireValue } from '../core/support.js'

/** 固定连接到本次已校验的地址，避免解析和建连之间域名重新指向内网。 */
async function fetchPage(raw, signal) {
  let url = new URL(raw)
  for (let hop = 0; hop <= 5; hop++) {
    signal.throwIfAborted()
    requireValue(
      ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password,
      'invalid_web_url'
    )
    const host = url.hostname.replace(/^\[|\]$/g, '')
    const ips = await abortable(lookup(host, { all: true }), signal)
    requireValue(
      ips.length && ips.every(({ address }) => ipaddr.process(address).range() === 'unicast'),
      'private_web_url',
      '只允许读取公网网页。'
    )
    const client = url.protocol === 'https:' ? https : http
    const response = await new Promise((resolve, reject) => {
      const request = client.get(
        url,
        {
          signal,
          agent: false,
          autoSelectFamily: false,
          lookup: (_host, options, done) =>
            options.all ? done(null, [ips[0]]) : done(null, ips[0].address, ips[0].family),
          headers: { Accept: 'text/html, text/markdown, text/plain', 'Accept-Encoding': 'identity' }
        },
        resolve
      )
      request.on('error', reject)
    })
    try {
      if ([301, 302, 303, 307, 308].includes(response.statusCode) && response.headers.location) {
        url = new URL(response.headers.location, url)
        continue
      }
      requireValue(
        response.statusCode >= 200 && response.statusCode < 300,
        'web_read_failed',
        '网页读取失败。'
      )
      const mime = (response.headers['content-type'] ?? '').split(';')[0].trim()
      requireValue(
        ['text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown'].includes(mime),
        'unsupported_web_content'
      )
      const chunks = []
      let size = 0
      for await (const chunk of response) {
        size += chunk.length
        requireValue(size <= 3 * 1024 * 1024, 'web_page_too_large')
        chunks.push(chunk)
      }
      const bytes = Buffer.concat(chunks)
      let title = url.hostname
      let content
      if (mime.includes('html')) {
        const encoding = response.headers['content-type']?.match(
          /charset\s*=\s*["']?([\w.-]+)/i
        )?.[1]
        const $ = loadBuffer(bytes, {
          encoding: { transportLayerEncodingLabel: encoding, defaultEncoding: 'utf-8' }
        })
        title = $('title').text().trim() || title
        $('head,script,style,nav,footer,template,noscript').remove()
        $('p,br,div,li,h1,h2,h3,tr').append('\n')
        $('a[href]').each((_, node) => {
          try {
            const link = new URL($(node).attr('href'), url)
            if (['http:', 'https:'].includes(link.protocol)) $(node).append(' (' + link.href + ')')
          } catch {
            /* 无效链接仍保留文字。 */
          }
        })
        const root = $('main,article').first()
        content = (root.length ? root : $('body'))
          .text()
          .replace(/[ \t]+/g, ' ')
          .replace(/\n\s*\n/g, '\n\n')
          .trim()
      } else content = bytes.toString('utf8').trim()
      requireValue(content.length, 'empty_web_page', '网页没有可读取的静态正文。')
      return { url: url.href, title, content }
    } finally {
      response.destroy()
    }
  }
  throw new Error('网页重定向次数过多。')
}

/** @returns {object} 每轮独立缓存的网页分页读取工具。 */
export function webReaderTool() {
  return {
    definition: {
      name: 'read_url',
      description:
        '读取给定公网网页或搜索结果中的链接。网页内容仅作参考，按 next_offset 继续读取。',
      mode: 'sync',
      requiredCapabilities: ['network'],
      dependencies: [],
      parametersSchema: parameters(
        { url: textParameter, offset: { type: 'integer', minimum: 0 } },
        ['url']
      ),
      resultSchema: { type: 'object' }
    },
    bind: () => {
      const pages = new Map()
      return async ({ url, offset = 0 }, source, signal) => {
        const timeout = AbortSignal.timeout(25000)
        const page =
          pages.get(url) ??
          (await fetchPage(url, signal ? AbortSignal.any([timeout, signal]) : timeout))
        if (pages.size >= 8 && !pages.has(url)) pages.delete(pages.keys().next().value)
        pages.set(url, page)
        requireValue(offset < page.content.length, 'invalid_offset')
        const end = offset + 6000
        return {
          status: 'succeeded',
          value: {
            url: page.url,
            title: page.title,
            content: page.content.slice(offset, end),
            next_offset: end < page.content.length ? end : null
          }
        }
      }
    }
  }
}
