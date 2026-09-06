"""app/runtime/sessions.py

Session metadata (id, title, timestamps, turn count).

The *conversation* itself is stored by LangGraph's SqliteSaver checkpointer —
this table only holds the stuff you need to render a sidebar / list endpoint.
Both live in the same .db file, which stays on the machine. No network.
"""

import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    turns      INTEGER NOT NULL DEFAULT 0
);
"""


class SessionStore:

    def __init__(self, db_path: str):
        # check_same_thread=False: FastAPI serves sync endpoints from a
        # threadpool, so the connection is touched by several threads.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._lock = threading.Lock()

    def create(self, title: str | None = None) -> str:
        session_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            self.conn.commit()
        return session_id

    def ensure(self, session_id: str) -> str:
        """Accept a client-supplied session id without blowing up."""
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) "
                "VALUES (?, NULL, ?, ?)",
                (session_id, now, now),
            )
            self.conn.commit()
        return session_id

    def touch(self, session_id: str, first_message: str | None = None) -> None:
        """Bump updated_at + turn count. Titles the session from its first message."""
        with self._lock:
            if first_message:
                title = first_message.strip().replace("\n", " ")[:60]
                self.conn.execute(
                    "UPDATE sessions SET title = COALESCE(title, ?) WHERE id = ?",
                    (title, session_id),
                )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ?, turns = turns + 1 WHERE id = ?",
                (time.time(), session_id),
            )
            self.conn.commit()

    def get(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def rename(self, session_id: str, title: str) -> None:
        """Set the title explicitly, overriding the one derived from the first message."""
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title.strip()[:120], session_id),
            )
            self.conn.commit()

    def delete(self, session_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.conn.commit()