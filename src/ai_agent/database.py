from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import json
import re
import sqlite3


DEFAULT_USER_ID = "local-user"
DEFAULT_THREAD_ID = "default-thread"


class AvelinDatabase:
    def __init__(
        self,
        path: Path,
        default_model_provider: str = "mock",
        default_model_name: str = "mock-local",
    ) -> None:
        self.path = path
        self.default_model_provider = default_model_provider
        self.default_model_name = default_model_name
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
            self._migrate_chat_threads(connection)
            self._migrate_project_links(connection)
            self._migrate_durable_tasks(connection)
            self._migrate_messages(connection)
            self._migrate_memory_records(connection)
            self._ensure_migrated_indexes(connection)
            self._backfill_memory_search_index(connection)
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
                (DEFAULT_USER_ID, self.default_model_provider, self.default_model_name),
            )

    @staticmethod
    def _backfill_memory_search_index(connection: sqlite3.Connection) -> None:
        version_row = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'memory_records_fts_version'"
        ).fetchone()
        if version_row is not None and str(version_row["value"]) == "1":
            connection.execute(
                """
                INSERT INTO memory_records_fts (memory_id, user_id, content)
                SELECT record.id, record.user_id,
                       replace(replace(record.content, 'ё', 'е'), 'Ё', 'Е')
                FROM memory_records AS record
                WHERE NOT EXISTS (
                    SELECT 1 FROM memory_records_fts AS indexed
                    WHERE indexed.memory_id = record.id
                )
                """
            )
            return
        connection.execute("DELETE FROM memory_records_fts")
        connection.execute(
            """
            INSERT INTO memory_records_fts (memory_id, user_id, content)
            SELECT record.id, record.user_id, replace(replace(record.content, 'ё', 'е'), 'Ё', 'Е')
            FROM memory_records AS record
            """
        )
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES ('memory_records_fts_version', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """
        )

    def _migrate_messages(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        migrations = {
            "status": "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'",
            "error": "ALTER TABLE messages ADD COLUMN error TEXT",
            "provider": "ALTER TABLE messages ADD COLUMN provider TEXT",
            "model": "ALTER TABLE messages ADD COLUMN model TEXT",
            "artifacts_json": "ALTER TABLE messages ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    @staticmethod
    def _migrate_memory_records(connection: sqlite3.Connection) -> None:
        for table in ("memory_records", "health_memory_records"):
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "memory_key" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN memory_key TEXT")
            if "auto_generated" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN auto_generated INTEGER NOT NULL DEFAULT 0"
                )

    def _migrate_project_links(self, connection: sqlite3.Connection) -> None:
        table_columns = {
            "notes": {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(notes)").fetchall()
            },
            "tasks": {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            },
        }
        if "project_id" not in table_columns["notes"]:
            connection.execute("ALTER TABLE notes ADD COLUMN project_id TEXT")
        if "source_thread_id" not in table_columns["notes"]:
            connection.execute("ALTER TABLE notes ADD COLUMN source_thread_id TEXT")
        if "project_id" not in table_columns["tasks"]:
            connection.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")

    @staticmethod
    def _migrate_durable_tasks(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO task_runs (
                id, task_id, user_id, state, plan_version, deduplication_key,
                budget_json, usage_json
            )
            SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
                   substr(lower(hex(randomblob(2))), 2) || '-a' ||
                   substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
                   tasks.id,
                   tasks.user_id,
                   CASE tasks.status
                       WHEN 'executing' THEN 'queued'
                       WHEN 'blocked' THEN 'waiting_approval'
                       WHEN 'completed' THEN 'completed'
                       WHEN 'failed' THEN 'failed'
                       ELSE 'queued'
                   END,
                   1,
                   tasks.id,
                   '{"max_steps":50,"max_failures":3,"max_seconds":3600,"max_tokens":100000,"max_cost_micros":0}',
                   '{"steps":0,"failures":0,"tokens":0,"cost_micros":0}'
            FROM tasks
            """
        )

    def _migrate_chat_threads(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(chat_threads)").fetchall()
        }
        migrations = {
            "status": "ALTER TABLE chat_threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "archived_at": "ALTER TABLE chat_threads ADD COLUMN archived_at TEXT",
            "deleted_at": "ALTER TABLE chat_threads ADD COLUMN deleted_at TEXT",
            "pinned": "ALTER TABLE chat_threads ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
            "project_id": "ALTER TABLE chat_threads ADD COLUMN project_id TEXT",
            "memory_enabled": "ALTER TABLE chat_threads ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1",
            "memory_saved_at": "ALTER TABLE chat_threads ADD COLUMN memory_saved_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    def _ensure_migrated_indexes(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_threads_project_id ON chat_threads(project_id);
            CREATE INDEX IF NOT EXISTS idx_chat_threads_status ON chat_threads(status, archived_at, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
            CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
            CREATE INDEX IF NOT EXISTS idx_memory_records_key ON memory_records(user_id, memory_key, status);
            """
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
            (user_id, self.default_model_provider, self.default_model_name),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_settings (user_id, autonomous_enabled, health_autonomous_enabled)
            VALUES (?, 0, 0)
            """,
            (user_id,),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO security_settings (user_id, emergency_stop)
            VALUES (?, 0)
            """,
            (user_id,),
        )

    def list_chat_threads(
        self,
        user_id: str,
        status: str = "active",
        project_id: str | None = None,
        unassigned: bool = False,
    ) -> list[dict]:
        self.ensure_user_defaults(user_id)
        if status not in {"active", "archived", "deleted", "all"}:
            status = "active"

        where = "chat_threads.user_id = ?"
        params: list[str] = [user_id]
        if status == "active":
            where += (
                " AND chat_threads.deleted_at IS NULL"
                " AND chat_threads.archived_at IS NULL"
                " AND chat_threads.status = 'active'"
            )
        elif status == "archived":
            where += (
                " AND chat_threads.deleted_at IS NULL"
                " AND (chat_threads.archived_at IS NOT NULL OR chat_threads.status = 'archived')"
            )
        elif status == "deleted":
            where += " AND chat_threads.deleted_at IS NOT NULL"
        if project_id:
            where += " AND chat_threads.project_id = ?"
            params.append(project_id)
        elif unassigned:
            where += " AND chat_threads.project_id IS NULL"

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    chat_threads.id,
                    chat_threads.user_id,
                    chat_threads.title,
                    chat_threads.status,
                    chat_threads.archived_at,
                    chat_threads.deleted_at,
                    chat_threads.pinned,
                    chat_threads.project_id,
                    chat_threads.memory_enabled,
                    chat_threads.memory_saved_at,
                    chat_threads.created_at,
                    chat_threads.updated_at,
                    COUNT(messages.id) AS message_count,
                    MAX(messages.created_at) AS last_message_at
                FROM chat_threads
                LEFT JOIN messages ON messages.thread_id = chat_threads.id
                WHERE {where}
                GROUP BY chat_threads.id
                ORDER BY
                    chat_threads.pinned DESC,
                    COALESCE(MAX(messages.created_at), chat_threads.updated_at) DESC,
                    chat_threads.created_at DESC
                """,
                params,
            ).fetchall()
        return [self._thread_payload(row) for row in rows]

    def get_chat_thread(self, user_id: str, thread_id: str) -> dict | None:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    chat_threads.id,
                    chat_threads.user_id,
                    chat_threads.title,
                    chat_threads.status,
                    chat_threads.archived_at,
                    chat_threads.deleted_at,
                    chat_threads.pinned,
                    chat_threads.project_id,
                    chat_threads.memory_enabled,
                    chat_threads.memory_saved_at,
                    chat_threads.created_at,
                    chat_threads.updated_at,
                    COUNT(messages.id) AS message_count,
                    MAX(messages.created_at) AS last_message_at
                FROM chat_threads
                LEFT JOIN messages ON messages.thread_id = chat_threads.id
                WHERE chat_threads.user_id = ? AND chat_threads.id = ?
                GROUP BY chat_threads.id
                """,
                (user_id, thread_id),
            ).fetchone()
        return self._thread_payload(row) if row else None

    def create_chat_thread(
        self,
        user_id: str,
        title: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        if project_id and self.get_project(user_id, project_id) is None:
            return self.create_chat_thread(user_id, title=title)
        thread_id = str(uuid4())
        normalized_title = (title or "New chat").strip() or "New chat"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_threads (id, user_id, title, project_id)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, user_id, normalized_title, project_id),
            )
        return self.get_chat_thread(user_id, thread_id) or {
            "id": thread_id,
            "user_id": user_id,
            "title": normalized_title,
            "status": "active",
            "archived_at": None,
            "deleted_at": None,
            "pinned": False,
            "project_id": None,
            "memory_enabled": True,
            "memory_saved_at": None,
            "created_at": "",
            "updated_at": "",
            "message_count": 0,
            "last_message_at": None,
        }

    def update_chat_thread(
        self,
        user_id: str,
        thread_id: str,
        title: str | None = None,
        pinned: bool | None = None,
        project_id: str | None = None,
        clear_project: bool = False,
        memory_enabled: bool | None = None,
    ) -> dict | None:
        if self.get_chat_thread(user_id, thread_id) is None:
            return None
        assignments = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[str | int] = []
        if title is not None:
            assignments.append("title = ?")
            params.append(title.strip() or "Untitled chat")
        if pinned is not None:
            assignments.append("pinned = ?")
            params.append(1 if pinned else 0)
        if clear_project:
            assignments.append("project_id = NULL")
        elif project_id is not None:
            if self.get_project(user_id, project_id) is None:
                return None
            assignments.append("project_id = ?")
            params.append(project_id)
        if memory_enabled is not None:
            assignments.append("memory_enabled = ?")
            params.append(1 if memory_enabled else 0)
        params.extend([user_id, thread_id])
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE chat_threads
                SET {", ".join(assignments)}
                WHERE user_id = ? AND id = ?
                """,
                params,
            )
        return self.get_chat_thread(user_id, thread_id)

    def create_project(self, user_id: str, title: str, description: str = "") -> dict:
        self.ensure_user_defaults(user_id)
        project_id = str(uuid4())
        normalized_title = title.strip() or "Untitled project"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (id, user_id, title, description)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, user_id, normalized_title, description.strip()),
            )
        return self.get_project(user_id, project_id) or {
            "id": project_id,
            "user_id": user_id,
            "title": normalized_title,
            "description": description.strip(),
            "status": "active",
            "created_at": "",
            "updated_at": "",
            "chat_count": 0,
        }

    def get_project(self, user_id: str, project_id: str) -> dict | None:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    projects.id,
                    projects.user_id,
                    projects.title,
                    projects.description,
                    projects.status,
                    projects.created_at,
                    projects.updated_at,
                    COUNT(chat_threads.id) AS chat_count
                FROM projects
                LEFT JOIN chat_threads
                  ON chat_threads.project_id = projects.id
                 AND chat_threads.deleted_at IS NULL
                WHERE projects.user_id = ? AND projects.id = ?
                GROUP BY projects.id
                """,
                (user_id, project_id),
            ).fetchone()
        return self._project_payload(row) if row else None

    def list_projects(self, user_id: str, status: str = "active") -> list[dict]:
        self.ensure_user_defaults(user_id)
        if status not in {"active", "archived", "deleted", "all"}:
            status = "active"
        where = "projects.user_id = ?"
        params = [user_id]
        if status != "all":
            where += " AND projects.status = ?"
            params.append(status)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    projects.id,
                    projects.user_id,
                    projects.title,
                    projects.description,
                    projects.status,
                    projects.created_at,
                    projects.updated_at,
                    COUNT(chat_threads.id) AS chat_count
                FROM projects
                LEFT JOIN chat_threads
                  ON chat_threads.project_id = projects.id
                 AND chat_threads.deleted_at IS NULL
                WHERE {where}
                GROUP BY projects.id
                ORDER BY
                    CASE projects.status
                        WHEN 'active' THEN 1
                        WHEN 'archived' THEN 2
                        WHEN 'deleted' THEN 3
                        ELSE 4
                    END,
                    projects.updated_at DESC,
                    projects.created_at DESC
                """,
                params,
            ).fetchall()
        return [self._project_payload(row) for row in rows]

    def update_project(
        self,
        user_id: str,
        project_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict | None:
        if self.get_project(user_id, project_id) is None:
            return None
        assignments = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[str] = []
        if title is not None:
            assignments.append("title = ?")
            params.append(title.strip() or "Untitled project")
        if description is not None:
            assignments.append("description = ?")
            params.append(description.strip())
        if status is not None:
            if status not in {"active", "archived", "deleted"}:
                return None
            assignments.append("status = ?")
            params.append(status)
        params.extend([user_id, project_id])
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE projects
                SET {", ".join(assignments)}
                WHERE user_id = ? AND id = ?
                """,
                params,
            )
        return self.get_project(user_id, project_id)

    def _project_payload(self, row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "title": str(row["title"]),
            "description": str(row["description"] or ""),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "chat_count": int(row["chat_count"] or 0),
        }

    def archive_chat_thread(self, user_id: str, thread_id: str, archived: bool = True) -> dict | None:
        if self.get_chat_thread(user_id, thread_id) is None:
            return None
        with self.connect() as connection:
            if archived:
                connection.execute(
                    """
                    UPDATE chat_threads
                    SET status = 'archived',
                        archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND id = ? AND deleted_at IS NULL
                    """,
                    (user_id, thread_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE chat_threads
                    SET status = 'active',
                        archived_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND id = ? AND deleted_at IS NULL
                    """,
                    (user_id, thread_id),
                )
        return self.get_chat_thread(user_id, thread_id)

    def soft_delete_chat_thread(self, user_id: str, thread_id: str) -> dict | None:
        if self.get_chat_thread(user_id, thread_id) is None:
            return None
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE chat_threads
                SET status = 'deleted',
                    deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND id = ?
                """,
                (user_id, thread_id),
            )
        return self.get_chat_thread(user_id, thread_id)

    def restore_chat_thread(self, user_id: str, thread_id: str) -> dict | None:
        if self.get_chat_thread(user_id, thread_id) is None:
            return None
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE chat_threads
                SET status = 'active',
                    archived_at = NULL,
                    deleted_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND id = ?
                """,
                (user_id, thread_id),
            )
        return self.get_chat_thread(user_id, thread_id)

    def clear_chat_messages(self, user_id: str, thread_id: str) -> int | None:
        if self.get_chat_thread(user_id, thread_id) is None:
            return None
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()["count"]
            connection.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            connection.execute(
                """
                UPDATE chat_threads
                SET updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND id = ?
                """,
                (user_id, thread_id),
            )
        return int(count)

    def auto_title_chat_thread(self, user_id: str, thread_id: str) -> dict | None:
        thread = self.get_chat_thread(user_id, thread_id)
        if thread is None or thread["title"] not in {"New chat", "Main conversation", "Untitled chat"}:
            return thread
        messages = self.list_messages(thread_id)
        first_user_message = next(
            (message["content"] for message in messages if message["role"] == "user" and message["content"]),
            "",
        )
        title = _title_from_message(first_user_message)
        if not title:
            return thread
        return self.update_chat_thread(user_id, thread_id, title=title)

    def _thread_payload(self, row: sqlite3.Row) -> dict:
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "archived_at": row["archived_at"],
            "deleted_at": row["deleted_at"],
            "pinned": bool(row["pinned"]),
            "project_id": row["project_id"],
            "memory_enabled": bool(row["memory_enabled"]),
            "memory_saved_at": row["memory_saved_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "message_count": int(row["message_count"] or 0),
            "last_message_at": row["last_message_at"],
        }

    def add_note(
        self,
        note: str,
        user_id: str = DEFAULT_USER_ID,
        project_id: str | None = None,
        source_thread_id: str | None = None,
    ) -> str:
        self.ensure_user_defaults(user_id)
        note_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notes (id, user_id, content, project_id, source_thread_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (note_id, user_id, note, project_id, source_thread_id),
            )
        return note_id

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

    def create_memory_record(
        self,
        user_id: str,
        *,
        kind: str,
        content: str,
        scope: str = "global",
        project_id: str | None = None,
        thread_id: str | None = None,
        memory_key: str | None = None,
        auto_generated: bool = False,
        confidence: float = 1.0,
        expires_at: str | None = None,
        sensitive: bool = False,
        consent_granted: bool = False,
        source_type: str = "user",
        source_id: str | None = None,
        source_uri: str | None = None,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        normalized_kind = _normalize_memory_kind(kind)
        normalized_scope = _normalize_memory_scope(scope)
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Memory content cannot be empty.")
        if not consent_granted:
            raise ValueError("Explicit consent is required before saving personal memory.")
        if normalized_scope == "project" and not project_id:
            raise ValueError("project_id is required for project memory.")
        if normalized_scope == "chat" and not thread_id:
            raise ValueError("thread_id is required for chat memory.")
        normalized_confidence = max(0.0, min(float(confidence), 1.0))
        normalized_expiry = _normalize_optional_datetime(expires_at)
        table = _memory_table(sensitive)
        store = "health" if sensitive else "personal"
        memory_id = str(uuid4())
        consented_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {table} (
                    id, user_id, kind, content, scope, project_id, thread_id,
                    memory_key, auto_generated, confidence, status, consent_status,
                    consented_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'granted', ?, ?)
                """,
                (
                    memory_id,
                    user_id,
                    normalized_kind,
                    normalized_content,
                    normalized_scope,
                    project_id if normalized_scope == "project" else None,
                    thread_id if normalized_scope == "chat" else None,
                    memory_key.strip() if memory_key else None,
                    int(bool(auto_generated)),
                    normalized_confidence,
                    consented_at,
                    normalized_expiry,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_provenance (
                    id, user_id, memory_id, memory_store, source_type, source_id, source_uri
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, memory_id, store, source_type.strip() or "user", source_id, source_uri),
            )
        record = self.get_memory_record(user_id, memory_id, sensitive=sensitive)
        if record is None:
            raise RuntimeError("Saved memory could not be loaded.")
        return record

    def get_memory_settings(self, user_id: str) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return {
            "autonomous_enabled": bool(row["autonomous_enabled"]),
            "health_autonomous_enabled": bool(row["health_autonomous_enabled"]),
            "updated_at": str(row["updated_at"]),
        }

    def update_memory_settings(
        self,
        user_id: str,
        *,
        autonomous_enabled: bool,
        health_autonomous_enabled: bool = False,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memory_settings
                SET autonomous_enabled = ?, health_autonomous_enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (int(autonomous_enabled), int(health_autonomous_enabled), user_id),
            )
        return self.get_memory_settings(user_id)

    def observe_memory_candidate(
        self,
        user_id: str,
        *,
        memory_key: str,
        kind: str,
        content: str,
        polarity: str,
        confidence: float,
        source_message_id: str,
        decision: str = "pending",
        reason: str = "",
    ) -> dict:
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM memory_candidates
                WHERE user_id = ? AND memory_key = ? AND polarity = ?
                """,
                (user_id, memory_key, polarity),
            ).fetchone()
            if existing is None:
                candidate_id = str(uuid4())
                source_ids = [source_message_id]
                connection.execute(
                    """
                    INSERT INTO memory_candidates (
                        id, user_id, memory_key, kind, content, polarity,
                        confidence, source_message_ids_json, decision, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id, user_id, memory_key, kind, content, polarity,
                        max(0.0, min(float(confidence), 1.0)),
                        json.dumps(source_ids), decision, reason,
                    ),
                )
            else:
                candidate_id = str(existing["id"])
                source_ids = list(json.loads(str(existing["source_message_ids_json"] or "[]")))
                if source_message_id not in source_ids:
                    source_ids.append(source_message_id)
                connection.execute(
                    """
                    UPDATE memory_candidates
                    SET content = ?, observation_count = observation_count + 1,
                        confidence = MAX(confidence, ?), source_message_ids_json = ?,
                        decision = ?, reason = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        content, max(0.0, min(float(confidence), 1.0)),
                        json.dumps(source_ids), decision, reason, candidate_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        return _memory_candidate_payload(row)

    def set_memory_candidate_decision(self, candidate_id: str, decision: str, reason: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memory_candidates
                SET decision = ?, reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (decision, reason, candidate_id),
            )

    def find_active_memory_by_key(self, user_id: str, memory_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_records
                WHERE user_id = ? AND memory_key = ? AND status = 'active'
                  AND consent_status = 'granted'
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (user_id, memory_key),
            ).fetchone()
            return self._memory_record_payload(connection, row, "personal", False) if row else None

    def add_memory_provenance(
        self,
        user_id: str,
        memory_id: str,
        *,
        source_type: str,
        source_id: str | None = None,
        source_uri: str | None = None,
    ) -> None:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM memory_records WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            ).fetchone()
            if not exists:
                raise ValueError("Memory record not found.")
            duplicate = connection.execute(
                """
                SELECT 1 FROM memory_provenance
                WHERE user_id = ? AND memory_store = 'personal' AND memory_id = ?
                  AND source_type = ? AND COALESCE(source_id, '') = COALESCE(?, '')
                """,
                (user_id, memory_id, source_type, source_id),
            ).fetchone()
            if not duplicate:
                connection.execute(
                    """
                    INSERT INTO memory_provenance (
                        id, user_id, memory_id, memory_store, source_type, source_id, source_uri
                    ) VALUES (?, ?, ?, 'personal', ?, ?, ?)
                    """,
                    (str(uuid4()), user_id, memory_id, source_type, source_id, source_uri),
                )

    def list_memory_records(
        self,
        user_id: str,
        *,
        include_sensitive: bool = False,
        include_inactive: bool = False,
    ) -> list[dict]:
        self._expire_due_memory(user_id)
        stores = [("memory_records", "personal", False)]
        if include_sensitive:
            stores.append(("health_memory_records", "health", True))
        records: list[dict] = []
        with self.connect() as connection:
            for table, store, sensitive in stores:
                clauses = ["user_id = ?"]
                params: list[object] = [user_id]
                if not include_inactive:
                    clauses.extend(["status = 'active'", "consent_status = 'granted'"])
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, rowid DESC",
                    params,
                ).fetchall()
                for row in rows:
                    records.append(self._memory_record_payload(connection, row, store, sensitive))
        records.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return records

    def search_memory_records(
        self,
        user_id: str,
        *,
        terms: list[str],
        project_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search consented non-sensitive memory with FTS and metadata scope filters."""
        self._expire_due_memory(user_id)
        normalized_terms = [
            term.casefold()
            for term in terms
            if re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9]+", term) and len(term) >= 3
        ]
        if not normalized_terms:
            return []
        safe_limit = max(1, min(int(limit), 200))
        scope_clauses = ["record.scope = 'global'"]
        scope_params: list[object] = []
        if project_id:
            scope_clauses.append("(record.scope = 'project' AND record.project_id = ?)")
            scope_params.append(project_id)
        if thread_id:
            scope_clauses.append("(record.scope = 'chat' AND record.thread_id = ?)")
            scope_params.append(thread_id)
        fts_query = " OR ".join(f'"{_memory_search_prefix(term)}"*' for term in normalized_terms)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT record.*, bm25(memory_records_fts) AS fts_rank
                FROM memory_records_fts
                JOIN memory_records AS record ON record.id = memory_records_fts.memory_id
                WHERE memory_records_fts MATCH ?
                  AND record.user_id = ?
                  AND record.status = 'active'
                  AND record.consent_status = 'granted'
                  AND ({' OR '.join(scope_clauses)})
                ORDER BY fts_rank ASC, record.updated_at DESC
                LIMIT ?
                """,
                [fts_query, user_id, *scope_params, safe_limit],
            ).fetchall()
            return [
                {
                    **self._memory_record_payload(connection, row, "personal", False),
                    "fts_rank": float(row["fts_rank"]),
                }
                for row in rows
            ]

    def get_memory_record(self, user_id: str, memory_id: str, *, sensitive: bool = False) -> dict | None:
        self._expire_due_memory(user_id)
        table = _memory_table(sensitive)
        store = "health" if sensitive else "personal"
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            ).fetchone()
            return self._memory_record_payload(connection, row, store, sensitive) if row else None

    def update_memory_record(
        self,
        user_id: str,
        memory_id: str,
        *,
        sensitive: bool = False,
        content: str | None = None,
        confidence: float | None = None,
        status: str | None = None,
        expires_at: str | None = None,
        clear_expiry: bool = False,
    ) -> dict | None:
        table = _memory_table(sensitive)
        existing = self.get_memory_record(user_id, memory_id, sensitive=sensitive)
        if existing is None:
            return None
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[object] = []
        if content is not None:
            normalized_content = content.strip()
            if not normalized_content:
                raise ValueError("Memory content cannot be empty.")
            updates.append("content = ?")
            params.append(normalized_content)
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(max(0.0, min(float(confidence), 1.0)))
        if status is not None:
            normalized_status = _normalize_memory_status(status)
            if normalized_status == "active" and existing["consent_status"] != "granted":
                raise ValueError("Revoked memory requires new explicit consent before activation.")
            updates.append("status = ?")
            params.append(normalized_status)
            if normalized_status == "revoked":
                updates.extend(["consent_status = 'revoked'", "revoked_at = ?"])
                params.append(datetime.now(timezone.utc).isoformat())
        if clear_expiry:
            updates.append("expires_at = NULL")
        elif expires_at is not None:
            updates.append("expires_at = ?")
            params.append(_normalize_optional_datetime(expires_at))
        params.extend([user_id, memory_id])
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {', '.join(updates)} WHERE user_id = ? AND id = ?",
                params,
            )
        if cursor.rowcount == 0:
            return None
        return self.get_memory_record(user_id, memory_id, sensitive=sensitive)

    def delete_memory_record(self, user_id: str, memory_id: str, *, sensitive: bool = False) -> int:
        table = _memory_table(sensitive)
        store = "health" if sensitive else "personal"
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            )
            if cursor.rowcount:
                connection.execute(
                    "DELETE FROM memory_provenance WHERE user_id = ? AND memory_store = ? AND memory_id = ?",
                    (user_id, store, memory_id),
                )
            return int(cursor.rowcount)

    def _expire_due_memory(self, user_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            for table in ("memory_records", "health_memory_records"):
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (user_id, now),
                )

    def _memory_record_payload(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        store: str,
        sensitive: bool,
    ) -> dict:
        provenance = _rows_to_dicts(
            connection.execute(
                """
                SELECT id, source_type, source_id, source_uri, created_at
                FROM memory_provenance
                WHERE user_id = ? AND memory_store = ? AND memory_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (str(row["user_id"]), store, str(row["id"])),
            ).fetchall()
        )
        return {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "content": str(row["content"]),
            "scope": str(row["scope"]),
            "project_id": row["project_id"],
            "thread_id": row["thread_id"],
            "memory_key": row["memory_key"],
            "auto_generated": bool(row["auto_generated"]),
            "confidence": float(row["confidence"]),
            "status": str(row["status"]),
            "consent_status": str(row["consent_status"]),
            "consented_at": row["consented_at"],
            "revoked_at": row["revoked_at"],
            "expires_at": row["expires_at"],
            "sensitive": sensitive,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "provenance": provenance,
        }

    def memory_overview(self, user_id: str = DEFAULT_USER_ID) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            note_rows = connection.execute(
                """
                SELECT id, content, project_id, source_thread_id, created_at
                FROM notes
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 50
                """,
                (user_id,),
            ).fetchall()
            counts = {
                "notes": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM notes WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "global_notes": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM notes
                        WHERE user_id = ? AND project_id IS NULL AND source_thread_id IS NULL
                        """,
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "project_notes": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM notes
                        WHERE user_id = ? AND project_id IS NOT NULL
                        """,
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "chat_notes": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM notes
                        WHERE user_id = ? AND source_thread_id IS NOT NULL
                        """,
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "memory_index": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM memory_index WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "chat_messages": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM messages
                        WHERE thread_id IN (SELECT id FROM chat_threads WHERE user_id = ?)
                        """,
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "tasks": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM tasks WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "action_logs": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM action_logs WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "personal_records": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM memory_records WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
                "health_records": int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM health_memory_records WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()["count"]
                ),
            }
        return {
            "counts": counts,
            "notes": [
                {
                    "id": str(row["id"]),
                    "content": str(row["content"]),
                    "scope": _note_scope_from_row(row),
                    "project_id": row["project_id"],
                    "source_thread_id": row["source_thread_id"],
                    "created_at": str(row["created_at"]),
                }
                for row in note_rows
            ],
        }

    def export_agent_memory(self, user_id: str = DEFAULT_USER_ID, *, include_sensitive: bool = False) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            user_row = connection.execute(
                """
                SELECT id, email, username, display_name, created_at, updated_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            notes = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, content, project_id, source_thread_id, created_at
                    FROM notes
                    WHERE user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            memory_index = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, source_type, source_id, content, embedding_json, created_at, updated_at
                    FROM memory_index
                    WHERE user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            chat_threads = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, title, status, archived_at, deleted_at, pinned, project_id,
                           memory_enabled, memory_saved_at, created_at, updated_at
                    FROM chat_threads
                    WHERE user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            messages = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT messages.id, messages.thread_id, messages.role, messages.content,
                           messages.name, messages.status, messages.error, messages.provider,
                           messages.model, messages.artifacts_json, messages.created_at
                    FROM messages
                    JOIN chat_threads ON chat_threads.id = messages.thread_id
                    WHERE chat_threads.user_id = ?
                    ORDER BY messages.created_at ASC, messages.rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            context_compactions = [
                _context_compaction_from_row(row)
                for row in connection.execute(
                    """
                    SELECT * FROM context_compactions
                    WHERE user_id = ?
                    ORDER BY thread_id ASC, cycle_number ASC
                    """,
                    (user_id,),
                ).fetchall()
            ]
            tasks = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, project_id, description, status, priority, result, created_at, updated_at
                    FROM tasks
                    WHERE user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            task_steps = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT task_steps.id, task_steps.task_id, task_steps.position, task_steps.description,
                           task_steps.status, task_steps.result, task_steps.created_at, task_steps.updated_at
                    FROM task_steps
                    JOIN tasks ON tasks.id = task_steps.task_id
                    WHERE tasks.user_id = ?
                    ORDER BY task_steps.created_at ASC, task_steps.rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )
            action_logs = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, tool_name, status, arguments_json, result, created_at
                    FROM action_logs
                    WHERE user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (user_id,),
                ).fetchall()
            )

        return {
            "format": "avelin-memory-export-v2",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "includes_sensitive_health_data": include_sensitive,
            "user": dict(user_row) if user_row else {"id": user_id},
            "overview": self.memory_overview(user_id),
            "memory_records": self.list_memory_records(
                user_id,
                include_sensitive=include_sensitive,
                include_inactive=True,
            ),
            "notes": notes,
            "memory_index": memory_index,
            "chat_threads": chat_threads,
            "messages": messages,
            "context_compactions": context_compactions,
            "tasks": tasks,
            "task_steps": task_steps,
            "action_logs": action_logs,
        }

    def delete_note(self, user_id: str, note_id: str) -> int:
        with self.connect() as connection:
            linked_ids = [
                str(row["memory_id"])
                for row in connection.execute(
                    """
                    SELECT memory_id FROM memory_provenance
                    WHERE user_id = ? AND memory_store = 'personal' AND source_id = ?
                    """,
                    (user_id, note_id),
                ).fetchall()
            ]
            cursor = connection.execute(
                "DELETE FROM notes WHERE user_id = ? AND id = ?",
                (user_id, note_id),
            )
            connection.execute(
                """
                DELETE FROM memory_index
                WHERE user_id = ? AND source_type = 'note' AND source_id = ?
                """,
                (user_id, note_id),
            )
            if linked_ids:
                placeholders = ",".join("?" for _ in linked_ids)
                connection.execute(
                    f"DELETE FROM memory_records WHERE user_id = ? AND id IN ({placeholders})",
                    [user_id, *linked_ids],
                )
                connection.execute(
                    f"DELETE FROM memory_provenance WHERE user_id = ? AND memory_store = 'personal' AND memory_id IN ({placeholders})",
                    [user_id, *linked_ids],
                )
            return int(cursor.rowcount)

    def clear_agent_memory(
        self,
        user_id: str,
        scope: str = "all",
        thread_id: str | None = None,
        project_id: str | None = None,
        include_action_logs: bool = False,
    ) -> dict[str, int]:
        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"all", "global", "current_chat", "current_project"}:
            normalized_scope = "all"

        deleted_notes = 0
        deleted_memory_index = 0
        deleted_action_logs = 0
        updated_threads = 0

        with self.connect() as connection:
            note_where = "user_id = ?"
            note_params: list[str] = [user_id]
            if normalized_scope == "global":
                note_where += " AND project_id IS NULL AND source_thread_id IS NULL"
            elif normalized_scope == "current_chat":
                if not thread_id:
                    return {
                        "deleted_notes": 0,
                        "deleted_memory_index": 0,
                        "deleted_action_logs": 0,
                        "updated_threads": 0,
                    }
                note_where += " AND source_thread_id = ?"
                note_params.append(thread_id)
            elif normalized_scope == "current_project":
                if not project_id:
                    return {
                        "deleted_notes": 0,
                        "deleted_memory_index": 0,
                        "deleted_action_logs": 0,
                        "updated_threads": 0,
                    }
                note_where += " AND project_id = ?"
                note_params.append(project_id)

            note_ids = [
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM notes WHERE {note_where}",
                    note_params,
                ).fetchall()
            ]
            if note_ids:
                placeholders = ",".join("?" for _ in note_ids)
                cursor = connection.execute(
                    f"DELETE FROM notes WHERE id IN ({placeholders}) AND user_id = ?",
                    [*note_ids, user_id],
                )
                deleted_notes = int(cursor.rowcount)
                cursor = connection.execute(
                    f"""
                    DELETE FROM memory_index
                    WHERE user_id = ? AND source_type = 'note' AND source_id IN ({placeholders})
                    """,
                    [user_id, *note_ids],
                )
                deleted_memory_index += int(cursor.rowcount)

            if normalized_scope == "all":
                cursor = connection.execute("DELETE FROM memory_index WHERE user_id = ?", (user_id,))
                deleted_memory_index += int(cursor.rowcount)

            memory_scope = {"global": "global", "current_chat": "chat", "current_project": "project"}.get(
                normalized_scope
            )
            for table, store in (("memory_records", "personal"), ("health_memory_records", "health")):
                record_params: list[object] = [user_id]
                record_where = "user_id = ?"
                if normalized_scope != "all" and memory_scope:
                    record_where += " AND scope = ?"
                    record_params.append(memory_scope)
                    if normalized_scope == "current_chat" and thread_id:
                        record_where += " AND thread_id = ?"
                        record_params.append(thread_id)
                    elif normalized_scope == "current_project" and project_id:
                        record_where += " AND project_id = ?"
                        record_params.append(project_id)
                record_ids = [
                    str(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM {table} WHERE {record_where}", record_params
                    ).fetchall()
                ]
                if record_ids:
                    placeholders = ",".join("?" for _ in record_ids)
                    connection.execute(
                        f"DELETE FROM {table} WHERE user_id = ? AND id IN ({placeholders})",
                        [user_id, *record_ids],
                    )
                    connection.execute(
                        f"DELETE FROM memory_provenance WHERE user_id = ? AND memory_store = ? AND memory_id IN ({placeholders})",
                        [user_id, store, *record_ids],
                    )

            if normalized_scope == "current_chat" and thread_id:
                cursor = connection.execute(
                    """
                    UPDATE chat_threads
                    SET memory_enabled = 0,
                        memory_saved_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND id = ?
                    """,
                    (user_id, thread_id),
                )
                updated_threads = int(cursor.rowcount)
            elif normalized_scope == "all":
                cursor = connection.execute(
                    """
                    UPDATE chat_threads
                    SET memory_enabled = 0,
                        memory_saved_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
                updated_threads = int(cursor.rowcount)

            if include_action_logs:
                cursor = connection.execute("DELETE FROM action_logs WHERE user_id = ?", (user_id,))
                deleted_action_logs = int(cursor.rowcount)

        return {
            "deleted_notes": deleted_notes,
            "deleted_memory_index": deleted_memory_index,
            "deleted_action_logs": deleted_action_logs,
            "updated_threads": updated_threads,
        }

    def list_note_items(
        self,
        user_id: str = DEFAULT_USER_ID,
        project_id: str | None = None,
        source_thread_id: str | None = None,
        include_global: bool = True,
    ) -> list[dict]:
        clauses = ["user_id = ?"]
        params: list[str] = [user_id]
        scope_clauses: list[str] = []
        if include_global:
            scope_clauses.append("(project_id IS NULL AND source_thread_id IS NULL)")
        if project_id:
            scope_clauses.append("project_id = ?")
            params.append(project_id)
        if source_thread_id:
            scope_clauses.append("source_thread_id = ?")
            params.append(source_thread_id)
        if scope_clauses:
            clauses.append(f"({' OR '.join(scope_clauses)})")
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, user_id, content, project_id, source_thread_id, created_at
                FROM notes
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "content": str(row["content"]),
                "project_id": row["project_id"],
                "source_thread_id": row["source_thread_id"],
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def remember_thread(self, user_id: str, thread_id: str) -> str | None:
        thread = self.get_chat_thread(user_id, thread_id)
        if thread is None or thread["deleted_at"] is not None:
            return None
        messages = self.list_messages(thread_id)
        user_messages = [
            str(message["content"]).strip()
            for message in messages
            if message["role"] == "user" and str(message["content"]).strip()
        ]
        if not user_messages:
            return ""
        title = str(thread["title"] or "Chat")
        summary = "; ".join(user_messages[-6:])
        content = f"Chat memory [{title}]: {summary}"
        note_id = self.add_note(
            content,
            user_id=user_id,
            project_id=thread["project_id"],
            source_thread_id=thread_id,
        )
        self.create_memory_record(
            user_id,
            kind="episode",
            content=content,
            scope="project" if thread["project_id"] else "chat",
            project_id=thread["project_id"],
            thread_id=thread_id,
            consent_granted=True,
            source_type="chat_summary",
            source_id=note_id,
        )
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE chat_threads
                SET memory_saved_at = CURRENT_TIMESTAMP,
                    memory_enabled = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND id = ?
                """,
                (user_id, thread_id),
            )
        return content

    def add_message(
        self,
        role: str,
        content: str,
        name: str | None = None,
        thread_id: str = DEFAULT_THREAD_ID,
        status: str = "complete",
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        artifacts: list[dict] | None = None,
    ) -> str:
        message_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    id, thread_id, role, content, name, status, error, provider, model, artifacts_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    role,
                    content,
                    name,
                    status,
                    error,
                    provider,
                    model,
                    json.dumps(artifacts or [], ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                UPDATE chat_threads
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (thread_id,),
            )
        return message_id

    def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        artifacts: list[dict] | None = None,
    ) -> None:
        assignments = []
        values: list[str | None] = []
        for column, value in (
            ("content", content),
            ("status", status),
            ("error", error),
            ("provider", provider),
            ("model", model),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        if artifacts is not None:
            assignments.append("artifacts_json = ?")
            values.append(json.dumps(artifacts, ensure_ascii=False))
        if not assignments:
            return
        values.append(message_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE messages SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def list_messages(self, thread_id: str = DEFAULT_THREAD_ID) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, name, status, error, provider, model,
                       artifacts_json, created_at
                FROM messages
                WHERE thread_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (thread_id,),
            ).fetchall()
            return [
                {
                    "id": str(row["id"]),
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "name": row["name"],
                    "status": str(row["status"] or "complete"),
                    "error": row["error"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "artifacts": _load_json_list(row["artifacts_json"]),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]

    def create_context_compaction(
        self,
        user_id: str,
        thread_id: str,
        *,
        summary: dict,
        source_message_ids: list[str],
        artifact_refs: list[str],
        source_hash: str,
    ) -> dict:
        compaction_id = str(uuid4())
        with self.connect() as connection:
            thread = connection.execute(
                """
                SELECT id FROM chat_threads
                WHERE id = ? AND user_id = ? AND deleted_at IS NULL
                """,
                (thread_id, user_id),
            ).fetchone()
            if thread is None:
                raise ValueError("Чат не найден или удален.")
            existing = connection.execute(
                """
                SELECT id FROM context_compactions
                WHERE thread_id = ? AND source_hash = ? AND status != 'rolled_back'
                """,
                (thread_id, source_hash),
            ).fetchone()
            if existing is not None:
                existing_id = str(existing["id"])
            else:
                existing_id = ""
                cycle_number = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(cycle_number), 0) + 1 AS value FROM context_compactions WHERE thread_id = ?",
                        (thread_id,),
                    ).fetchone()["value"]
                )
                connection.execute(
                    """
                    INSERT INTO context_compactions (
                        id, user_id, thread_id, cycle_number, status, summary_json,
                        source_message_ids_json, artifact_refs_json, source_hash
                    ) VALUES (?, ?, ?, ?, 'pending_review', ?, ?, ?, ?)
                    """,
                    (
                        compaction_id,
                        user_id,
                        thread_id,
                        cycle_number,
                        json.dumps(summary, ensure_ascii=False, sort_keys=True),
                        json.dumps(source_message_ids, ensure_ascii=False),
                        json.dumps(artifact_refs, ensure_ascii=False),
                        source_hash,
                    ),
                )
        return self.get_context_compaction(
            user_id,
            thread_id,
            existing_id or compaction_id,
        ) or {}

    def list_context_compactions(
        self,
        user_id: str,
        thread_id: str,
        *,
        include_rolled_back: bool = True,
    ) -> list[dict]:
        condition = "" if include_rolled_back else "AND status != 'rolled_back'"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM context_compactions
                WHERE user_id = ? AND thread_id = ? {condition}
                ORDER BY cycle_number ASC
                """,
                (user_id, thread_id),
            ).fetchall()
        return [_context_compaction_from_row(row) for row in rows]

    def get_context_compaction(
        self,
        user_id: str,
        thread_id: str,
        compaction_id: str,
    ) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM context_compactions
                WHERE id = ? AND user_id = ? AND thread_id = ?
                """,
                (compaction_id, user_id, thread_id),
            ).fetchone()
        return _context_compaction_from_row(row) if row is not None else None

    def set_context_compaction_status(
        self,
        user_id: str,
        thread_id: str,
        compaction_id: str,
        status: str,
    ) -> dict | None:
        if status not in {"verified", "rolled_back"}:
            raise ValueError("Неподдерживаемый статус compaction.")
        timestamp_column = "verified_at" if status == "verified" else "rolled_back_at"
        allowed_statuses = (
            ("pending_review",)
            if status == "verified"
            else ("pending_review", "verified")
        )
        placeholders = ",".join("?" for _ in allowed_statuses)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE context_compactions
                SET status = ?, {timestamp_column} = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND thread_id = ?
                  AND status IN ({placeholders})
                """,
                (status, compaction_id, user_id, thread_id, *allowed_statuses),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_context_compaction(user_id, thread_id, compaction_id)

    def context_compaction_sources(
        self,
        user_id: str,
        thread_id: str,
        compaction_id: str,
    ) -> list[dict] | None:
        compaction = self.get_context_compaction(user_id, thread_id, compaction_id)
        if compaction is None:
            return None
        source_ids = compaction["source_message_ids"]
        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, role, content, name, artifacts_json, created_at
                FROM messages
                WHERE thread_id = ? AND id IN ({placeholders})
                ORDER BY created_at ASC, rowid ASC
                """,
                [thread_id, *source_ids],
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
                "name": row["name"],
                "artifacts": _load_json_list(row["artifacts_json"]),
                "created_at": str(row["created_at"]),
            }
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
            connection.execute(
                """
                INSERT INTO task_runs (
                    id, task_id, user_id, state, plan_version, deduplication_key,
                    budget_json, usage_json
                ) VALUES (?, ?, ?, 'queued', 1, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    task_id,
                    user_id,
                    task_id,
                    json.dumps(_default_task_budget(), sort_keys=True),
                    json.dumps(_default_task_usage(), sort_keys=True),
                ),
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
                SELECT id, user_id, project_id, description, status, priority, result
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
                "project_id": row["project_id"],
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
                SELECT id, user_id, project_id, description, status, priority, result
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
                    "project_id": row["project_id"],
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

    def clear_task_result(self, task_id: str) -> dict | None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET result = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task_id,),
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

    def get_task_run(self, user_id: str, run_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            ).fetchone()
        return _task_run_payload(row) if row is not None else None

    def get_task_run_for_task(self, user_id: str, task_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_runs WHERE task_id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
        return _task_run_payload(row) if row is not None else None

    def list_task_runs(self, user_id: str, limit: int = 50) -> list[dict]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE user_id = ?
                ORDER BY
                    CASE state
                        WHEN 'running' THEN 1 WHEN 'waiting_approval' THEN 2
                        WHEN 'queued' THEN 3 WHEN 'paused' THEN 4 ELSE 5
                    END,
                    created_at ASC
                LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
        return [_task_run_payload(row) for row in rows]

    def acquire_task_run(self, user_id: str, worker_id: str, lease_seconds: int = 120) -> dict | None:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=max(5, min(int(lease_seconds), 900)))).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE task_runs
                SET state = 'queued', worker_id = NULL, lease_expires_at = NULL,
                    failure_reason = 'Recovered after expired worker lease.',
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND state = 'running'
                  AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (user_id, now.isoformat()),
            )
            row = connection.execute(
                """
                SELECT task_runs.id
                FROM task_runs
                JOIN tasks ON tasks.id = task_runs.task_id
                WHERE task_runs.user_id = ? AND task_runs.state = 'queued'
                ORDER BY tasks.priority ASC, task_runs.created_at ASC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            run_id = str(row["id"])
            updated = connection.execute(
                """
                UPDATE task_runs
                SET state = 'running', worker_id = ?, lease_expires_at = ?,
                    heartbeat_at = ?, started_at = COALESCE(started_at, ?),
                    failure_reason = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND state = 'queued'
                """,
                (worker_id, expires_at, now.isoformat(), now.isoformat(), run_id, user_id),
            )
            if updated.rowcount != 1:
                return None
            claimed = connection.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
        return _task_run_payload(claimed) if claimed is not None else None

    def heartbeat_task_run(
        self,
        user_id: str,
        run_id: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=max(5, min(int(lease_seconds), 900)))).isoformat()
        with self.connect() as connection:
            updated = connection.execute(
                """
                UPDATE task_runs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND state = 'running' AND worker_id = ?
                """,
                (now.isoformat(), expires_at, run_id, user_id, worker_id),
            )
            if updated.rowcount != 1:
                return None
        return self.get_task_run(user_id, run_id)

    def set_task_run_state(
        self,
        user_id: str,
        run_id: str,
        state: str,
        *,
        worker_id: str | None = None,
        failure_reason: str | None = None,
    ) -> dict | None:
        allowed = {"queued", "running", "waiting_approval", "paused", "failed", "completed", "cancelled"}
        if state not in allowed:
            raise ValueError(f"Unsupported durable run state: {state}")
        terminal = state in {"failed", "completed", "cancelled"}
        with self.connect() as connection:
            params: list[object] = [state, failure_reason]
            owner_clause = ""
            if worker_id is not None:
                owner_clause = " AND (worker_id = ? OR worker_id IS NULL)"
                params.append(worker_id)
            params.extend([run_id, user_id])
            updated = connection.execute(
                f"""
                UPDATE task_runs
                SET state = ?, failure_reason = ?,
                    worker_id = CASE WHEN ? IN ('running') THEN worker_id ELSE NULL END,
                    lease_expires_at = CASE WHEN ? IN ('running') THEN lease_expires_at ELSE NULL END,
                    finished_at = CASE WHEN ? THEN COALESCE(finished_at, CURRENT_TIMESTAMP) ELSE finished_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?{owner_clause}
                """,
                [state, failure_reason, state, state, 1 if terminal else 0, run_id, user_id]
                + ([worker_id] if worker_id is not None else []),
            )
            if updated.rowcount != 1:
                return None
        return self.get_task_run(user_id, run_id)

    def update_task_run_usage(
        self,
        user_id: str,
        run_id: str,
        *,
        steps: int = 0,
        failures: int = 0,
        tokens: int = 0,
        cost_micros: int = 0,
    ) -> dict | None:
        current = self.get_task_run(user_id, run_id)
        if current is None:
            return None
        usage = dict(current["usage"])
        for key, delta in {
            "steps": steps, "failures": failures, "tokens": tokens, "cost_micros": cost_micros,
        }.items():
            usage[key] = max(0, int(usage.get(key, 0)) + int(delta))
        with self.connect() as connection:
            connection.execute(
                "UPDATE task_runs SET usage_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (json.dumps(usage, sort_keys=True), run_id, user_id),
            )
        return self.get_task_run(user_id, run_id)

    def create_task_checkpoint(
        self,
        user_id: str,
        *,
        run_id: str,
        task_id: str,
        step_id: str | None,
        plan_version: int,
        status: str,
        risk: str,
        tool_name: str | None,
        arguments_digest: str | None,
        deduplication_key: str,
        tool_call: dict,
        observation: str = "",
        artifacts: list[dict] | None = None,
        evidence: dict | None = None,
        next_action: str | None = None,
    ) -> dict:
        run = self.get_task_run(user_id, run_id)
        if run is None or run["task_id"] != task_id:
            raise ValueError("Durable task run not found.")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM task_checkpoints WHERE run_id = ? AND deduplication_key = ?",
                (run_id, deduplication_key),
            ).fetchone()
            if existing is not None:
                return _task_checkpoint_payload(existing)
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM task_checkpoints WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["value"]
            )
            checkpoint_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO task_checkpoints (
                    id, run_id, task_id, step_id, sequence, plan_version, status, risk,
                    tool_name, arguments_digest, deduplication_key, tool_call_json,
                    observation, artifacts_json, evidence_json, next_action
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id, run_id, task_id, step_id, sequence, plan_version, status, risk,
                    tool_name, arguments_digest, deduplication_key,
                    json.dumps(tool_call, ensure_ascii=False, sort_keys=True),
                    observation,
                    json.dumps(artifacts or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True),
                    next_action,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_checkpoints WHERE id = ?", (checkpoint_id,)
            ).fetchone()
        return _task_checkpoint_payload(row)

    def update_task_checkpoint(
        self,
        user_id: str,
        checkpoint_id: str,
        *,
        status: str,
        observation: str,
        artifacts: list[dict] | None = None,
        evidence: dict | None = None,
        next_action: str | None = None,
    ) -> dict | None:
        with self.connect() as connection:
            updated = connection.execute(
                """
                UPDATE task_checkpoints
                SET status = ?, observation = ?, artifacts_json = ?, evidence_json = ?,
                    next_action = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id IN (SELECT id FROM task_runs WHERE user_id = ?)
                """,
                (
                    status, observation,
                    json.dumps(artifacts or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True),
                    next_action, checkpoint_id, user_id,
                ),
            )
            if updated.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM task_checkpoints WHERE id = ?", (checkpoint_id,)
            ).fetchone()
        return _task_checkpoint_payload(row) if row is not None else None

    def find_task_checkpoint(self, user_id: str, run_id: str, deduplication_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT checkpoint.* FROM task_checkpoints AS checkpoint
                JOIN task_runs AS run ON run.id = checkpoint.run_id
                WHERE checkpoint.run_id = ? AND checkpoint.deduplication_key = ? AND run.user_id = ?
                """,
                (run_id, deduplication_key, user_id),
            ).fetchone()
        return _task_checkpoint_payload(row) if row is not None else None

    def list_task_checkpoints(self, user_id: str, run_id: str, limit: int = 100) -> list[dict]:
        safe_limit = max(1, min(int(limit), 500))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT checkpoint.* FROM task_checkpoints AS checkpoint
                JOIN task_runs AS run ON run.id = checkpoint.run_id
                WHERE checkpoint.run_id = ? AND run.user_id = ?
                ORDER BY checkpoint.sequence ASC LIMIT ?
                """,
                (run_id, user_id, safe_limit),
            ).fetchall()
        return [_task_checkpoint_payload(row) for row in rows]

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

    def create_approval_request(
        self,
        user_id: str,
        *,
        tool_name: str,
        arguments_digest: str,
        arguments: dict,
        scope: dict,
        risk: str,
        expires_at: str,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE user_id = ? AND tool_name = ? AND arguments_digest = ?
                  AND status IN ('pending', 'approved')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, tool_name, arguments_digest),
            ).fetchone()
            if existing is not None and not _approval_is_expired(existing):
                return _approval_request_payload(existing)
            approval_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO approval_requests (
                    id, user_id, task_id, step_id, tool_name, arguments_digest,
                    arguments_json, scope_json, risk, status, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    approval_id,
                    user_id,
                    task_id,
                    step_id,
                    tool_name,
                    arguments_digest,
                    json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                    json.dumps(scope, ensure_ascii=False, sort_keys=True),
                    risk,
                    expires_at,
                ),
            )
        return self.get_approval_request(user_id, approval_id) or {}

    def get_approval_request(self, user_id: str, approval_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ? AND user_id = ?",
                (approval_id, user_id),
            ).fetchone()
            if row is None:
                return None
            if _approval_is_expired(row) and str(row["status"]) in {"pending", "approved"}:
                connection.execute(
                    "UPDATE approval_requests SET status = 'expired', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (approval_id,),
                )
                row = connection.execute(
                    "SELECT * FROM approval_requests WHERE id = ?", (approval_id,)
                ).fetchone()
        return _approval_request_payload(row) if row is not None else None

    def list_approval_requests(
        self,
        user_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        self.ensure_user_defaults(user_id)
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
        items = []
        for row in rows:
            item = self.get_approval_request(user_id, str(row["id"]))
            if item is not None and (status is None or item["status"] == status):
                items.append(item)
        return items

    def decide_approval_request(self, user_id: str, approval_id: str, decision: str) -> dict | None:
        if decision not in {"approved", "denied", "revoked"}:
            raise ValueError("Unsupported approval decision.")
        current = self.get_approval_request(user_id, approval_id)
        if current is None:
            return None
        allowed = {"pending"} if decision in {"approved", "denied"} else {"pending", "approved"}
        if current["status"] not in allowed:
            raise ValueError(f"Approval request is already {current['status']}.")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = CURRENT_TIMESTAMP,
                    approved_at = CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE approved_at END
                WHERE id = ? AND user_id = ?
                """,
                (decision, decision, approval_id, user_id),
            )
        return self.get_approval_request(user_id, approval_id)

    def consume_approval_request(
        self,
        user_id: str,
        *,
        tool_name: str,
        arguments_digest: str,
        step_id: str | None = None,
    ) -> dict | None:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE user_id = ? AND tool_name = ? AND status = 'approved'
                ORDER BY approved_at DESC, created_at DESC
                """,
                (user_id, tool_name),
            ).fetchall()
            for row in rows:
                if _approval_is_expired(row):
                    connection.execute(
                        "UPDATE approval_requests SET status = 'expired', decided_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (row["id"],),
                    )
                    continue
                if str(row["arguments_digest"]) != arguments_digest:
                    continue
                if step_id and row["step_id"] and str(row["step_id"]) != step_id:
                    continue
                connection.execute(
                    "UPDATE approval_requests SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["id"],),
                )
                updated = connection.execute(
                    "SELECT * FROM approval_requests WHERE id = ?", (row["id"],)
                ).fetchone()
                return _approval_request_payload(updated)
        return None

    def approval_for_step(self, user_id: str, step_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE user_id = ? AND step_id = ? AND status IN ('pending', 'approved')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, step_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_approval_request(user_id, str(row["id"]))

    def security_settings(self, user_id: str) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT emergency_stop, updated_at FROM security_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return {
            "emergency_stop": bool(row["emergency_stop"]),
            "updated_at": str(row["updated_at"]),
        }

    def set_emergency_stop(self, user_id: str, enabled: bool) -> dict:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE security_settings
                SET emergency_stop = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (int(enabled), user_id),
            )
            if enabled:
                connection.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'revoked', decided_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND status IN ('pending', 'approved')
                    """,
                    (user_id,),
                )
                connection.execute(
                    """
                    UPDATE permission_grants
                    SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND status = 'active'
                    """,
                    (user_id,),
                )
        return self.security_settings(user_id)

    def create_permission_grant(
        self,
        user_id: str,
        *,
        permission: str,
        scope: dict,
        expires_at: str,
    ) -> dict:
        self.ensure_user_defaults(user_id)
        grant_id = str(uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO permission_grants (id, user_id, permission, scope_json, status, expires_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (grant_id, user_id, permission, json.dumps(scope, ensure_ascii=False, sort_keys=True), expires_at),
            )
        return self.get_permission_grant(user_id, grant_id) or {}

    def get_permission_grant(self, user_id: str, grant_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM permission_grants WHERE id = ? AND user_id = ?",
                (grant_id, user_id),
            ).fetchone()
            if row is None:
                return None
            if _grant_is_expired(row) and str(row["status"]) == "active":
                connection.execute(
                    "UPDATE permission_grants SET status = 'expired' WHERE id = ?",
                    (grant_id,),
                )
                row = connection.execute("SELECT * FROM permission_grants WHERE id = ?", (grant_id,)).fetchone()
        return _permission_grant_payload(row) if row is not None else None

    def list_permission_grants(self, user_id: str, *, active_only: bool = False) -> list[dict]:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM permission_grants WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        items = [self.get_permission_grant(user_id, str(row["id"])) for row in rows]
        return [item for item in items if item is not None and (not active_only or item["status"] == "active")]

    def revoke_permission_grant(self, user_id: str, grant_id: str) -> dict | None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE permission_grants SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (grant_id, user_id),
            )
        return self.get_permission_grant(user_id, grant_id)

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
    status TEXT NOT NULL DEFAULT 'active',
    archived_at TEXT,
    deleted_at TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    memory_saved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'complete',
    error TEXT,
    provider TEXT,
    model TEXT,
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_compactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    cycle_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review',
    summary_json TEXT NOT NULL,
    source_message_ids_json TEXT NOT NULL,
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT NOT NULL,
    verified_at TEXT,
    rolled_back_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thread_id, cycle_number)
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    project_id TEXT,
    source_thread_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    project_id TEXT,
    thread_id TEXT,
    memory_key TEXT,
    auto_generated INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    consent_status TEXT NOT NULL DEFAULT 'granted',
    consented_at TEXT,
    revoked_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS health_memory_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    project_id TEXT,
    thread_id TEXT,
    memory_key TEXT,
    auto_generated INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    consent_status TEXT NOT NULL DEFAULT 'granted',
    consented_at TEXT,
    revoked_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_provenance (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL,
    memory_store TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    source_uri TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    autonomous_enabled INTEGER NOT NULL DEFAULT 0,
    health_autonomous_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'positive',
    observation_count INTEGER NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 0.5,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    decision TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, memory_key, polarity)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5(
    memory_id UNINDEXED,
    user_id UNINDEXED,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS memory_records_fts_insert
AFTER INSERT ON memory_records BEGIN
    INSERT INTO memory_records_fts (memory_id, user_id, content)
    VALUES (new.id, new.user_id, replace(replace(new.content, 'ё', 'е'), 'Ё', 'Е'));
END;

CREATE TRIGGER IF NOT EXISTS memory_records_fts_update
AFTER UPDATE OF content, user_id ON memory_records BEGIN
    DELETE FROM memory_records_fts WHERE memory_id = old.id;
    INSERT INTO memory_records_fts (memory_id, user_id, content)
    VALUES (new.id, new.user_id, replace(replace(new.content, 'ё', 'е'), 'Ё', 'Е'));
END;

CREATE TRIGGER IF NOT EXISTS memory_records_fts_delete
AFTER DELETE ON memory_records BEGIN
    DELETE FROM memory_records_fts WHERE memory_id = old.id;
END;

CREATE TABLE IF NOT EXISTS memory_index (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, source_type, source_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id TEXT,
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

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'queued',
    worker_id TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    plan_version INTEGER NOT NULL DEFAULT 1,
    deduplication_key TEXT NOT NULL UNIQUE,
    budget_json TEXT NOT NULL DEFAULT '{}',
    usage_json TEXT NOT NULL DEFAULT '{}',
    failure_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id TEXT REFERENCES task_steps(id) ON DELETE SET NULL,
    sequence INTEGER NOT NULL,
    plan_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    risk TEXT NOT NULL DEFAULT 'read',
    tool_name TEXT,
    arguments_digest TEXT,
    deduplication_key TEXT NOT NULL,
    tool_call_json TEXT NOT NULL DEFAULT '{}',
    observation TEXT NOT NULL DEFAULT '',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    next_action TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, deduplication_key)
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

CREATE TABLE IF NOT EXISTS security_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    emergency_stop INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id TEXT,
    step_id TEXT,
    tool_name TEXT NOT NULL,
    arguments_digest TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    scope_json TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    approved_at TEXT,
    decided_at TEXT,
    consumed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS permission_grants (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
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
CREATE INDEX IF NOT EXISTS idx_context_compactions_thread_id ON context_compactions(thread_id, cycle_number);
CREATE INDEX IF NOT EXISTS idx_context_compactions_status ON context_compactions(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_records_user_status ON memory_records(user_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_records_scope ON memory_records(user_id, scope, project_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_health_memory_records_user_status ON health_memory_records(user_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_provenance_memory ON memory_provenance(user_id, memory_store, memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_user_decision ON memory_candidates(user_id, decision, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_index_user_id ON memory_index(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_index_source ON memory_index(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_queue ON task_runs(user_id, state, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_task_checkpoints_run ON task_checkpoints(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_task_checkpoints_step ON task_checkpoints(task_id, step_id, status);
CREATE INDEX IF NOT EXISTS idx_action_logs_user_id ON action_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_approvals_user_status ON approval_requests(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_step ON approval_requests(user_id, step_id, status);
CREATE INDEX IF NOT EXISTS idx_permission_grants_user_status ON permission_grants(user_id, status, expires_at);
"""


