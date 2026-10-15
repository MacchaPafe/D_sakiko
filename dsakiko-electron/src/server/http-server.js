import { Buffer } from 'node:buffer'
import { URL } from 'node:url'
import { setInterval, clearInterval } from 'node:timers'
import http from 'node:http'
import { randomBytes, timingSafeEqual } from 'node:crypto'
import { WebSocketServer } from 'ws'
import { IMPORT_PURPOSES } from '../shared/contracts/assets.js'
import { API_METHODS } from '../shared/contracts/api.js'
import { fingerprint, requireValue, failure, problem } from '../backend/core/support.js'

/**
 * @typedef {object} CommandRecord
 * @property {string} material 方法与参数的语义摘要；同身份不同材料拒绝。
 * @property {Promise<{value?: unknown, problem?: import('../shared/contracts/common.js').Problem}>} result 共享的在途或已完成结果，失败也被记录。
 * @property {number} createdAt 本次运行的登记时间；记录不自动过期，以免重试再次执行副作用。
 */
/** @typedef {{expiresAt: number, origin: string | null}} EventTicket 一次性 WebSocket 握手票据，绑定获取时的页面来源。 */
/** @typedef {{asset: import('../shared/contracts/common.js').AssetRef, expiresAt: number}} AssetGrant 仅允许读取一个资源的短期凭证。 */
/** @typedef {{dispose?: () => void}} ServerSubscription 先占位，再异步取得后端取消能力，防止订阅中途断线漏释放。 */

/** 长度相同后使用恒定时间比较；任何凭证内容都不进入错误说明。 */
function matches(secret, supplied) {
  const left = Buffer.from(secret)
  const right = Buffer.from(supplied ?? '')
  return left.length === right.length && timingSafeEqual(left, right)
}

/** 累计接收大小受限的 JSON，拒绝超大请求后才进行完整解析。 */
async function body(request) {
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    requireValue(size <= 8 * 1024 * 1024, 'request_too_large')
    chunks.push(chunk)
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw failure('invalid_json', '请求体不是合法 JSON。')
  }
}

/**
 * 启动仅监听本机的业务 HTTP/WS 服务；不导出任何任意方法调用或磁盘路径入口。
 * @param {import('../backend/create-core-backend.js').CoreBackend} backend 已装配的业务模块，内部能力不会自动暴露。
 * @param {{ port?: number, origins?: string[], token?: string }} [options] 明确允许的页面来源；端口缺省由系统分配。
 * @returns {Promise<{ baseUrl: string, token: string, close: () => Promise<void> }>} 可信宿主 bootstrap 与关闭能力。
 */
