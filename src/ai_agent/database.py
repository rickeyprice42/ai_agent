from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import json
import sqlite3


DEFAULT_USER_ID = "local-user"
DEFAULT_THREAD_ID = "default-thread"


class AvelinDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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
            INSERT OR IGNORE INTO users (id, display_name)
            VALUES (?, ?)
            """,
            (user_id, "Local user"),
        )
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

    def create_task(
        self,
        user_id: str,
        description: str,
        priority: int = 3,
        steps: list[str] | None = None,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        task_id = str(uuid4())
        normalized_steps = [step.strip() for step in steps or [] if step.strip()]
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (id, user_id, description, priority)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, user_id, description, priority),
            )
            for position, step in enumerate(normalized_steps, start=1):
                connection.execute(
                    """
                    INSERT INTO task_steps (id, task_id, position, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(uuid4()), task_id, position, step),
                )
        return self.get_task(task_id) or {
            "id": task_id,
            "user_id": user_id,
            "description": description,
            "status": "created",
            "priority": priority,
            "result": None,
            "steps": [],
        }

    def get_task(self, task_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, description, status, priority, result
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "description": str(row["description"]),
                "status": str(row["status"]),
                "priority": int(row["priority"]),
                "result": row["result"],
                "steps": self.list_task_steps(str(row["id"])),
            }

    def list_tasks(self, user_id: str, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, description, status, priority, result
                FROM tasks
                WHERE user_id = ?
                ORDER BY
                    CASE status
                        WHEN 'executing' THEN 1
                        WHEN 'blocked' THEN 2
                        WHEN 'planned' THEN 3
                        WHEN 'created' THEN 4
                        WHEN 'failed' THEN 5
                        WHEN 'completed' THEN 6
                        ELSE 7
                    END,
                    priority ASC,
                    created_at ASC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        tasks: list[dict] = []
        for row in rows:
            tasks.append(
                {
                    "id": str(row["id"]),
                    "user_id": str(row["user_id"]),
                    "description": str(row["description"]),
                    "status": str(row["status"]),
                    "priority": int(row["priority"]),
                    "result": row["result"],
                    "steps": self.list_task_steps(str(row["id"])),
                }
            )
        return tasks

    def update_task_status(self, task_id: str, status: str, result: str | None = None) -> dict | None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, result = COALESCE(?, result), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, result, task_id),
            )
        return self.get_task(task_id)

    def list_task_steps(self, task_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, description, status, position, result
                FROM task_steps
                WHERE task_id = ?
                ORDER BY position ASC, created_at ASC
                """,
                (task_id,),
            ).fetchall()
            return [
                {
                    "id": str(row["id"]),
                    "task_id": str(row["task_id"]),
                    "description": str(row["description"]),
                    "status": str(row["status"]),
                    "position": int(row["position"]),
                    "result": row["result"],
                }
                for row in rows
            ]

    def add_task_step(self, task_id: str, description: str) -> dict | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        position = len(task["steps"]) + 1
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_steps (id, task_id, position, description)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid4()), task_id, position, description),
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = CASE WHEN status = 'created' THEN 'planned' ELSE status END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
            )
        return self.get_task(task_id)

    def update_task_step_status(
        self,
        step_id: str,
        status: str,
        result: str | None = None,
    ) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM task_steps WHERE id = ?",
                (step_id,),
            ).fetchone()
            if not row:
                return None
            task_id = str(row["task_id"])
            connection.execute(
                """
                UPDATE task_steps
                SET status = ?, result = COALESCE(?, result), updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, result, step_id),
            )
        return self.get_task(task_id)

    def add_action_log(
        self,
        user_id: str,
        tool_name: str,
        status: str,
        arguments: dict,
        result: str,
    ) -> None:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO action_logs (id, user_id, tool_name, status, arguments_json, result)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    user_id,
                    tool_name,
                    status,
                    json.dumps(arguments, ensure_ascii=False),
                    result,
                ),
            )

    def list_action_logs(self, user_id: str, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, tool_name, status, arguments_json, result, created_at
                FROM action_logs
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        logs: list[dict] = []
        for row in rows:
            try:
                arguments = json.loads(str(row["arguments_json"] or "{}"))
            except json.JSONDecodeError:
                arguments = {}
            logs.append(
                {
                    "id": str(row["id"]),
                    "user_id": str(row["user_id"]),
                    "tool_name": str(row["tool_name"]),
                    "status": str(row["status"]),
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": str(row["result"] or ""),
                    "created_at": str(row["created_at"]),
                }
            )
        return logs

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

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    priority INTEGER NOT NULL DEFAULT 3,
    result TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS action_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_user_id ON action_logs(user_id);
"""
