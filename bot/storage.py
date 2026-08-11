"""Tiny SQLite store for users, jobs and settings (owner panel data)."""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("DB_PATH", "/tmp/pdfbot.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                banned INTEGER NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 0,
                jobs INTEGER NOT NULL DEFAULT 0,
                pages INTEGER NOT NULL DEFAULT 0,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pages INTEGER NOT NULL,
                mode TEXT NOT NULL,
                in_bytes INTEGER NOT NULL,
                out_bytes INTEGER NOT NULL,
                seconds REAL NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def get_setting(key: str, default: str) -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def touch_user(user_id: int, username: Optional[str], name: Optional[str]) -> Dict[str, Any]:
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "INSERT INTO users(user_id, username, name, first_seen, last_seen) "
            "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "username = excluded.username, name = excluded.name, last_seen = excluded.last_seen",
            (user_id, username, name, now, now),
        )
        row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row)


def set_flag(user_id: int, field: str, value: int) -> None:
    if field not in ("banned", "approved"):
        raise ValueError(field)
    with _conn() as c:
        c.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))


def record_job(user_id: int, pages: int, mode: str, in_bytes: int, out_bytes: int, seconds: float) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs(user_id, pages, mode, in_bytes, out_bytes, seconds, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, pages, mode, in_bytes, out_bytes, seconds, int(time.time())),
        )
        c.execute(
            "UPDATE users SET jobs = jobs + 1, pages = pages + ? WHERE user_id = ?",
            (pages, user_id),
        )


def stats() -> Dict[str, Any]:
    day_ago = int(time.time()) - 86400
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        banned = c.execute("SELECT COUNT(*) n FROM users WHERE banned = 1").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM users WHERE last_seen > ?", (day_ago,)).fetchone()["n"]
        jrow = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(pages),0) p, COALESCE(SUM(in_bytes),0) i, "
            "COALESCE(SUM(out_bytes),0) o, COALESCE(AVG(seconds),0) s FROM jobs"
        ).fetchone()
        today = c.execute("SELECT COUNT(*) n FROM jobs WHERE created_at > ?", (day_ago,)).fetchone()["n"]
    return {
        "users": users,
        "banned": banned,
        "active_24h": active,
        "jobs": jrow["n"],
        "jobs_24h": today,
        "pages": jrow["p"],
        "in_bytes": jrow["i"],
        "out_bytes": jrow["o"],
        "avg_seconds": jrow["s"],
    }


def recent_users(limit: int = 10) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def all_user_ids(exclude_banned: bool = True) -> List[int]:
    q = "SELECT user_id FROM users" + (" WHERE banned = 0" if exclude_banned else "")
    with _conn() as c:
        return [r["user_id"] for r in c.execute(q).fetchall()]