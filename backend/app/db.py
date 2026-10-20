"""SQLite 数据层：PRD v1.2 §6 数据模型 + FTS5 trigram 中文检索。"""
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

_lock = threading.Lock()
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
  engine_type TEXT NOT NULL DEFAULT 'cloud',
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
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at TEXT
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
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rpa_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER,
  start_url TEXT,
  actions_json TEXT DEFAULT '[]',
  extracted_content TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  started_at TEXT, ended_at TEXT
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
  embedding_status TEXT NOT NULL DEFAULT 'pending',    -- pending | done | failed
  embedding_model TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 trigram：解决中文无空格分词（SQLCipher 内置 SQLite >= 3.34 才有 trigram）
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  title, content, tokenize='trigram'
);
"""


def init_db() -> None:
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
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


def fts_sync(item_id: int, title: str, content: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (item_id,))
        conn.execute("INSERT INTO knowledge_fts(rowid, title, content) VALUES (?,?,?)", (item_id, title, content))
        conn.commit()
