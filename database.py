"""
database.py
===========
Every SQLite operation of the app lives in this file.

The app keeps four kinds of data:
  * meetings      - one row per analyzed meeting (title, summary, decisions,
                    key points, follow-up email, original text, ...)
  * action_items  - one row per task, linked to a meeting
                    (task, owner, deadline, status)
  * meeting_chats - the saved "ask about this meeting" conversation
                    (role = user / assistant, linked to a meeting)
  * app_settings  - small key/value table for non-secret settings
                    (AI model name, Ollama URL, ...)

Security notes:
  * SQL is written with placeholders (?), never with string formatting,
    so user input can never inject SQL.
  * No API key exists - the AI runs on the local Ollama server.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

# Absolute path of this project folder (works no matter where you start python)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The database file is created next to this file on first run.
DB_PATH = os.environ.get("MEETING_DB_PATH") or os.path.join(BASE_DIR, "meeting_app.db")

# Allowed values for the task status column.
STATUSES = ("Not started", "In progress", "Done")

# Allowed values for the deadline type column.
#   absolute  -> an explicit calendar date ("2026-10-20", "Oct 20")
#   relative  -> a vague date that must be resolved ("next Monday")
#   unclassified -> text we could not classify (kept exactly as written)
DEADLINE_TYPES = ("absolute", "relative", "unclassified")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL,
    summary        TEXT    NOT NULL DEFAULT '',
    key_points     TEXT    NOT NULL DEFAULT '[]',
    decisions      TEXT    NOT NULL DEFAULT '[]',
    email_subject  TEXT    NOT NULL DEFAULT '',
    email_body     TEXT    NOT NULL DEFAULT '',
    meeting_date   TEXT,
    source_filename TEXT,
    source_text    TEXT    NOT NULL DEFAULT '',
    ai_model       TEXT,
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS action_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id        INTEGER NOT NULL
                            REFERENCES meetings(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL DEFAULT 0,
    task              TEXT    NOT NULL,
    owner             TEXT,
    deadline          TEXT,
    deadline_type     TEXT,
    deadline_resolved TEXT,
    status            TEXT    NOT NULL DEFAULT 'Not started'
);

CREATE TABLE IF NOT EXISTS meeting_chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL,           -- 'user' or 'assistant'
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_meeting ON action_items(meeting_id);
CREATE INDEX IF NOT EXISTS idx_chats_meeting ON meeting_chats(meeting_id);
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the tables if they do not exist yet (safe to call repeatedly)."""
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)


