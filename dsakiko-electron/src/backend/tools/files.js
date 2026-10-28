import path from 'node:path'
import { realpath, readdir, stat } from 'node:fs/promises'
import { isWithin } from '../resources/files.js'
import { requireValue } from '../core/support.js'
import { parameters, syncTool, textParameter } from './definition.js'
import { readTextFile } from './text-file.js'

const encodingParameter = {
  type: 'string',
  minLength: 1,
  maxLength: 64,
  description:
    '文本编码，默认 auto（BOM、UTF-8 校验或统计识别）；也可指定 gb18030、gbk、utf-16le、big5、shift_jis 等。推测编码可能不准确；乱码或识别失败时请明确指定，翻页时建议沿用上次返回的 encoding。'
}

/**
 * 文件工具统一执行读取策略，写入则由对话绑定回调强制落到 workspace。
 * @param {object} options 文件工具依赖。
 * @returns {object[]} 普通工具条目。
 */
export function fileTools({ settings, assets, write }) {
  async function resolve(chatId, filename) {
    const workspace = await assets.workspaceDirectory(chatId)
    const target = await realpath(path.resolve(workspace, filename || '.'))
    const policy = (await settings.get()).tools.files
    if (policy.readMode === 'roots' && !isWithin(workspace, target)) {
      const roots = await Promise.all(policy.readableRoots.map((root) => realpath(root)))
      requireValue(
        roots.some((root) => isWithin(root, target)),
        'file_read_forbidden',
        '文件不在允许读取的目录范围内。'
      )
    }
    return { target, policy }
  }
  async function read(chatId, input, signal) {
    const { target, policy } = await resolve(chatId, input.file_path)
    const decoded = await readTextFile(
      target,
      { maxBytes: policy.maxReadBytes, encoding: input.encoding },
      signal
    )
    return { target, ...decoded }
  }
  return [
    syncTool(
      {
        name: 'list_directory',
        description:
          '分页浏览目录中的文件与子目录列表（单页最多返回 100 项）；省略 directory 时默认浏览当前对话的 workspace。',
        requiredCapabilities: ['files'],
        parametersSchema: parameters({
          directory: {
            ...textParameter,
            description:
              '要浏览的目录路径（相对路径以当前对话 workspace 为基准）；省略时浏览当前对话 workspace 根目录。'
          },
          offset: {
            type: 'integer',
            minimum: 0,
            description:
              '分页起始偏移量（默认 0；若上次返回 nextOffset 非空，可传入该值继续翻页）。'
          }
        })
      },
      async ({ chatId }, input, source, signal) => {
        const { target } = await resolve(chatId, input.directory)
        signal?.throwIfAborted()
        requireValue((await stat(target)).isDirectory(), 'not_directory')
        const entries = (await readdir(target, { withFileTypes: true })).sort((a, b) =>
          a.name.localeCompare(b.name)
        )
        const offset = input.offset ?? 0
        return {
          directory: target,
          entries: entries.slice(offset, offset + 100).map((item) => {
            let kind = 'file'
            if (item.isDirectory()) kind = 'directory'
            if (item.isSymbolicLink()) kind = 'symlink'
            return { name: item.name, kind }
          }),
          nextOffset: offset + 100 < entries.length ? offset + 100 : null
        }
      }
    ),
    syncTool(
      {
        name: 'read_file_content',
        description:
          '按行分页尝试读取文本或代码文件，不限制扩展名，支持自动识别或指定编码（单次最多返回 200 行或 12000 字符；不解析 PDF、Word、PPT、Excel、ZIP 等文档或压缩格式）。相对路径以当前对话 workspace 为基准。',
        requiredCapabilities: ['files'],
        parametersSchema: parameters(
          {
            file_path: {
              ...textParameter,
              description: '要读取的文件路径（相对路径以当前对话 workspace 为基准）。'
            },
            encoding: encodingParameter,
            start_line: {
              type: 'integer',
              minimum: 1,
              description:
                '起始行号（从 1 开始计数，默认 1；翻页时直接传入上次返回的 next.start_line）。'
            },
            start_column: {
              type: 'integer',
              minimum: 0,
              description:
                '起始行内的字符列偏移（从 0 开始计数，默认 0；翻页时直接传入上次返回的 next.start_column）。'
            },
            end_line: {
              type: 'integer',
              minimum: 1,
              description: '结束行号（含该行，单次最多读取自 start_line 起的 200 行）。'
            }
          },
          ['file_path']
        )
      },
      async ({ chatId }, input, source, signal) => {
        const { target, text, encoding, encodingSource } = await read(chatId, input, signal)
        const lines = text.split('\n')
        const start = (input.start_line ?? 1) - 1
        let line = start
        let column = input.start_column ?? 0
        requireValue(start < lines.length && column <= lines[start].length, 'invalid_file_page')
        const end = Math.min(lines.length, input.end_line ?? start + 200, start + 200)
        requireValue(end > start, 'invalid_file_page')
        let content = ''
        while (line < end && content.length < 12000) {
          const remaining = lines[line].slice(column)
          const segment = remaining.slice(0, 12000 - content.length)
          content += segment
          column += segment.length
          if (column < lines[line].length) break
          line += 1
          column = 0
          if (line < end && content.length < 12000) content += '\n'
        }
        return {
          file: target,
          content,
          encoding,
          encodingSource,
          totalLines: lines.length,
          next: line < lines.length ? { start_line: line + 1, start_column: column } : null
        }
      }
    ),
    syncTool(
      {
        name: 'grep_search_in_file',
        description:
          '在单个文本文件内按字面关键词搜索匹配行，支持自动识别或指定编码（单次最多返回 30 条匹配及上下文片段；不支持正则表达式）。',
        requiredCapabilities: ['files'],
        parametersSchema: parameters(
          {
            file_path: {
              ...textParameter,
              description: '要搜索的文件路径（相对路径以当前对话 workspace 为基准）。'
            },
            query: {
              ...textParameter,
              description: '要查找的字面关键词（非正则表达式）。'
            },
            case_sensitive: {
              type: 'boolean',
              description: '是否区分大小写（默认 false）。'
            },
            encoding: encodingParameter,
            start_line: {
              type: 'integer',
              minimum: 1,
              description:
                '开始搜索的起始行号（从 1 开始计数，默认 1；若上次返回 nextLine 非空，可传入该值继续向后搜索）。'
            }
          },
          ['file_path', 'query']
        )
      },
      async ({ chatId }, input, source, signal) => {
        const { text, encoding, encodingSource } = await read(chatId, input, signal)
        const lines = text.split('\n')
        const query = input.case_sensitive ? input.query : input.query.toLowerCase()
        const matches = []
        let index = (input.start_line ?? 1) - 1
        for (; index < lines.length; index++) {
          signal?.throwIfAborted()
          const line = lines[index]
          const position = (input.case_sensitive ? line : line.toLowerCase()).indexOf(query)
          if (position >= 0)
            matches.push({
              line: index + 1,
              text: line.slice(Math.max(0, position - 100), position + 400)
            })
          if (matches.length === 30) {
            index++
            break
          }
        }
        return {
          matches,
          encoding,
          encodingSource,
          nextLine: index < lines.length ? index + 1 : null
        }
      }
    ),
    syncTool(
      {
        name: 'export_document',
        description:
          '在当前对话 workspace 中创建或覆盖 UTF-8 文本/代码文件，并生成供用户下载的资源引用。只能指定纯文件名；修改已有文件需设 overwrite=true。',
        requiredCapabilities: ['files'],
        parametersSchema: parameters(
          {
            filename: {
              ...textParameter,
              description: '要保存的文件名（如 "notes.md"，不得包含目录路径或斜杠）。'
            },
            content: {
              type: 'string',
              maxLength: 2000000,
              description: '要写入的完整 UTF-8 文本内容。'
            },
            overwrite: {
              type: 'boolean',
              description:
                '若同名工作文件已存在，是否允许覆盖（默认 false；修改已有文件时必须传 true）。'
            }
          },
          ['filename', 'content']
        )
      },
      ({ chatId }, input, source, signal) => write(chatId, input, source, signal)
    )
  ]
}
