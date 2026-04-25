from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import sqlite3


DEFAULT_USER_ID = "local-user"
DEFAULT_THREAD_ID = "default-thread"


class AvelinDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """
                INSERT OR IGNORE INTO users (id, display_name)
                VALUES (?, ?)
                """,
                (DEFAULT_USER_ID, "Local user"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chat_threads (id, user_id, title)
                VALUES (?, ?, ?)
                """,
                (DEFAULT_THREAD_ID, DEFAULT_USER_ID, "Main conversation"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO model_settings (user_id, provider, model_name)
                VALUES (?, ?, ?)
                """,
                (DEFAULT_USER_ID, "mock", "mock-local"),
            )

    def get_metadata(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def has_memory_content(self, user_id: str = DEFAULT_USER_ID) -> bool:
        with self.connect() as connection:
            notes_count = connection.execute(
                "SELECT COUNT(*) AS count FROM notes WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            messages_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM messages
                WHERE thread_id IN (
                    SELECT id FROM chat_threads WHERE user_id = ?
                )
                """,
                (user_id,),
            ).fetchone()["count"]
            return int(notes_count) > 0 or int(messages_count) > 0

    def create_user(
        self,
        email: str,
        username: str,
        password_hash: str,
        display_name: str,
    ) -> dict[str, str]:
        user_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, username, password_hash, display_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, email, username, password_hash, display_name),
            )
            self._ensure_user_defaults(connection, user_id)
        return self.get_user(user_id) or {
            "id": user_id,
            "email": email,
            "username": username,
            "display_name": display_name,
            "auth_provider": "password",
        }

    def get_user(self, user_id: str) -> dict[str, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, username, display_name
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return None
            provider_row = connection.execute(
                """
                SELECT provider
                FROM auth_accounts
                WHERE user_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return {
                "id": str(row["id"]),
                "email": str(row["email"] or ""),
                "username": str(row["username"] or ""),
                "display_name": str(row["display_name"]),
                "auth_provider": str(provider_row["provider"]) if provider_row else "password",
            }

    def get_user_for_login(self, login: str) -> dict[str, str] | None:
        normalized = login.strip().lower()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, username, display_name, password_hash
                FROM users
                WHERE lower(email) = ? OR lower(username) = ?
                """,
                (normalized, normalized),
            ).fetchone()
            if not row:
                return None
            return {
                "id": str(row["id"]),
                "email": str(row["email"] or ""),
                "username": str(row["username"] or ""),
                "display_name": str(row["display_name"]),
                "password_hash": str(row["password_hash"] or ""),
                "auth_provider": "password",
            }

    def create_session(self, user_id: str, token_hash: str, expires_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, token_hash, expires_at),
            )

    def get_user_by_session_token_hash(self, token_hash: str) -> dict[str, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT users.id
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND (sessions.expires_at IS NULL OR sessions.expires_at > CURRENT_TIMESTAMP)
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            return self.get_user(str(row["id"]))

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def ensure_user_defaults(self, user_id: str) -> None:
        with self.connect() as connection:
            self._ensure_user_defaults(connection, user_id)

    def _ensure_user_defaults(self, connection: sqlite3.Connection, user_id: str) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO chat_threads (id, user_id, title)
            VALUES (?, ?, ?)
            """,
            (user_id, user_id, "Main conversation"),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO model_settings (user_id, provider, model_name)
            VALUES (?, ?, ?)
            """,
            (user_id, "mock", "mock-local"),
        )

    def add_note(self, note: str, user_id: str = DEFAULT_USER_ID) -> None:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO notes (id, user_id, content) VALUES (?, ?, ?)",
                (str(uuid4()), user_id, note),
            )

    def list_notes(self, user_id: str = DEFAULT_USER_ID) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT content
                FROM notes
                WHERE user_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (user_id,),
            ).fetchall()
            return [str(row["content"]) for row in rows]

    def add_message(
        self,
        role: str,
        content: str,
        name: str | None = None,
        thread_id: str = DEFAULT_THREAD_ID,
    ) -> None:
        user_id = thread_id if thread_id != DEFAULT_THREAD_ID else DEFAULT_USER_ID
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid4()), thread_id, role, content, name),
            )

    def list_messages(self, thread_id: str = DEFAULT_THREAD_ID) -> list[dict[str, str | None]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, name
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (thread_id,),
            ).fetchall()
            return [
                {"role": str(row["role"]), "content": str(row["content"]), "name": row["name"]}
                for row in rows
            ]

    def get_model_settings(self, user_id: str = DEFAULT_USER_ID) -> dict[str, str]:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT provider, model_name, ollama_url
                FROM model_settings
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return {"provider": "mock", "model_name": "mock-local", "ollama_url": ""}
            return {
                "provider": str(row["provider"]),
                "model_name": str(row["model_name"]),
                "ollama_url": str(row["ollama_url"] or ""),
            }

    def set_model_settings(
        self,
        user_id: str,
        provider: str,
        model_name: str,
        ollama_url: str | None = None,
    ) -> dict[str, str]:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_settings (user_id, provider, model_name, ollama_url, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    provider = excluded.provider,
                    model_name = excluded.model_name,
                    ollama_url = excluded.ollama_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, provider, model_name, ollama_url),
            )
        return self.get_model_settings(user_id)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    username TEXT UNIQUE,
    password_hash TEXT,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Main conversation',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'mock',
    model_name TEXT NOT NULL DEFAULT 'mock-local',
    ollama_url TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_accounts_user_id ON auth_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_user_id ON chat_threads(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);
"""
