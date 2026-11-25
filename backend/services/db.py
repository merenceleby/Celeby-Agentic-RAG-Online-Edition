"""Sohbet geçmişi, feedback ve metrikler için ortak SQLite bağlantısı."""
import sqlite3
from contextlib import contextmanager

from config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT, metadata TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
CREATE TABLE IF NOT EXISTS feedback (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  rating INTEGER NOT NULL, comment TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, mode TEXT NOT NULL, ok INTEGER NOT NULL,
  error_kind TEXT, latency REAL, llm_calls INTEGER, cache_hit INTEGER, corrected INTEGER,
  validated INTEGER, num_sources INTEGER, query TEXT);
CREATE TABLE IF NOT EXISTS eval_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_bans (
  ip TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0,
  ban_level INTEGER NOT NULL DEFAULT 0, banned_until REAL NOT NULL DEFAULT 0);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(str(settings.app_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
