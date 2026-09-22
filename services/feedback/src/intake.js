import { accepted, boundedBytes, checkHeader, cleanup, clearContent, DAY, error, gzip, sha256, UUID, validPayload } from './protocol.js';

async function handle(request, env) {
  // 必须先于路径、正文、限流和数据库访问；该约定并非认证。
  const id = await checkHeader(request);
  if (!id) return accepted(null);
  const url = new URL(request.url);
  if (url.pathname !== '/v1/feedback' || !['POST', 'DELETE'].includes(request.method)) return error(404, 'not_found');
  if (!env.RATE_LIMITER) return error(503, 'unavailable');
  const limited = await env.RATE_LIMITER.limit({ key: `${request.method}:${request.headers.get('CF-Connecting-IP') || 'unknown'}` });
  if (!limited.success) return error(429, 'rate_limited');
  const token = request.headers.get('X-DSakiko-Control') || '';
  if (!/^[0-9a-f]{64}$/.test(token)) return accepted(id);
  const tokenHash = await sha256(token);
  const now = Math.floor(Date.now() / 1000);
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  if (request.method === 'DELETE') {
    if (url.searchParams.get('request_id') !== id) return accepted(id);
    // batch 在 D1 中原子执行；未知请求也创建有界墓碑，阻止迟到上传。
    const results = await env.DB.batch([
      env.DB.prepare(`INSERT INTO feedback(request_id,feedback_id,token_hash,created_at,expires_at,control_expires_at,day,state)
        SELECT ?,?,?,?,?,?,?,'withdrawn' WHERE NOT EXISTS (SELECT 1 FROM feedback WHERE request_id=?)`)
        .bind(id, crypto.randomUUID(), tokenHash, now, now, now + 8 * DAY, day, id),
      env.DB.prepare(`UPDATE feedback SET ${clearContent} WHERE request_id=? AND token_hash=?`).bind(id, tokenHash),
      env.DB.prepare('SELECT token_hash,feedback_id FROM feedback WHERE request_id=?').bind(id),
    ]);
    const row = results[2].results[0];
    return row?.token_hash === tokenHash ? accepted(id, row.feedback_id) : error(403, 'forbidden');
  }
  if (env.ACCEPTING !== 'true') return error(503, 'paused');
  if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json' || request.headers.has('Content-Encoding')) return accepted(id);
  let bytes;
  try { bytes = await boundedBytes(request.body); }
  catch (e) { if (e instanceof RangeError) return error(413, 'body_too_large'); throw e; }
  let body;
  try { body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { return accepted(id); }
  if (body?.schema_version !== 1) {
    // 未知版本只检查公共信封；不声称知道其业务字段。
    return Number.isInteger(body?.schema_version) && body.schema_version > 0 && body.kind === 'feedback' && UUID.test(body.request_id) && body.request_id === id
      ? error(400, 'unsupported_schema_version') : accepted(id);
  }
  if (!validPayload(body) || body.request_id !== id) return accepted(id);
  if (env.ENABLED_SCHEMAS !== '1') return error(400, 'unsupported_schema_version');
  // 请求最多可首次创建/重试七天；墓碑保留更久，不能过期后复活旧请求。
  if (body.created_at < now - 7 * DAY || body.created_at > now + 300) return error(400, 'request_expired');
  const bodyHash = await sha256(bytes);
  const compressed = await gzip(bytes);
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT INTO feedback(request_id,feedback_id,token_hash,body_hash,payload,raw_bytes,stored_bytes,created_at,expires_at,control_expires_at,day,rating,app_version,has_conversation,state)
      SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active' WHERE NOT EXISTS (SELECT 1 FROM feedback WHERE request_id=?)`)
      .bind(id, crypto.randomUUID(), tokenHash, bodyHash, compressed.buffer, bytes.length, compressed.length, now, now + 90 * DAY, now + 97 * DAY, day, body.rating, body.app_version, body.conversation ? 1 : 0, id),
    env.DB.prepare('SELECT feedback_id,token_hash,body_hash,state FROM feedback WHERE request_id=?').bind(id),
  ]);
  const row = results[1].results[0];
  if (!row) return error(503, 'unavailable');
  if (row.token_hash !== tokenHash) return error(403, 'forbidden');
  if (row.state !== 'active' || row.body_hash !== bodyHash) return error(409, 'request_conflict');
  return accepted(id, row.feedback_id);
}

export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch (e) {
      // 不记录异常正文、令牌或原始请求；数据库故障绝不能假成功。
      return String(e?.message).includes('feedback_quota') || String(e?.message).includes('feedback_control_quota')
        ? error(429, 'quota_exceeded') : error(503, 'unavailable');
    }
  },
  async scheduled(_event, env) { await cleanup(env.DB); },
};