export async function startHttpServer(
  backend,
  { port = 0, origins = [], token = randomBytes(32).toString('base64url') } = {}
) {
  /** @type {Map<string, CommandRecord>} */
  const commands = new Map()
  /** @type {Map<string, EventTicket>} */
  const tickets = new Map()
  /** @type {Map<string, AssetGrant>} */
  const grants = new Map()
  const allowedOrigins = new Set(origins)
  const sockets = new Set()
  let baseUrl
  let closing = false
  const ws = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 })
  function originAllowed(request) {
    return !request.headers.origin || allowedOrigins.has(request.headers.origin)
  }
  function send(response, status, value, request) {
    const headers = {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff'
    }
    if (request.headers.origin && originAllowed(request)) {
      headers['Access-Control-Allow-Origin'] = request.headers.origin
      headers.Vary = 'Origin'
    }
    response.writeHead(status, headers)
    response.end(JSON.stringify(value))
  }
  /** 同时核对来源、Host 和凭证；监听回环地址本身不等于允许任意网页访问。 */
  function authorize(request) {
    requireValue(originAllowed(request), 'forbidden_origin')
    requireValue(request.headers.host === new URL(baseUrl).host, 'invalid_host')
    requireValue(matches('Bearer ' + token, request.headers.authorization), 'unauthorized')
  }
  /** 仅分派已由路由白名单核准的方法；资源上传将传输数据转换为后端材料。 */
  async function dispatch(namespace, method, args) {
    if (namespace === 'assets' && method === 'upload') {
      const [source] = args
      requireValue(
        source &&
          typeof source.base64 === 'string' &&
          typeof source.name === 'string' &&
          typeof source.mediaType === 'string' &&
          IMPORT_PURPOSES.includes(source.purpose)
      )
      return backend.assets.importAsset(
        {
          kind: 'bytes',
          name: source.name,
          mediaType: source.mediaType,
          bytes: Buffer.from(source.base64, 'base64')
        },
        'upload:' + randomBytes(16).toString('hex'),
        source.purpose
      )
    }
    if (namespace === 'assets' && method === 'access') {
      const [asset] = args
      await backend.assets.inspect(asset)
      const ticket = randomBytes(24).toString('base64url')
      const expiresAt = Date.now() + 5 * 60 * 1000
      grants.set(ticket, { asset, expiresAt })
      return {
        url: baseUrl + '/api/v1/assets/' + ticket,
        expiresAt: new Date(expiresAt).toISOString()
      }
    }
    return backend[namespace][method](...args)
  }
  const server = http.createServer(async (request, response) => {
    try {
      requireValue(!closing, 'backend_closed')
      const url = new URL(request.url, baseUrl)
      requireValue(
        request.headers.host === new URL(baseUrl).host && originAllowed(request),
        'forbidden_origin'
      )
      if (request.method === 'OPTIONS') {
        response.writeHead(204, {
          'Access-Control-Allow-Origin': request.headers.origin ?? '',
          'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
          'Access-Control-Allow-Headers': 'Authorization, Content-Type',
          Vary: 'Origin'
        })
        response.end()
        return
      }
      // 浏览器 Audio 无法附加 Bearer 请求头；资源 URL 只授予一个资源的短期读取权。
      if (request.method === 'GET' && url.pathname.startsWith('/api/v1/assets/')) {
        const grant = grants.get(url.pathname.slice('/api/v1/assets/'.length))
        requireValue(grant && grant.expiresAt > Date.now(), 'asset_access_expired')
        const info = await backend.assets.inspect(grant.asset)
        const bytes = await backend.assets.read(
          grant.asset,
          url.searchParams.get('file') ?? undefined
        )
        const headers = {
          'Content-Type': info.mediaType,
          'Cache-Control': 'private, no-store',
          'X-Content-Type-Options': 'nosniff',
          'Accept-Ranges': 'bytes'
        }
        if (request.headers.origin) headers['Access-Control-Allow-Origin'] = request.headers.origin
        const range = request.headers.range?.match(/^bytes=(\d+)-(\d*)$/)
        if (range) {
          const start = Number(range[1])
          const end = range[2] ? Number(range[2]) : bytes.length - 1
          requireValue(start <= end && end < bytes.length, 'invalid_range')
          response.writeHead(206, {
            ...headers,
            'Content-Length': end - start + 1,
            'Content-Range': `bytes ${start}-${end}/${bytes.length}`
          })
          response.end(bytes.subarray(start, end + 1))
        } else {
          response.writeHead(200, { ...headers, 'Content-Length': bytes.length })
          response.end(bytes)
        }
        return
      }
      authorize(request)
      requireValue(request.method === 'POST', 'method_not_allowed')
      // 浏览器 WebSocket 也不能设置鉴权头，使用短期单次票据，避免把长期 token 放进 URL。
      if (url.pathname === '/api/v1/events-ticket') {
        const ticket = randomBytes(24).toString('base64url')
        tickets.set(ticket, {
          expiresAt: Date.now() + 15000,
          origin: request.headers.origin ?? null
        })
        send(
          response,
          200,
          { value: { url: baseUrl.replace(/^http/, 'ws') + '/api/v1/events?ticket=' + ticket } },
          request
        )
        return
      }
      const route = url.pathname.match(/^\/api\/v1\/call\/([a-z]+)\/([A-Za-z]+)$/)
      requireValue(
        route &&
          Object.hasOwn(API_METHODS, route[1]) &&
          Object.hasOwn(API_METHODS[route[1]], route[2]),
        'method_not_found'
      )
      const [, namespace, method] = route
      const command = await body(request)
      requireValue(Array.isArray(command.args) && command.args.length <= 8)
      // 所有接口参数只能是 JSON。AbortSignal、宿主权限、回调和本机路径不从网络注入。
      requireValue(!JSON.stringify(command.args).includes('"__proto__"'), 'invalid_input')
      let pending
      if (API_METHODS[namespace][method]) {
        requireValue(
          typeof command.requestId === 'string' &&
            command.requestId.length > 0 &&
            command.requestId.length <= 200
        )
        // 在启动副作用前登记共享 Promise，并发重试共同等待同一结果。容量满后拒绝，不淘汰旧身份。
        const material = fingerprint({ namespace, method, args: command.args })
        const previous = commands.get(command.requestId)
        if (previous) {
          requireValue(previous.material === material, 'request_conflict')
          pending = previous.result
        } else {
          requireValue(
            commands.size < 10000,
            'command_capacity',
            '命令去重记录达到本次运行上限，请重启服务。'
          )
          pending = Promise.resolve()
            .then(() => dispatch(namespace, method, command.args))
            .then(
              (value) => ({ value: value ?? null }),
              (error) => ({ problem: problem(error) })
            )
          commands.set(command.requestId, { material, result: pending, createdAt: Date.now() })
        }
      } else
        pending = Promise.resolve()
          .then(() => dispatch(namespace, method, command.args))
          .then((value) => ({ value: value ?? null }))
      const result = await pending
      send(response, result.problem ? 409 : 200, result, request)
    } catch (error) {
      if (!response.headersSent) send(response, 400, { problem: problem(error) }, request)
      else response.destroy()
    }
  })
  server.on('connection', (socket) => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
  })
  server.on('upgrade', (request, socket, head) => {
    try {
      const url = new URL(request.url, baseUrl)
      const ticket = url.searchParams.get('ticket')
      const entry = tickets.get(ticket)
      requireValue(
        !closing &&
          url.pathname === '/api/v1/events' &&
          request.headers.host === new URL(baseUrl).host &&
          originAllowed(request)
      )
      requireValue(
        entry && entry.expiresAt > Date.now() && entry.origin === (request.headers.origin ?? null),
        'unauthorized'
      )
      tickets.delete(ticket)
      ws.handleUpgrade(request, socket, head, (client) => ws.emit('connection', client))
    } catch {
      socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n')
      socket.destroy()
    }
  })
  ws.on('connection', (client) => {
    /** @type {Map<string, ServerSubscription>} */
    const subscriptions = new Map()
    function deliver(value) {
      if (client.readyState !== 1) return
      // 慢客户端断开后重取完整快照；不无限积压事件，也不让客户端背压阻塞业务。
      if (client.bufferedAmount > 2 * 1024 * 1024) {
        client.close(1013, 'snapshot required')
        return
      }
      client.send(JSON.stringify(value))
    }
    client.on('message', async (raw) => {
      try {
        const command = JSON.parse(raw.toString())
        requireValue(typeof command.id === 'string' && command.id.length <= 100)
        if (command.kind === 'unsubscribe') {
          const previous = subscriptions.get(command.id)
          subscriptions.delete(command.id)
          previous?.dispose?.()
          return
        }
        requireValue(
          command.kind === 'subscribe' && !subscriptions.has(command.id) && subscriptions.size < 64
        )
        // observe 可能需要读盘；先登记占位，退订或断线才能让迟到的订阅立即释放。
        const registration = {}
        subscriptions.set(command.id, registration)
        const listener = (snapshot) => deliver({ kind: 'snapshot', id: command.id, snapshot })
        let dispose
        if (command.topic === 'conversation')
          dispose = await backend.conversations.observe(command.key, listener)
        else if (command.topic === 'performance')
          dispose = await backend.performance.observe(command.key, listener)
        else if (command.topic === 'settings') dispose = await backend.settings.observe(listener)
        else if (command.topic === 'interactions')
          dispose = await backend.interactions.observe(listener)
        else throw failure('topic_unavailable', '订阅主题不存在。')
        if (client.readyState !== 1 || subscriptions.get(command.id) !== registration) dispose()
        else registration.dispose = dispose
      } catch (error) {
        deliver({ kind: 'problem', problem: problem(error) })
      }
    })
    client.on('error', () => {})
    client.on('close', () => {
      for (const item of subscriptions.values()) item.dispose?.()
      subscriptions.clear()
    })
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, '127.0.0.1', resolve)
  })
  baseUrl = 'http://127.0.0.1:' + server.address().port
  const cleanup = setInterval(() => {
    for (const map of [tickets, grants])
      for (const [key, entry] of map) if (entry.expiresAt <= Date.now()) map.delete(key)
  }, 30000)
  cleanup.unref()
  return {
    baseUrl,
    token,
    /** 停止接收命令并释放 HTTP/WS 连接；业务模块由宿主另外关闭。
     * @returns {Promise<void>} 监听已停止，传输资源已释放。
     */
    async close() {
      closing = true
      clearInterval(cleanup)
      for (const client of ws.clients) client.terminate()
      ws.close()
      server.closeAllConnections()
      await new Promise((resolve) => server.close(resolve))
      for (const socket of sockets) socket.destroy()
    }
  }
}
