-- MAuto 智能助手平台 V1.0 数据库结构（SQLite）
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS user (
  id            TEXT PRIMARY KEY,
  nickname      TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt          TEXT NOT NULL,
  avatar        TEXT,
  status        TEXT NOT NULL DEFAULT 'active',
  prefs_json    TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  title      TEXT NOT NULL DEFAULT '新会话',
  project_id TEXT,
  model      TEXT NOT NULL DEFAULT 'MAuto-Air',
  is_pinned  INTEGER NOT NULL DEFAULT 0,
  is_archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation(user_id, is_archived, updated_at);

CREATE TABLE IF NOT EXISTS message (
  id            TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL DEFAULT '',
  tokens        INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'done',
  model         TEXT,
  feedback      INTEGER NOT NULL DEFAULT 0,
  citations_json TEXT NOT NULL DEFAULT '[]',
  plugin_calls_json TEXT NOT NULL DEFAULT '[]',
  reasoning     TEXT NOT NULL DEFAULT '',
  attachments_json TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON message(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS attachment (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  filename   TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  file_size  INTEGER NOT NULL DEFAULT 0,
  mime       TEXT,
  parse_status TEXT NOT NULL DEFAULT 'parsed',
  text_excerpt TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  freq_type   TEXT NOT NULL,
  freq_value  TEXT NOT NULL DEFAULT '',
  run_time    TEXT NOT NULL,
  timezone    TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  notify_types TEXT NOT NULL DEFAULT '["inbox"]',
  output_format TEXT NOT NULL DEFAULT 'chat',
  status      TEXT NOT NULL DEFAULT 'enabled',
  next_run_at TEXT,
  last_run_at TEXT,
  last_status TEXT,
  last_result TEXT,
  output_conversation_id TEXT,
  fail_count  INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  retry_at    TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_due ON task(status, next_run_at);

CREATE TABLE IF NOT EXISTS task_run (
  id             TEXT PRIMARY KEY,
  task_id        TEXT NOT NULL,
  run_at         TEXT NOT NULL,
  status         TEXT NOT NULL,
  duration_ms    INTEGER NOT NULL DEFAULT 0,
  result_content TEXT NOT NULL DEFAULT '',
  error_msg      TEXT NOT NULL DEFAULT '',
  summary        TEXT NOT NULL DEFAULT '',
  conversation_id TEXT,
  message_id     TEXT,
  attempt        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_run_task ON task_run(task_id, run_at);

CREATE TABLE IF NOT EXISTS news_category (
  id   TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  icon TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS news (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  summary     TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT '',
  industry_id TEXT NOT NULL,
  publish_at  TEXT NOT NULL,
  heat        INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'online',
  ai_points_json TEXT NOT NULL DEFAULT '[]',
  url         TEXT NOT NULL DEFAULT '',
  cover       TEXT NOT NULL DEFAULT '',
  read_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_news_ind ON news(industry_id, publish_at);

CREATE TABLE IF NOT EXISTS news_state (
  user_id  TEXT NOT NULL,
  news_id  TEXT NOT NULL,
  is_read  INTEGER NOT NULL DEFAULT 0,
  is_favorite INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, news_id)
);

CREATE TABLE IF NOT EXISTS news_subscription (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  type       TEXT NOT NULL,
  target_id  TEXT NOT NULL DEFAULT '',
  keyword    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_sync (
  id          TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  item_count  INTEGER NOT NULL DEFAULT 0,
  feed_ok     INTEGER NOT NULL DEFAULT 0,
  feed_total  INTEGER NOT NULL DEFAULT 0,
  error       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plugin (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  icon        TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  detail      TEXT NOT NULL DEFAULT '',
  category    TEXT NOT NULL DEFAULT '办公效率',
  type        TEXT NOT NULL DEFAULT 'public',
  author_id   TEXT,
  status      TEXT NOT NULL DEFAULT 'approved',
  install_count INTEGER NOT NULL DEFAULT 0,
  rating      REAL NOT NULL DEFAULT 4.8,
  rating_count INTEGER NOT NULL DEFAULT 128,
  version     TEXT NOT NULL DEFAULT '1.0.0',
  changelog   TEXT NOT NULL DEFAULT '',
  params_json TEXT NOT NULL DEFAULT '[]',
  examples_json TEXT NOT NULL DEFAULT '[]',
  permissions_json TEXT NOT NULL DEFAULT '[]',
  auth_type   TEXT NOT NULL DEFAULT 'none',
  api_url     TEXT NOT NULL DEFAULT '',
  reject_reason TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_plugin (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  plugin_id  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'enabled',
  is_default INTEGER NOT NULL DEFAULT 0,
  authorized INTEGER NOT NULL DEFAULT 0,
  installed_at TEXT NOT NULL,
  UNIQUE(user_id, plugin_id)
);

CREATE TABLE IF NOT EXISTS plugin_call (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  plugin_id  TEXT NOT NULL,
  conversation_id TEXT,
  input_json TEXT NOT NULL DEFAULT '{}',
  output     TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'success',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ppt_template (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  category   TEXT NOT NULL,
  style      TEXT NOT NULL,
  ratio      TEXT NOT NULL DEFAULT '16:9',
  page_count INTEGER NOT NULL DEFAULT 12,
  use_count  INTEGER NOT NULL DEFAULT 0,
  color_a    TEXT NOT NULL DEFAULT '#1A5CC8',
  color_b    TEXT NOT NULL DEFAULT '#5B8DEF',
  layout     TEXT NOT NULL DEFAULT 'classic'
);

CREATE TABLE IF NOT EXISTS ppt_document (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  template_id TEXT NOT NULL,
  title       TEXT NOT NULL,
  content_json TEXT NOT NULL DEFAULT '[]',
  page_count  INTEGER NOT NULL DEFAULT 0,
  ratio       TEXT NOT NULL DEFAULT '16:9',
  style       TEXT NOT NULL DEFAULT '简约',
  cover_color TEXT NOT NULL DEFAULT '#1A5CC8',
  version     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ppt_version (
  id          TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  version     INTEGER NOT NULL,
  content_json TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ppt_job (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  title      TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'running',
  progress   INTEGER NOT NULL DEFAULT 0,
  message    TEXT NOT NULL DEFAULT '',
  doc_id     TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
  id          TEXT PRIMARY KEY,
  owner_id    TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  icon        TEXT NOT NULL DEFAULT '📁',
  color       TEXT NOT NULL DEFAULT '#1A5CC8',
  visibility  TEXT NOT NULL DEFAULT 'private',
  instructions TEXT NOT NULL DEFAULT '',
  is_archived INTEGER NOT NULL DEFAULT 0,
  is_deleted  INTEGER NOT NULL DEFAULT 0,
  deleted_at  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_file (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL,
  folder      TEXT NOT NULL DEFAULT '根目录',
  file_name   TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  file_size   INTEGER NOT NULL DEFAULT 0,
  mime        TEXT NOT NULL DEFAULT '',
  parse_status TEXT NOT NULL DEFAULT 'parsed',
  text_len    INTEGER NOT NULL DEFAULT 0,
  text_excerpt TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_member (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'viewer',
  created_at TEXT NOT NULL,
  UNIQUE(project_id, user_id)
);

CREATE TABLE IF NOT EXISTS notification (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  type       TEXT NOT NULL,
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  ref_type   TEXT NOT NULL DEFAULT '',
  ref_id     TEXT NOT NULL DEFAULT '',
  is_read    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notification(user_id, is_read, created_at);

CREATE TABLE IF NOT EXISTS event_log (
  id         TEXT PRIMARY KEY,
  user_id    TEXT,
  event      TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
