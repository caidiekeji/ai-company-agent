"""SQLite 数据层：PRD v1.2 §6 数据模型 + FTS5 trigram 中文检索 + 迁移。

设计要点：
- 原始事件层（events / meeting_messages / rpa_runs）与知识条目层（knowledge_items）分离。
- 向量按「嵌入模型 + 维度」分表，避免混合维度写入同一张表（F-KB-10）。
- 旧库通过 `_migrate()` 增量补列，保证用户已存在的数据不丢。
"""
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
AVATAR_DIR = DATA_DIR / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
RPA_PROFILE_DIR = DATA_DIR / "rpa_profiles"
RPA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS departments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  gender TEXT NOT NULL DEFAULT '其他',            -- 男 | 女 | 其他
  avatar_path TEXT,
  role_prompt TEXT NOT NULL DEFAULT '',
  personality_prompt TEXT NOT NULL DEFAULT '',
  llm_config_id INTEGER, asr_config_id INTEGER, tts_config_id INTEGER, tts_voice_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_credentials(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '默认',
  base_url TEXT,
  api_key_ref TEXT NOT NULL,                      -- keyring 引用，非明文
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(provider, alias)
);

CREATE TABLE IF NOT EXISTS llm_configs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  credential_id INTEGER REFERENCES provider_credentials(id) ON DELETE RESTRICT,
  model_id TEXT NOT NULL,
  temperature REAL DEFAULT 0.7,
  max_tokens INTEGER DEFAULT 2048,
  context_window INTEGER DEFAULT 32768,
  extra_params_json TEXT DEFAULT '{}',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS asr_configs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  credential_id INTEGER REFERENCES provider_credentials(id) ON DELETE RESTRICT,
  engine_type TEXT NOT NULL DEFAULT 'cloud',      -- local | cloud
  model_id TEXT NOT NULL DEFAULT '',
  model_path TEXT,
  extra_params_json TEXT DEFAULT '{}',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tts_configs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  credential_id INTEGER REFERENCES provider_credentials(id) ON DELETE RESTRICT,
  engine_type TEXT NOT NULL DEFAULT 'cloud',      -- local | cloud
  model_id TEXT NOT NULL DEFAULT '',
  voice_id TEXT,
  model_path TEXT,
  extra_params_json TEXT DEFAULT '{}',
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meetings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  host_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
  scheduler_mode TEXT NOT NULL DEFAULT 'round_robin',   -- round_robin | host_pick | ai_pick
  max_rounds INTEGER NOT NULL DEFAULT 6,
  token_budget INTEGER NOT NULL DEFAULT 60000,
  total_tokens_used INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',                 -- draft | running | ended | stopped
  summary TEXT,
  decisions_json TEXT DEFAULT '[]',
  todos_json TEXT DEFAULT '[]',
  context_visibility TEXT NOT NULL DEFAULT 'host_full',  -- host_full | recent_n
  visible_rounds INTEGER NOT NULL DEFAULT 6,              -- recent_n 模式下的可见轮数
  compress_context INTEGER NOT NULL DEFAULT 0,            -- F-MTG-10 上下文压缩
  template_id TEXT,
  knowledge_item_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS meeting_participants(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  seat INTEGER NOT NULL DEFAULT 0,
  joined_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(meeting_id, employee_id)
);

CREATE TABLE IF NOT EXISTS meeting_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  employee_id INTEGER,
  role TEXT NOT NULL,                                   -- host | member | user
  content TEXT NOT NULL,
  model_used TEXT,
  tokens INTEGER NOT NULL DEFAULT 0,
  interrupted INTEGER NOT NULL DEFAULT 0,
  seq INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rpa_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER,
  start_url TEXT,
  actions_json TEXT DEFAULT '[]',
  extracted_content TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  title TEXT,
  error TEXT,
  started_at TEXT, ended_at TEXT
);