def _decode(value, default):
    """Turn JSON text coming from the database back into a Python object."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# meetings
# ---------------------------------------------------------------------------
def create_meeting(data: dict) -> int:
    """Insert a new meeting plus its action items. Returns the new meeting id."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            """
            INSERT INTO meetings
                (title, summary, key_points, decisions, email_subject,
                 email_body, meeting_date, source_filename, source_text,
                 ai_model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("title") or "Untitled meeting",
                data.get("summary") or "",
                json.dumps(data.get("key_points") or [], ensure_ascii=False),
                json.dumps(data.get("decisions") or [], ensure_ascii=False),
                data.get("email_subject") or "",
                data.get("email_body") or "",
                data.get("meeting_date") or None,
                data.get("source_filename") or None,
                data.get("source_text") or "",
                data.get("ai_model") or None,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        meeting_id = cur.lastrowid

        for position, item in enumerate(data.get("action_items") or []):
            conn.execute(
                """
                INSERT INTO action_items
                    (meeting_id, position, task, owner, deadline,
                     deadline_type, deadline_resolved, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    position,
                    item.get("task") or "",
                    item.get("owner"),
                    item.get("deadline"),
                    item.get("deadline_type"),
                    item.get("deadline_resolved"),
                    item.get("status") or "Not started",
                ),
            )
    return int(meeting_id)


def get_meeting(meeting_id: int) -> dict | None:
    """Return one meeting (with parsed JSON fields and its action items)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if row is None:
            return None

        meeting = dict(row)
        meeting["key_points"] = _decode(meeting.get("key_points"), [])
        meeting["decisions"] = _decode(meeting.get("decisions"), [])
        meeting["action_items"] = list_action_items(meeting_id)
    return meeting


def list_meetings(search: str = "") -> list[dict]:
    """All meetings, newest first, with task counters (optional search)."""
    sql = """
        SELECT m.id, m.title, m.meeting_date, m.created_at, m.source_filename,
               COUNT(a.id)                                    AS total_items,
               COALESCE(SUM(CASE WHEN a.status = 'Done'
                                 THEN 1 ELSE 0 END), 0)        AS done_items
          FROM meetings m
          LEFT JOIN action_items a ON a.meeting_id = m.id
    """
    params: tuple = ()
    if search:
        sql += " WHERE m.title LIKE ?"
        params = (f"%{search}%",)
    sql += " GROUP BY m.id ORDER BY m.created_at DESC, m.id DESC"

    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def delete_meeting(meeting_id: int) -> None:
    """Delete a meeting; its action items AND chat are removed by the
    ON DELETE CASCADE rules in the schema."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def rename_meeting(meeting_id: int, title: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))


def save_email(meeting_id: int, subject: str, body: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE meetings SET email_subject = ?, email_body = ? WHERE id = ?",
            (subject, body, meeting_id),
        )


# ---------------------------------------------------------------------------
# action items
# ---------------------------------------------------------------------------
def list_action_items(meeting_id: int) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM action_items
             WHERE meeting_id = ?
             ORDER BY position ASC, id ASC
            """,
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_action_items(meeting_id: int, items: list[dict]) -> list[dict]:
    """
    Replace the task list of a meeting.

    * items that already have an id are updated
    * items without an id are inserted
    * rows that are not in `items` are deleted (this is how "remove" works)
    """
    with closing(_connect()) as conn, conn:
        existing = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM action_items WHERE meeting_id = ?", (meeting_id,)
            ).fetchall()
        }
        kept: set[int] = set()

        for position, item in enumerate(items):
            item_id = item.get("id")
            if item_id and int(item_id) in existing:
                conn.execute(
                    """
                    UPDATE action_items
                       SET position = ?, task = ?, owner = ?, deadline = ?,
                           deadline_type = ?, deadline_resolved = ?, status = ?
                     WHERE id = ? AND meeting_id = ?
                    """,
                    (
                        position,
                        item.get("task") or "",
                        item.get("owner"),
                        item.get("deadline"),
                        item.get("deadline_type"),
                        item.get("deadline_resolved"),
                        item.get("status") or "Not started",
                        int(item_id),
                        meeting_id,
                    ),
                )
                kept.add(int(item_id))
            else:
                cur = conn.execute(
                    """
                    INSERT INTO action_items
                        (meeting_id, position, task, owner, deadline,
                         deadline_type, deadline_resolved, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meeting_id,
                        position,
                        item.get("task") or "",
                        item.get("owner"),
                        item.get("deadline"),
                        item.get("deadline_type"),
                        item.get("deadline_resolved"),
                        item.get("status") or "Not started",
                    ),
                )
                kept.add(int(cur.lastrowid))

        removed = existing - kept
        for item_id in removed:
            conn.execute(
                "DELETE FROM action_items WHERE id = ? AND meeting_id = ?",
                (item_id, meeting_id),
            )

    return list_action_items(meeting_id)


# ---------------------------------------------------------------------------
# chat ("ask about this meeting")
# ---------------------------------------------------------------------------
def add_chat(meeting_id: int, role: str, content: str) -> int:
    """Store one chat message ('user' or 'assistant'). Returns its id."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            """
            INSERT INTO meeting_chats (meeting_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (meeting_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )
    return int(cur.lastrowid)


def list_chats(meeting_id: int) -> list[dict]:
    """The whole conversation of one meeting, oldest first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, created_at
              FROM meeting_chats
             WHERE meeting_id = ?
             ORDER BY id ASC
            """,
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_chats(meeting_id: int) -> None:
    """Delete the saved conversation of one meeting."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM meeting_chats WHERE meeting_id = ?", (meeting_id,))


def search_meeting_texts(keyword: str, limit: int = 10) -> list[dict]:
    """Meetings whose title OR original notes contain the keyword."""
    like = f"%{keyword}%"
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, title, meeting_date, summary, source_text
              FROM meetings
             WHERE title LIKE ? OR source_text LIKE ?
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# dashboard statistics
# ---------------------------------------------------------------------------
def dashboard_stats() -> dict:
    with closing(_connect()) as conn:
        totals = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM meetings)                 AS meetings,
                   (SELECT COUNT(*) FROM action_items)             AS tasks,
                   (SELECT COUNT(*) FROM action_items
                     WHERE status != 'Done')                       AS pending,
                   (SELECT COUNT(*) FROM action_items
                     WHERE status = 'Done')                        AS done
            """
        ).fetchone()
        recent = conn.execute(
            """
            SELECT m.id, m.title, m.meeting_date, m.created_at,
                   COUNT(a.id)                             AS total_items,
                   COALESCE(SUM(CASE WHEN a.status = 'Done'
                                     THEN 1 ELSE 0 END), 0) AS done_items
              FROM meetings m
              LEFT JOIN action_items a ON a.meeting_id = m.id
             GROUP BY m.id
             ORDER BY m.created_at DESC, m.id DESC
             LIMIT 6
            """
        ).fetchall()
        pending_rows = conn.execute(
            """
            SELECT a.id, a.task, a.owner, a.deadline, a.status,
                   m.id   AS meeting_id,
                   m.title AS meeting_title
              FROM action_items a
              JOIN meetings m ON m.id = a.meeting_id
             WHERE a.status != 'Done'
             ORDER BY m.created_at DESC, a.position ASC
             LIMIT 8
            """
        ).fetchall()

    return {
        "totals": dict(totals),
        "recent": [dict(r) for r in recent],
        "pending": [dict(r) for r in pending_rows],
    }


# ---------------------------------------------------------------------------
# app settings (never stores secrets)
# ---------------------------------------------------------------------------
def get_setting(key: str, default: str = "") -> str:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
