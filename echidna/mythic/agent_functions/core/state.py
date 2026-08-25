"""SQLite-backed per-channel state.

Persists pinned callbacks, token usage, and context reset cutoffs so
they survive container restarts. The database path defaults to the
ECHIDNA_STATE_DB env var or ./echidna_state.db (which lands in /Mythic
inside the container). To also survive container rebuilds, mount a
volume at the database location.
"""
import os
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pinned_callbacks (
    channel_id INTEGER PRIMARY KEY,
    callback_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS token_usage (
    channel_id INTEGER PRIMARY KEY,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS context_resets (
    channel_id INTEGER PRIMARY KEY,
    cutoff INTEGER NOT NULL
);
"""


class StateStore:
    def __init__(self, path=None):
        self.path = (
            path or os.environ.get("ECHIDNA_STATE_DB", "echidna_state.db")
        )
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- pinned callbacks ----

    def get_pinned(self, channel_id):
        row = self._conn.execute(
            "SELECT callback_id FROM pinned_callbacks "
            "WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return row[0] if row else None

    def set_pinned(self, channel_id, callback_id):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pinned_callbacks "
                "(channel_id, callback_id) VALUES (?, ?)",
                (channel_id, callback_id),
            )
            self._conn.commit()

    def clear_pinned(self, channel_id):
        with self._lock:
            self._conn.execute(
                "DELETE FROM pinned_callbacks WHERE channel_id = ?",
                (channel_id,),
            )
            self._conn.commit()

    # ---- token usage ----

    def add_tokens(self, channel_id, input_tokens, output_tokens):
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_usage "
                "(channel_id, input_tokens, output_tokens) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET "
                "input_tokens = input_tokens + excluded.input_tokens, "
                "output_tokens = output_tokens + excluded.output_tokens",
                (channel_id, input_tokens, output_tokens),
            )
            self._conn.commit()

    def get_tokens(self, channel_id):
        row = self._conn.execute(
            "SELECT input_tokens, output_tokens FROM token_usage "
            "WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if not row:
            return None
        return {"input": row[0], "output": row[1]}

    # ---- context reset cutoffs ----

    def set_cutoff(self, channel_id, cutoff):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO context_resets "
                "(channel_id, cutoff) VALUES (?, ?)",
                (channel_id, cutoff),
            )
            self._conn.commit()

    def get_cutoff(self, channel_id):
        row = self._conn.execute(
            "SELECT cutoff FROM context_resets WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return row[0] if row else 0


_store = None


def get_store():
    """Process-wide lazily-created StateStore singleton."""
    global _store
    if _store is None:
        _store = StateStore()
    return _store