CREATE TABLE IF NOT EXISTS rpa_sessions(
  id TEXT PRIMARY KEY,
  employee_id INTEGER,
  current_url TEXT,
  status TEXT NOT NULL DEFAULT 'idle',                  -- idle | running | awaiting_human
  pending_action TEXT,
  page_title TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rpa_task_templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT,
  url TEXT,
  steps_json TEXT DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,                                  -- meeting | rpa | manual
  ref_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
  tags TEXT DEFAULT '',
  embedding_status TEXT NOT NULL DEFAULT 'pending',    -- pending | done | failed | rebuilding
  embedding_model TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 trigram：解决中文无空格分词
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  title, content, tokenize='trigram'
);

-- ---------- 系统设置（F-SYS） ----------
CREATE TABLE IF NOT EXISTS app_settings(
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS domain_whitelist(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL UNIQUE,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- F-MOD-04：本地模型包（ASR/TTS/嵌入/浏览器内核）下载状态
CREATE TABLE IF NOT EXISTS model_packages(
  id TEXT PRIMARY KEY,                                 -- 包标识，如 bge-small-zh-v1.5
  category TEXT NOT NULL,                              -- asr | tts | embedding | browser
  display_name TEXT NOT NULL,
  version TEXT,
  size_bytes INTEGER DEFAULT 0,
  installed INTEGER NOT NULL DEFAULT 0,
  install_path TEXT,
  checksum TEXT,
  source_url TEXT,
  license TEXT,
  note TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- F-KB-10：重嵌入迁移任务
CREATE TABLE IF NOT EXISTS embedding_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_model TEXT NOT NULL,
  target_dim INTEGER,
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running',              -- running | done | failed | cancelled
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT
);

-- F-VOI-05：流式 TTS 缓存
CREATE TABLE IF NOT EXISTS voice_cache(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text_hash TEXT NOT NULL,
  config_id INTEGER,
  voice_id TEXT,
  audio_path TEXT NOT NULL,
  duration_ms INTEGER DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(text_hash, config_id, voice_id)
);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


# 旧库增量迁移：补列（新增表由 SCHEMA 的 IF NOT EXISTS 负责）
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("employees", "avatar_path", "TEXT"),
    ("employees", "asr_config_id", "INTEGER"),
    ("employees", "tts_config_id", "INTEGER"),
    ("employees", "tts_voice_id", "TEXT"),
    ("meetings", "total_tokens_used", "INTEGER NOT NULL DEFAULT 0"),
    ("meetings", "context_visibility", "TEXT NOT NULL DEFAULT 'host_full'"),
    ("meetings", "visible_rounds", "INTEGER NOT NULL DEFAULT 6"),
    ("meetings", "compress_context", "INTEGER NOT NULL DEFAULT 0"),
    ("meetings", "template_id", "TEXT"),
    ("meetings", "knowledge_item_id", "INTEGER"),
    ("meeting_messages", "interrupted", "INTEGER NOT NULL DEFAULT 0"),
    ("meeting_messages", "seq", "INTEGER NOT NULL DEFAULT 0"),
    ("knowledge_items", "embedding_model", "TEXT"),
    ("knowledge_items", "embedding_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("rpa_runs", "title", "TEXT"),
    ("rpa_runs", "error", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _MIGRATIONS:
        cols = _table_columns(conn, table)
        if cols and column not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.Error:
                pass


def init_db() -> None:
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


def query(sql: str, args: tuple = ()) -> list[dict]:
    cur = get_conn().execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, args: tuple = ()) -> dict | None:
    cur = get_conn().execute(sql, args)
    row = cur.fetchone()
    return dict(row) if row else None


def execute(sql: str, args: tuple = ()) -> int:
    """写操作（线程锁 + 事务提交），返回 lastrowid。"""
    with _lock:
        conn = get_conn()
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid or cur.rowcount


def execute_many(sql: str, seq: list[tuple]) -> None:
    with _lock:
        conn = get_conn()
        conn.executemany(sql, seq)
        conn.commit()


def fts_sync(item_id: int, title: str, content: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (item_id,))
        conn.execute("INSERT INTO knowledge_fts(rowid, title, content) VALUES (?,?,?)", (item_id, title, content))
        conn.commit()


def fts_delete(item_id: int) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (item_id,))
        conn.commit()
