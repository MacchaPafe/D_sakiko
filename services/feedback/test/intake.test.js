import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import intake from '../src/intake.js';
import admin, { handleAdmin } from '../src/admin.js';
import { cleanup, DAY, headerFor, MAX_BODY, unpack } from '../src/protocol.js';

// 使用真实 SQLite 事务/触发器模拟 D1 接口，不以 Map 模拟配额与幂等。
class Statement {
  constructor(db, sql, params = []) { Object.assign(this, { db, sql, params }); }
  bind(...params) { return new Statement(this.db, this.sql, params.map(x => x instanceof ArrayBuffer ? new Uint8Array(x) : x)); }
  execute() {
    const statement = this.db.prepare(this.sql);
    if (/^\s*SELECT/i.test(this.sql)) return { results: statement.all(...this.params), meta: { changes: 0 } };
    const result = statement.run(...this.params);
    return { results: [], meta: { changes: Number(result.changes) } };
  }
  async all() { return this.execute(); }
  async run() { return this.execute(); }
  async first() { return this.execute().results[0] || null; }
}
function environment() {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../migrations/0001_feedback.sql', import.meta.url), 'utf8'));
  return { sql: db, ACCEPTING: 'true', ENABLED_SCHEMAS: '1', RATE_LIMITER: { limit: async () => ({ success: true }) }, DB: {
    prepare: sql => new Statement(db, sql),
    batch: async statements => {
      db.exec('BEGIN');
      try { const result = statements.map(s => s.execute()); db.exec('COMMIT'); return result; }
      catch (e) { db.exec('ROLLBACK'); throw e; }
    },
  } };
}
const secret = 'a'.repeat(64);
function payload() {
  return { schema_version: 1, kind: 'feedback', request_id: crypto.randomUUID(), created_at: Math.floor(Date.now() / 1000), app_version: 'test', rating: 'none', comment: '测试反馈', target: null, conversation: null, prompt_context: null, worldbook_enabled: false, worldbook_diagnostics: [], consent: 'feedback-v1-90d' };
}
async function request(body, method = 'POST', token = secret) {
  const id = body.request_id;
  return new Request('https://feedback.example/v1/feedback' + (method === 'DELETE' ? `?request_id=${id}` : ''), {
    method, headers: { 'X-DSakiko-Feedback': await headerFor(id), 'X-DSakiko-Control': token, 'Content-Type': 'application/json' },
    ...(method === 'POST' ? { body: JSON.stringify(body) } : {}),
  });
}
test('missing/invalid header bypasses even body access, rate limiter and database', async () => {
  let touched = false;
  const req = { headers: new Headers(), get body() { touched = true; throw Error(); } };
  const res = await intake.fetch(req, {});
  assert.equal(res.status, 200); assert.equal(touched, false);
  req.headers.set('X-DSakiko-Feedback', 'bad');
  assert.equal((await intake.fetch(req, {})).status, 200);
});
test('gzip is lossless; parallel retries preserve receipt and consume one quota', async () => {
  const env = environment(); const body = payload();
  const results = await Promise.all(Array.from({ length: 12 }, async () => intake.fetch(await request(body), env)));
  assert.ok(results.every(r => r.status === 200));
  const receipts = await Promise.all(results.map(r => r.json()));
  assert.equal(new Set(receipts.map(r => r.feedback_id)).size, 1);
  assert.equal(env.sql.prepare('SELECT count(*) AS n FROM feedback').get().n, 1);
  assert.equal(env.sql.prepare('SELECT count FROM daily_usage').get().count, 1);
  const row = env.sql.prepare('SELECT payload FROM feedback').get();
  assert.deepEqual(await unpack(row.payload), body);
  assert.equal((await intake.fetch(await request({ ...body, comment: 'changed' }), env)).status, 409);
  assert.equal((await intake.fetch(await request(body, 'POST', 'b'.repeat(64)), env)).status, 403);
});
test('schema mismatch fakes success; supported shape with disabled version returns 400', async () => {
  const env = environment(); const body = payload();
  assert.equal((await intake.fetch(await request({ ...body, unknown: 'secret' }), env)).status, 200);
  assert.equal(env.sql.prepare('SELECT count(*) AS n FROM feedback').get().n, 0);
  env.ENABLED_SCHEMAS = '';
  assert.equal((await intake.fetch(await request(body), env)).status, 400);
  assert.equal((await intake.fetch(await request({ ...body, schema_version: 2 }), env)).status, 400);
  const malformed = await request(body);
  assert.equal((await intake.fetch(new Request(malformed, { body: '{' }), env)).status, 200);
});
test('bounded stream rejects oversized body without content length, but missing header wins', async () => {
  const env = environment(); const body = payload(); const req = await request(body);
  const oversized = new Request(req, { body: ' '.repeat(MAX_BODY + 1) });
  assert.equal((await intake.fetch(oversized, env)).status, 413);
  assert.equal((await intake.fetch(new Request('https://example', { method: 'POST', body: ' '.repeat(MAX_BODY + 1) }), env)).status, 200);
  env.RATE_LIMITER.limit = async () => ({ success: false });
  assert.equal((await intake.fetch(await request(body), env)).status, 429);
});
test('withdrawal before late upload creates tombstone; retries cannot resurrect it', async () => {
  const env = environment(); const body = payload();
  assert.equal((await intake.fetch(await request(body, 'DELETE'), env)).status, 200);
  assert.equal((await intake.fetch(await request(body), env)).status, 409);
  assert.equal((await intake.fetch(await request(body, 'DELETE'), env)).status, 200);
  const row = env.sql.prepare('SELECT payload,body_hash FROM feedback').get();
  assert.equal(row.payload, null); assert.equal(row.body_hash, null);
  assert.equal(env.sql.prepare('SELECT count FROM daily_usage').get().count, 0);
});
test('different submissions remain independent and withdrawal frees storage, not daily quota', async () => {
  const env = environment(); const a = payload(); const b = { ...a, request_id: crypto.randomUUID() };
  await intake.fetch(await request(a), env); await intake.fetch(await request(b), env);
  assert.equal((await intake.fetch(await request(a, 'DELETE', 'b'.repeat(64)), env)).status, 403);
  await intake.fetch(await request(a, 'DELETE'), env);
  assert.equal(env.sql.prepare("SELECT count(*) AS n FROM feedback WHERE state='active'").get().n, 1);
  assert.equal(env.sql.prepare('SELECT count FROM daily_usage').get().count, 2);
  assert.equal(env.sql.prepare('SELECT used_stored FROM limits').get().used_stored, env.sql.prepare('SELECT stored_bytes FROM feedback WHERE request_id=?').get(b.request_id).stored_bytes);
});
test('concurrent distinct requests obey exact count, byte and control quotas without partial writes', async () => {
  for (const limits of ['daily_count=2', 'daily_raw=900', 'daily_stored=300', 'total_stored=300', 'daily_controls=2']) {
    const env = environment(); env.sql.exec(`UPDATE limits SET ${limits}`);
    const results = await Promise.all(Array.from({ length: 10 }, async () => intake.fetch(await request(payload()), env)));
    assert.ok(results.some(r => r.status === 429), limits);
    const acceptedCount = results.filter(r => r.status === 200).length;
    assert.equal(env.sql.prepare('SELECT count(*) AS n FROM feedback').get().n, acceptedCount);
    assert.equal(env.sql.prepare('SELECT count FROM daily_usage').get()?.count || 0, acceptedCount);
    const accounting = env.sql.prepare('SELECT used_stored FROM limits').get().used_stored;
    assert.equal(accounting, env.sql.prepare('SELECT COALESCE(SUM(stored_bytes),0) AS n FROM feedback').get().n);
  }
});
test('expired content is immediately hidden and cleanup clears content and later controls', async () => {
  const env = environment(); const body = payload();
  const receipt = await (await intake.fetch(await request(body), env)).json();
  const now = Math.floor(Date.now() / 1000);
  env.sql.exec(`UPDATE feedback SET expires_at=${now - 1}`);
  assert.equal((await handleAdmin(new Request(`https://admin/v1/admin/feedback/${receipt.feedback_id}`), env, 'tester')).status, 404);
  await cleanup(env.DB, now);
  assert.equal(env.sql.prepare('SELECT payload FROM feedback').get().payload, null);
  assert.equal(env.sql.prepare('SELECT used_stored FROM limits').get().used_stored, 0);
  await cleanup(env.DB, now + 98 * DAY);
  assert.equal(env.sql.prepare('SELECT count(*) AS n FROM feedback').get().n, 0);
  body.created_at = now - 8 * DAY;
  assert.equal((await intake.fetch(await request(body), env)).status, 400);
});
test('management requires Access, never accepts intake header, and mutation preserves body', async () => {
  const env = environment(); const body = payload();
  const receipt = await (await intake.fetch(await request(body), env)).json();
  assert.equal((await admin.fetch(new Request('https://admin/v1/admin/feedback', { headers: { 'X-DSakiko-Feedback': await headerFor(body.request_id) } }), env)).status, 403);
  const path = `https://admin/v1/admin/feedback/${receipt.feedback_id}`;
  const before = env.sql.prepare('SELECT payload FROM feedback').get().payload;
  assert.equal((await handleAdmin(new Request(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: '{"processed":true}' }), env, 'tester')).status, 200);
  assert.deepEqual(env.sql.prepare('SELECT payload FROM feedback').get().payload, before);
  const list = await (await handleAdmin(new Request('https://admin/v1/admin/feedback?processed=1'), env, 'tester')).json();
  assert.equal(list.items.length, 1); assert.equal(list.items[0].payload, undefined);
  assert.equal((await handleAdmin(new Request(path, { method: 'DELETE' }), env, 'tester')).status, 200);
  assert.equal((await handleAdmin(new Request(path), env, 'tester')).status, 404);
});
test('database outage never produces a fake receipt for a valid submission', async () => {
  const env = environment(); env.DB.batch = async () => { throw Error('unavailable'); };
  assert.equal((await intake.fetch(await request(payload()), env)).status, 503);
});
