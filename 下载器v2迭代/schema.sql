-- D1 资源目录：首次建库执行，不修改已上传的 R2 文件。
-- 不包含 Bestdori V2 模型记录；所有路径均为 R2 对象 key，不带域名。
-- 乐队、角色基础资料及展示顺序由本地配置维护，不建对应表。
-- character_id 是 char_info_json 中的 romaji，因角色资料在本地，不设数据库外键。

CREATE TABLE models_v3 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT NOT NULL UNIQUE,         -- 原始模型 ID，与数据库行号区分
    character_id TEXT NOT NULL,
    name         TEXT,                        -- 为 NULL 时显示模型 ID
    r2_key       TEXT NOT NULL UNIQUE,
    sha256       TEXT CHECK (
        sha256 IS NULL OR
        (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')
    )                                         -- 小写十六进制；未生成时为 NULL
);

CREATE TABLE images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    r2_key       TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN ('background', 'avatar')),
    character_id TEXT,
    name         TEXT,                        -- 为 NULL 时显示文件名
    CHECK (
        (kind = 'background' AND character_id IS NULL) OR
        (kind = 'avatar' AND character_id IS NOT NULL)
    )
);

CREATE INDEX idx_models_character ON models_v3(character_id, id);
CREATE INDEX idx_images_kind_character ON images(kind, character_id, id);
