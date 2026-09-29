import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./worker.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
function fixture(rows = []) {
  const calls = [];
  const env = {
    DOWNLOAD_TOKEN: "test-only-token",
    model_downloader_tabels: {
      prepare(sql) {
        const query = { sql, values: [] };
        calls.push(query);
        return {
          bind(...values) { query.values = values; return this; },
          async all() { return { success: true, results: rows }; },
        };
      },
    },
  };
  const request = (path, method = "GET", token = env.DOWNLOAD_TOKEN) => worker.fetch(
    new Request(`https://models.dsakiko.org${path}`, {
      method, headers: token ? { Authorization: `Bearer ${token}` } : {},
    }), env,
  );
  return { env, calls, request };
}

test("鉴权与方法检查先于任何数据库访问", async () => {
  const f = fixture();
  for (const path of ["/api/v3/models", "/api/images?kind=background"]) {
    for (const token of [null, "wrong"]) assert.equal((await f.request(path, "GET", token)).status, 401);
    for (const method of ["POST", "PUT", "DELETE"]) assert.equal((await f.request(path, method)).status, 405);
  }
  f.env.DOWNLOAD_TOKEN = "";
  assert.equal((await f.request("/api/v3/models")).status, 503);
  assert.equal(f.calls.length, 0);
});

test("模型筛选参数绑定、固定 SELECT 和空列表", async () => {
  const f = fixture([{ id: 1, model_id: "model", character_id: "tomori", name: null }]);
  const r = await f.request("/api/v3/models?character=tomori");
  assert.equal(r.status, 200);
  assert.equal((await r.json()).items[0].character_id, "tomori");
  assert.deepEqual(f.calls[0].values, ["tomori"]);
  assert.match(f.calls[0].sql, /^SELECT .* FROM models_v3 WHERE character_id = \? ORDER BY id$/);
  assert.equal(r.headers.get("Cache-Control"), "private, no-store");
  const empty = fixture();
  assert.deepEqual(await (await empty.request("/api/v3/models")).json(), { items: [] });
  assert.deepEqual(empty.calls[0].values, []);
});

test("背景与头像查询分别绑定类型及可选角色", async () => {
  const f = fixture();
  for (const path of ["?kind=background", "?kind=avatar&character=anon", "?kind=avatar"]) {
    assert.equal((await f.request("/api/images" + path)).status, 200);
  }
  assert.deepEqual(f.calls.map(c => c.values), [["background"], ["avatar", "anon"], ["avatar"]]);
  assert.match(f.calls[1].sql, /^SELECT .* FROM images WHERE kind = \? AND character_id = \? ORDER BY id$/);
});

test("错误参数和注入字符串不会触发查询", async () => {
  const f = fixture();
  for (const path of ["/api/images", "/api/images?kind=portrait", "/api/images?kind=background&character=anon", "/api/images?kind=avatar&kind=background", "/api/v3/models?character=", "/api/v3/models?character=a&character=b", "/api/v3/models?sql=DELETE", "/api/v3/models?character=" + encodeURIComponent("anon' OR 1=1 --")]) {
    assert.equal((await f.request(path)).status, 400, path);
  }
  assert.equal((await f.request("/api/unknown")).status, 404);
  assert.equal(f.calls.length, 0);
});

test("缺少绑定、异常及失败结果返回 503，不泄露数据库错误", async () => {
  const f = fixture();
  delete f.env.model_downloader_tabels;
  assert.equal((await f.request("/api/v3/models")).status, 503);
  for (const all of [async () => { throw new Error("secret database details"); }, async () => ({ success: false })]) {
    f.env.model_downloader_tabels = { prepare: () => ({ all }) };
    const r = await f.request("/api/v3/models");
    assert.equal(r.status, 503);
    assert.doesNotMatch(await r.text(), /secret|SELECT/);
  }
});

test("HEAD 目录请求没有响应体", async () => {
  const f = fixture([{ id: 1 }]);
  const r = await f.request("/api/v3/models", "HEAD");
  assert.equal(r.status, 200);
  assert.match(r.headers.get("Content-Type"), /application\/json/);
  assert.equal(await r.text(), "");
});

test("D1 未绑定时 R2 GET/HEAD 仍可用，未知前缀仍拒绝", async () => {
  const f = fixture();
  delete f.env.model_downloader_tabels;
  const seen = [];
  const object = { body: "zip", size: 3, httpEtag: '"test"', writeHttpMetadata() {} };
  f.env.MODELS_BUCKET = {
    async get(key) { seen.push(["GET", key]); return key.includes("missing") ? null : object; },
    async head(key) { seen.push(["HEAD", key]); return object; },
  };
  const zip = await f.request("/ournotes/tomori/model/r1.zip");
  assert.equal(zip.status, 200);
  assert.equal(await zip.text(), "zip");
  assert.equal(zip.headers.get("Content-Type"), "application/zip");
  const png = await f.request("/images/portraits/anon.png", "HEAD");
  assert.equal(png.status, 200);
  assert.equal(png.headers.get("Content-Type"), "image/png");
  assert.equal(await png.text(), "");
  assert.equal((await f.request("/ournotes/missing.zip")).status, 404);
  assert.equal((await f.request("/private/file.zip")).status, 404);
  assert.equal(seen.length, 3);
});
