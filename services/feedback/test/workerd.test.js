import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { unstable_splitSqlQuery } from 'wrangler';
import { generateKeyPair, exportJWK, SignJWT } from 'jose';
import { headerFor, DAY } from '../src/protocol.js';

test('real workerd + D1: schema, compression, atomic retries, quota rollback and withdrawal', async () => {
  const bundle = await build({ entryPoints: ['src/intake.js'], bundle: true, write: false, format: 'esm', platform: 'browser' });
  const mf = new Miniflare(convertV4MiniflareOptions({ workers: [{ name: 'intake', modules: true, script: bundle.outputFiles[0].text, compatibilityDate: '2026-09-01',
    d1Databases: { DB: 'feedback-test' }, bindings: { ACCEPTING: 'true', ENABLED_SCHEMAS: '1' },
    ratelimits: { RATE_LIMITER: { namespace_id: '1', simple: { limit: 100, period: 60 } } },
  }] }));
  try {
    const db = await mf.getD1Database('DB');
    // 使用 Wrangler 的 SQL 拆分器，避免自定义正则掩盖部署工具的解析问题。
    const migration = readFileSync('migrations/0001_feedback.sql', 'utf8');
    const statements = unstable_splitSqlQuery(migration);
    await db.batch(statements.map(sql => db.prepare(sql)));
    const id = crypto.randomUUID();
    const payload = { schema_version: 1, kind: 'feedback', request_id: id, created_at: Math.floor(Date.now() / 1000), app_version: 'workerd', rating: 'up', comment: '端到端', target: null, conversation: null, prompt_context: null, worldbook_enabled: false, worldbook_diagnostics: [], consent: 'feedback-v1-90d' };
    const headers = { 'Content-Type': 'application/json', 'X-DSakiko-Feedback': await headerFor(id), 'X-DSakiko-Control': 'a'.repeat(64) };
    const send = () => mf.dispatchFetch('https://example/v1/feedback', { method: 'POST', headers, body: JSON.stringify(payload) });
    const replies = await Promise.all(Array.from({ length: 4 }, send));
    assert.deepEqual(replies.map(r => r.status), [200, 200, 200, 200]);
    const receipts = await Promise.all(replies.map(r => r.json()));
    assert.equal(new Set(receipts.map(r => r.feedback_id)).size, 1);
    assert.equal((await db.prepare('SELECT count FROM daily_usage').first()).count, 1);
    const row = await db.prepare('SELECT expires_at,created_at,stored_bytes FROM feedback').first();
    assert.equal(row.expires_at - row.created_at, 90 * DAY); assert.ok(row.stored_bytes > 0);
    await db.prepare('UPDATE limits SET daily_count=1').run();
    payload.request_id = crypto.randomUUID();
    headers['X-DSakiko-Feedback'] = await headerFor(payload.request_id);
    assert.equal((await send()).status, 429);
    assert.equal((await db.prepare('SELECT count(*) AS n FROM feedback').first()).n, 1);
    headers['X-DSakiko-Feedback'] = await headerFor(id);
    assert.equal((await mf.dispatchFetch(`https://example/v1/feedback?request_id=${id}`, { method: 'DELETE', headers })).status, 200);
    assert.equal((await db.prepare('SELECT payload FROM feedback').first()).payload, null);
    assert.equal((await db.prepare('SELECT used_stored FROM limits').first()).used_stored, 0);
    payload.request_id = id;
    assert.equal((await send()).status, 409);
  } finally { await mf.dispose(); }
});

test('workerd Access JWT validates signature, issuer, audience, expiry and cross-site writes', async () => {
  const bundle = await build({ entryPoints: ['src/admin.js'], bundle: true, write: false, format: 'esm', platform: 'browser' });
  const { publicKey, privateKey } = await generateKeyPair('RS256');
  const jwk = { ...await exportJWK(publicKey), kid: 'test', alg: 'RS256' };
  const mf = new Miniflare(convertV4MiniflareOptions({ workers: [{ name: 'admin', modules: true, script: bundle.outputFiles[0].text, compatibilityDate: '2026-09-01',
    d1Databases: { DB: 'admin-test' }, bindings: { ACCESS_TEAM: 'test', ACCESS_AUD: 'feedback-admin' },
    outboundService: request => {
      assert.equal(request.url, 'https://test.cloudflareaccess.com/cdn-cgi/access/certs');
      return Response.json({ keys: [jwk] });
    },
  }] }));
  try {
    const now = Math.floor(Date.now() / 1000);
    const claims = { iss: 'https://test.cloudflareaccess.com', aud: 'feedback-admin', sub: 'developer', iat: now, exp: now + 3600 };
    const sign = values => new SignJWT(values).setProtectedHeader({ alg: 'RS256', kid: 'test' }).sign(privateKey);
    const fetch = (jwt, origin) => mf.dispatchFetch('https://admin/v1/admin/unknown', { headers: { 'Cf-Access-Jwt-Assertion': jwt, ...(origin ? { Origin: origin } : {}) } });
    const good = await sign(claims);
    assert.equal((await fetch(good)).status, 404);
    for (const invalid of [{ ...claims, aud: 'another-app' }, { ...claims, iss: 'https://attacker.example' }, { ...claims, exp: now - 10 }]) {
      assert.equal((await fetch(await sign(invalid))).status, 403);
    }
    const parts = good.split('.'); parts[2] = (parts[2][0] === 'a' ? 'b' : 'a') + parts[2].slice(1);
    assert.equal((await fetch(parts.join('.'))).status, 403);
    assert.equal((await fetch(good, 'https://attacker.example')).status, 403);
  } finally { await mf.dispose(); }
});