def _memory_search_prefix(term: str) -> str:
    if len(term) <= 4:
        return term
    return term[:5] if term.isascii() else term[:4]


def _approval_request_payload(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "task_id": row["task_id"],
        "step_id": row["step_id"],
        "tool_name": str(row["tool_name"]),
        "arguments_digest": str(row["arguments_digest"]),
        "arguments": _load_json_dict(row["arguments_json"]),
        "scope": _load_json_dict(row["scope_json"]),
        "risk": str(row["risk"]),
        "status": str(row["status"]),
        "expires_at": str(row["expires_at"]),
        "approved_at": row["approved_at"],
        "decided_at": row["decided_at"],
        "consumed_at": row["consumed_at"],
        "created_at": str(row["created_at"]),
    }


def _permission_grant_payload(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "permission": str(row["permission"]),
        "scope": _load_json_dict(row["scope_json"]),
        "status": str(row["status"]),
        "expires_at": str(row["expires_at"]),
        "revoked_at": row["revoked_at"],
        "created_at": str(row["created_at"]),
    }


def _default_task_budget() -> dict[str, int]:
    return {
        "max_steps": 50,
        "max_failures": 3,
        "max_seconds": 3600,
        "max_tokens": 100000,
        "max_cost_micros": 0,
    }


def _default_task_usage() -> dict[str, int]:
    return {"steps": 0, "failures": 0, "tokens": 0, "cost_micros": 0}


