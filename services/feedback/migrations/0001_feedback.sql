-- 所有正文只存在于 payload；撤回保留无正文的有限期控制标记。
CREATE TABLE feedback (
  request_id TEXT PRIMARY KEY,
  feedback_id TEXT NOT NULL UNIQUE,
  token_hash TEXT NOT NULL,
  body_hash TEXT,
  payload BLOB,
  raw_bytes INTEGER NOT NULL DEFAULT 0,
  stored_bytes INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  control_expires_at INTEGER NOT NULL,
  day TEXT NOT NULL,
  rating TEXT,
  app_version TEXT,
  has_conversation INTEGER,
  state TEXT NOT NULL CHECK (state IN ('active', 'withdrawn')),
  processed INTEGER NOT NULL DEFAULT 0,
  processed_by TEXT,
  processed_at INTEGER
);
CREATE INDEX feedback_expiry ON feedback(expires_at);
CREATE INDEX feedback_control_expiry ON feedback(control_expires_at);
CREATE INDEX feedback_list ON feedback(state, created_at DESC, feedback_id DESC);
CREATE TABLE daily_usage(day TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0,
  raw_bytes INTEGER NOT NULL DEFAULT 0, stored_bytes INTEGER NOT NULL DEFAULT 0,
  controls INTEGER NOT NULL DEFAULT 0);
CREATE TABLE limits(id INTEGER PRIMARY KEY CHECK(id=1), daily_count INTEGER NOT NULL,
  daily_raw INTEGER NOT NULL, daily_stored INTEGER NOT NULL, total_stored INTEGER NOT NULL,
  daily_controls INTEGER NOT NULL, used_stored INTEGER NOT NULL DEFAULT 0);
-- 压缩正文预算 300 MiB，预留索引、元数据和 SQLite 页开销；可按实际套餐调整。
INSERT INTO limits VALUES (1, 200, 20971520, 3145728, 314572800, 1000, 0);

CREATE TRIGGER feedback_quota BEFORE INSERT ON feedback BEGIN
  INSERT OR IGNORE INTO daily_usage(day) VALUES (NEW.day);
  SELECT CASE WHEN (SELECT controls >= daily_controls FROM daily_usage, limits WHERE day=NEW.day AND id=1)
    THEN RAISE(ABORT, 'feedback_control_quota') END;
  SELECT CASE WHEN NEW.state='active' AND (
    SELECT count >= daily_count OR raw_bytes + NEW.raw_bytes > daily_raw
      OR stored_bytes + NEW.stored_bytes > daily_stored
      OR used_stored + NEW.stored_bytes > total_stored
    FROM daily_usage, limits WHERE day=NEW.day AND id=1
  ) THEN RAISE(ABORT, 'feedback_quota') END;
END;
CREATE TRIGGER feedback_account AFTER INSERT ON feedback BEGIN
  UPDATE daily_usage SET controls=controls+1,
    count=count+(NEW.state='active'), raw_bytes=raw_bytes+NEW.raw_bytes,
    stored_bytes=stored_bytes+NEW.stored_bytes WHERE day=NEW.day;
  UPDATE limits SET used_stored=used_stored+NEW.stored_bytes WHERE id=1;
END;
CREATE TRIGGER feedback_release AFTER UPDATE OF stored_bytes ON feedback BEGIN
  UPDATE limits SET used_stored=used_stored+NEW.stored_bytes-OLD.stored_bytes WHERE id=1;
END;
CREATE TRIGGER feedback_delete AFTER DELETE ON feedback BEGIN
  UPDATE limits SET used_stored=used_stored-OLD.stored_bytes WHERE id=1;
END;
