// Worker: dsakiko-model-download
// R2 绑定：MODELS_BUCKET -> bangdream-v3-models
// D1 绑定：model_downloader_tabels -> dsakiko-resources（与控制台名称一致）
// Secret：DOWNLOAD_TOKEN（在 Cloudflare 配置，不写入源码）
// 资源下载与只读目录接口共用 DOWNLOAD_TOKEN。
const RESOURCE_PREFIXES = ["ournotes/", "images/"];

function reply(request, status, message, extraHeaders = {}) {
  return new Response(request.method === "HEAD" ? null : message, {
    status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

function jsonReply(request, status, data) {
  return new Response(request.method === "HEAD" ? null : JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

async function catalog(request, env, url) {
  const models = url.pathname === "/api/v3/models";
  if (!models && url.pathname !== "/api/images") {
    return jsonReply(request, 404, { error: "Not found" });
  }
  const params = url.searchParams;
  const permitted = models ? ["character"] : ["character", "kind"];
  for (const key of params.keys()) {
    if (!permitted.includes(key) || params.getAll(key).length !== 1) {
      return jsonReply(request, 400, { error: "Invalid query parameters" });
    }
  }
  const character = params.get("character");
  if (character !== null && !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(character)) {
    return jsonReply(request, 400, { error: "Invalid character" });
  }
  const kind = params.get("kind");
  if (!models && !["background", "avatar"].includes(kind)) {
    return jsonReply(request, 400, { error: "kind must be background or avatar" });
  }
  if (!models && kind === "background" && character !== null) {
    return jsonReply(request, 400, { error: "Backgrounds do not have a character" });
  }
  if (!env.model_downloader_tabels) {
    return jsonReply(request, 503, { error: "Catalog unavailable" });
  }
  try {
    // SQL 结构由服务端固定；客户端只提供绑定值，不接受 SQL 输入。
    let sql;
    const values = [];
    if (models) {
      sql = "SELECT id, model_id, character_id, name, r2_key, sha256 FROM models_v3";
      if (character !== null) {
        sql += " WHERE character_id = ?";
        values.push(character);
      }
    } else {
      sql = "SELECT id, kind, character_id, name, r2_key FROM images WHERE kind = ?";
      values.push(kind);
      if (character !== null) {
        sql += " AND character_id = ?";
        values.push(character);
      }
    }
    sql += " ORDER BY id";
    let statement = env.model_downloader_tabels.prepare(sql);
    if (values.length) statement = statement.bind(...values);
    const result = await statement.all();
    if (!result.success || !Array.isArray(result.results)) {
      throw new Error("Catalog query failed");
    }
    return jsonReply(request, 200, { items: result.results });
  } catch {
    // 不向客户端泄露 SQL、数据库错误或内部元数据。
    return jsonReply(request, 503, { error: "Catalog temporarily unavailable" });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.protocol !== "https:") {
      return reply(request, 403, "HTTPS required");
    }

    // 未配置凭证时拒绝访问，避免意外开放。
    if (!env.DOWNLOAD_TOKEN) {
      return reply(request, 503, "Download service unavailable");
    }

    const expected = `Bearer ${env.DOWNLOAD_TOKEN}`;
    if (request.headers.get("Authorization") !== expected) {
      return reply(request, 401, "Unauthorized", {
        "WWW-Authenticate": 'Bearer realm="dsakiko-models"',
      });
    }

    if (!["GET", "HEAD"].includes(request.method)) {
      return reply(request, 405, "Method not allowed", {
        Allow: "GET, HEAD",
      });
    }

    if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
      return catalog(request, env, url);
    }

    let key;
    try {
      key = decodeURIComponent(url.pathname.slice(1));
    } catch {
      return reply(request, 400, "Invalid path");
    }

    // 只提供实际发布目录，不提供对象列表或写入接口。
    const invalidPath =
      key.includes("\\") ||
      /[\u0000-\u001f\u007f]/.test(key) ||
      key.split("/").some(
        (segment) => !segment || segment === "." || segment === ".."
      );
    const allowed = RESOURCE_PREFIXES.some((prefix) => key.startsWith(prefix));

    if (invalidPath || !allowed) {
      return reply(request, 404, "Not found");
    }

    try {
      const object =
        request.method === "HEAD"
          ? await env.MODELS_BUCKET.head(key)
          : await env.MODELS_BUCKET.get(key);

      if (!object) {
        return reply(request, 404, "Not found");
      }

      const headers = new Headers();
      object.writeHttpMetadata(headers);

      // 覆盖可能由上传工具推断错误的类型。
      if (key.endsWith(".json")) {
        headers.set("Content-Type", "application/json; charset=utf-8");
      } else if (key.endsWith(".zip")) {
        headers.set("Content-Type", "application/zip");
      } else if (key.endsWith(".png")) {
        headers.set("Content-Type", "image/png");
      } else if (!headers.has("Content-Type")) {
        headers.set("Content-Type", "application/octet-stream");
      }

      headers.set("Content-Length", String(object.size));
      headers.set("ETag", object.httpEtag);
      headers.set("X-Content-Type-Options", "nosniff");
      headers.set("Cache-Control", "private, no-store");

      // 最小版本按完整文件下载，不提供断点续传。
      headers.set("Accept-Ranges", "none");

      return new Response(request.method === "HEAD" ? null : object.body, {
        status: 200,
        headers,
      });
    } catch {
      return reply(request, 503, "Download temporarily unavailable");
    }
  },
};
