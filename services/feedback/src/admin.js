import { createRemoteJWKSet, jwtVerify } from 'jose';
import { boundedBytes, clearContent, error, json, unpack } from './protocol.js';

const keySets = new Map();
export async function authenticate(request, env) {
  if (!/^[a-z0-9-]+$/.test(env.ACCESS_TEAM || '') || !env.ACCESS_AUD) throw new Error('access_not_configured');
  const issuer = `https://${env.ACCESS_TEAM}.cloudflareaccess.com`;
  let keys = keySets.get(issuer);
  if (!keys) { keys = createRemoteJWKSet(new URL(`${issuer}/cdn-cgi/access/certs`)); keySets.set(issuer, keys); }
  const assertion = request.headers.get('Cf-Access-Jwt-Assertion');
  if (!assertion) throw new Error('unauthorized');
  const { payload } = await jwtVerify(assertion, keys, { issuer, audience: env.ACCESS_AUD, algorithms: ['RS256'], requiredClaims: ['exp', 'iat', 'sub'] });
  return String(payload.sub);
}

// 认证外壳和纯业务处理分开，后者只从已认证入口调用。
export async function handleAdmin(request, env, principal) {
  const url = new URL(request.url);
  const now = Math.floor(Date.now() / 1000);
  if (request.method === 'GET' && url.pathname === '/v1/admin/feedback') {
    const where = ["state='active'", 'expires_at>?'];
    const params = [now];
    for (const [key, column, values] of [['rating', 'rating', ['up', 'down', 'none']], ['processed', 'processed', ['0', '1']]]) {
      const value = url.searchParams.get(key);
      if (value !== null) {
        if (!values.includes(value)) return error(400, 'invalid_filter');
        where.push(`${column}=?`); params.push(value);
      }
    }
    for (const [key, op] of [['since', '>='], ['until', '<=']]) {
      const value = url.searchParams.get(key);
      if (value !== null) {
        if (!/^\d{1,12}$/.test(value)) return error(400, 'invalid_filter');
        where.push(`created_at${op}?`); params.push(Number(value));
      }
    }
    const cursor = url.searchParams.get('cursor');
    if (cursor) {
      const match = /^(\d{1,12})_([0-9a-f-]{36})$/.exec(cursor);
      if (!match) return error(400, 'invalid_cursor');
      where.push('(created_at<? OR (created_at=? AND feedback_id<?))');
      params.push(Number(match[1]), Number(match[1]), match[2]);
    }
    const { results } = await env.DB.prepare(`SELECT feedback_id,created_at,expires_at,rating,app_version,has_conversation,processed,processed_at,stored_bytes FROM feedback WHERE ${where.join(' AND ')} ORDER BY created_at DESC,feedback_id DESC LIMIT 51`).bind(...params).all();
    const items = results.slice(0, 50);
    const last = items.at(-1);
    return json({ items, cursor: results.length > 50 ? `${last.created_at}_${last.feedback_id}` : null });
  }
  const match = /^\/v1\/admin\/feedback\/([0-9a-f-]{36})$/.exec(url.pathname);
  if (!match) return error(404, 'not_found');
  const id = match[1];
  if (request.method === 'GET') {
    const row = await env.DB.prepare("SELECT payload,expires_at,processed FROM feedback WHERE feedback_id=? AND state='active' AND expires_at>?").bind(id, now).first();
    if (!row) return error(404, 'not_found');
    return json({ feedback_id: id, expires_at: row.expires_at, processed: row.processed, payload: await unpack(row.payload) });
  }
  if (request.method === 'PATCH') {
    if (request.headers.get('Content-Type') !== 'application/json') return error(415, 'json_required');
    let body;
    try { body = JSON.parse(new TextDecoder().decode(await boundedBytes(request.body, 1024))); } catch { return error(400, 'invalid_body'); }
    if (!body || Object.keys(body).length !== 1 || typeof body.processed !== 'boolean') return error(400, 'invalid_body');
    const result = await env.DB.prepare("UPDATE feedback SET processed=?,processed_by=?,processed_at=? WHERE feedback_id=? AND state='active' AND expires_at>?")
      .bind(body.processed ? 1 : 0, principal, now, id, now).run();
    return result.meta.changes ? json({ ok: true }) : error(404, 'not_found');
  }
  if (request.method === 'DELETE') {
    await env.DB.prepare(`UPDATE feedback SET ${clearContent} WHERE feedback_id=?`).bind(id).run();
    return json({ ok: true });
  }
  return error(405, 'method_not_allowed');
}

export default {
  async fetch(request, env) {
    let principal;
    try { principal = await authenticate(request, env); } catch { return error(403, 'forbidden'); }
    // Access Cookie 不足以授权跨站修改；不提供跨域 CORS 放行。
    const origin = request.headers.get('Origin');
    if (origin && origin !== new URL(request.url).origin) return error(403, 'forbidden');
    try { return await handleAdmin(request, env, principal); }
    catch { return error(503, 'unavailable'); }
  },
};
