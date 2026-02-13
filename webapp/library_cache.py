"""Local cache for content-library cinemeta metadata."""

import json
import os
import sqlite3
import time


class LibraryMetaCache:
    def __init__(self, config_dir="."):
        self.db_path = os.path.join(config_dir, "library.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_meta_cache (
                    imdb_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (imdb_id, kind)
                )
                """
            )

    def get(self, imdb_id, kind):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload FROM media_meta_cache WHERE imdb_id = ? AND kind = ?",
                (imdb_id, kind),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set(self, imdb_id, kind, payload):
        data = json.dumps(payload or {})
        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO media_meta_cache (imdb_id, kind, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(imdb_id, kind)
                DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (imdb_id, kind, data, now),
            )
