import validate from './schema-validator.cjs';

export const MAX_BODY = 1024 * 1024;
export const DAY = 86400;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const json = (value, status = 200) => Response.json(value, { status, headers: { 'Cache-Control': 'no-store', ...(status === 429 ? { 'Retry-After': '60' } : {}) } });
export const error = (status, code) => json({ ok: false, error: code }, status);
export async function sha256(value) {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
}
export async function headerFor(id) { return `v1:${id}:${await sha256(`d_sakiko.feedback/v1\n${id}`)}`; }
export async function checkHeader(request) {
  const value = request.headers.get('X-DSakiko-Feedback') || '';
  const parts = value.split(':');
  return parts.length === 3 && UUID.test(parts[1]) && value === await headerFor(parts[1]) ? parts[1] : null;
}
export function accepted(id, feedbackId = crypto.randomUUID()) {
  return json({ ok: true, request_id: id || crypto.randomUUID(), feedback_id: feedbackId });
}
export function validPayload(body) {
  if (!validate(body)) return false;
  if (body.rating === 'none' && !body.comment.trim()) return false;
  if (!body.worldbook_enabled && body.worldbook_diagnostics.length) return false;
  if (!body.conversation) return body.target === null && body.prompt_context === null && !body.worldbook_enabled && !body.worldbook_diagnostics.length;
  return body.prompt_context !== null && (body.target === null || body.target < body.conversation.messages.length);
}
export async function boundedBytes(stream, limit = MAX_BODY) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { void reader.cancel(); throw new RangeError('body_too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}
export async function gzip(bytes) {
  return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
}
export async function unpack(blob) {
  const bytes = await boundedBytes(new Blob([new Uint8Array(blob)]).stream().pipeThrough(new DecompressionStream('gzip')));
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}
export const clearContent = `state='withdrawn', payload=NULL, body_hash=NULL, raw_bytes=0, stored_bytes=0, rating=NULL, app_version=NULL, has_conversation=NULL, processed=0, processed_by=NULL, processed_at=NULL`;

export async function cleanup(db, now = Math.floor(Date.now() / 1000)) {
  await db.batch([
    db.prepare(`UPDATE feedback SET ${clearContent} WHERE state='active' AND expires_at<=?`).bind(now),
    db.prepare('DELETE FROM feedback WHERE control_expires_at<=?').bind(now),
    db.prepare('DELETE FROM daily_usage WHERE day<?').bind(new Date((now - 97 * DAY) * 1000).toISOString().slice(0, 10)),
  ]);
}