def _task_run_payload(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "task_id": str(row["task_id"]),
        "user_id": str(row["user_id"]),
        "state": str(row["state"]),
        "worker_id": row["worker_id"],
        "lease_expires_at": row["lease_expires_at"],
        "heartbeat_at": row["heartbeat_at"],
        "plan_version": int(row["plan_version"]),
        "deduplication_key": str(row["deduplication_key"]),
        "budget": {**_default_task_budget(), **_load_json_dict(row["budget_json"])},
        "usage": {**_default_task_usage(), **_load_json_dict(row["usage_json"])},
        "failure_reason": row["failure_reason"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _task_checkpoint_payload(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "task_id": str(row["task_id"]),
        "step_id": row["step_id"],
        "sequence": int(row["sequence"]),
        "plan_version": int(row["plan_version"]),
        "status": str(row["status"]),
        "risk": str(row["risk"]),
        "tool_name": row["tool_name"],
        "arguments_digest": row["arguments_digest"],
        "deduplication_key": str(row["deduplication_key"]),
        "tool_call": _load_json_dict(row["tool_call_json"]),
        "observation": str(row["observation"] or ""),
        "artifacts": _load_json_list(row["artifacts_json"]),
        "evidence": _load_json_dict(row["evidence_json"]),
        "next_action": row["next_action"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _approval_is_expired(row: sqlite3.Row) -> bool:
    return _is_past_datetime(str(row["expires_at"]))


def _grant_is_expired(row: sqlite3.Row) -> bool:
    return _is_past_datetime(str(row["expires_at"]))


def _is_past_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _memory_candidate_payload(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "memory_key": str(row["memory_key"]),
        "kind": str(row["kind"]),
        "content": str(row["content"]),
        "polarity": str(row["polarity"]),
        "observation_count": int(row["observation_count"]),
        "confidence": float(row["confidence"]),
        "source_message_ids": list(json.loads(str(row["source_message_ids_json"] or "[]"))),
        "decision": str(row["decision"]),
        "reason": str(row["reason"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _title_from_message(content: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        return ""
    title = normalized[:48].rstrip(".,!?;:")
    return f"{title}..." if len(normalized) > len(title) else title


def _memory_table(sensitive: bool) -> str:
    return "health_memory_records" if sensitive else "memory_records"


def _normalize_memory_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    if normalized not in {"profile", "preference", "episode", "fact"}:
        raise ValueError("Unsupported memory kind.")
    return normalized


def _normalize_memory_scope(scope: str) -> str:
    normalized = scope.strip().lower()
    aliases = {"current_project": "project", "current_chat": "chat"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"global", "project", "chat"}:
        raise ValueError("Unsupported memory scope.")
    return normalized


def _normalize_memory_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in {"active", "superseded", "revoked", "expired"}:
        raise ValueError("Unsupported memory status.")
    return normalized


def _normalize_optional_datetime(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO 8601 datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _context_compaction_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "thread_id": str(row["thread_id"]),
        "cycle_number": int(row["cycle_number"]),
        "status": str(row["status"]),
        "summary": _load_json_dict(row["summary_json"]),
        "source_message_ids": _load_json_string_list(row["source_message_ids_json"]),
        "artifact_refs": _load_json_string_list(row["artifact_refs_json"]),
        "source_hash": str(row["source_hash"]),
        "verified_at": row["verified_at"],
        "rolled_back_at": row["rolled_back_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _note_scope_from_row(row: sqlite3.Row) -> str:
    if row["source_thread_id"]:
        return "current_chat"
    if row["project_id"]:
        return "current_project"
    return "global"


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def _load_json_list(raw_value) -> list[dict]:
    try:
        value = json.loads(str(raw_value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _load_json_string_list(raw_value) -> list[str]:
    try:
        value = json.loads(str(raw_value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _load_json_dict(raw_value) -> dict:
    try:
        value = json.loads(str(raw_value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
