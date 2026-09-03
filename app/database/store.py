import json
from datetime import datetime, timezone

from app.database.json_store import JsonStore, DEFAULT_STATE


class SqlStore:
    """Small synchronous SQL-backed KV store for persistent bot state/history.

    Stores the same JSON structures used by JsonStore so the rest of the
    application remains unchanged. Supports PostgreSQL (recommended on Render)
    and SQLite URLs for local tests.
    """

    def __init__(self, database_url: str):
        from sqlalchemy import create_engine, text

        url = str(database_url or "").strip()
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = "postgresql+psycopg://" + url[len("postgresql://"):]

        self._text = text
        self.engine = create_engine(url, pool_pre_ping=True, future=True)
        with self.engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS bot_kv (
                    key VARCHAR(64) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            ))

    def _load(self, key, default):
        with self.engine.connect() as conn:
            row = conn.execute(
                self._text("SELECT value FROM bot_kv WHERE key=:key"),
                {"key": key},
            ).first()
        if row is None:
            return json.loads(json.dumps(default, ensure_ascii=False))
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return json.loads(json.dumps(default, ensure_ascii=False))

    def _save(self, key, value):
        payload = json.dumps(value, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO bot_kv(key, value, updated_at)
                    VALUES (:key, :value, :updated_at)
                    ON CONFLICT (key) DO UPDATE SET
                        value=EXCLUDED.value,
                        updated_at=EXCLUDED.updated_at
                    """
                ),
                {"key": key, "value": payload, "updated_at": now},
            )

    def state(self):
        state = self._load("state", DEFAULT_STATE)
        for key, default in DEFAULT_STATE.items():
            if key not in state:
                state[key] = json.loads(json.dumps(default, ensure_ascii=False))
        return state

    def history(self):
        return self._load("history", [])

    def save_state(self, value):
        self._save("state", value)

    def save_history(self, value):
        self._save("history", value)


def build_store(settings):
    database_url = str(getattr(settings, "database_url", "") or "").strip()
    require_persistent = bool(getattr(settings, "persistent_storage_required", False))

    if database_url:
        store = SqlStore(database_url)
        print("[storage] persistent SQL store enabled")
        return store

    if require_persistent:
        raise RuntimeError(
            "Persistent storage is required but DATABASE_URL is missing. "
            "Configure a PostgreSQL DATABASE_URL before production deployment."
        )

    print(
        "[storage] WARNING: DATABASE_URL missing; using local JSON storage. "
        "This is suitable for local/testing only and may be lost on ephemeral hosting."
    )
    return JsonStore(getattr(settings, "state_dir", "data"))
