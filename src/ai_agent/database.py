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
            self._migrate_chat_threads(connection)
            self._migrate_project_links(connection)
            self._ensure_migrated_indexes(connection)
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
            (user_id, "mock", "mock-local"),
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

        where = "user_id = ?"
        params: list[str] = [user_id]
        if status == "active":
            where += " AND deleted_at IS NULL AND archived_at IS NULL AND status = 'active'"
        elif status == "archived":
            where += " AND deleted_at IS NULL AND (archived_at IS NOT NULL OR status = 'archived')"
        elif status == "deleted":
            where += " AND deleted_at IS NOT NULL"
        if project_id:
            where += " AND project_id = ?"
            params.append(project_id)
        elif unassigned:
            where += " AND project_id IS NULL"

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
    ) -> None:
        self.ensure_user_defaults(user_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notes (id, user_id, content, project_id, source_thread_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, note, project_id, source_thread_id),
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
        self.add_note(
            content,
            user_id=user_id,
            project_id=thread["project_id"],
            source_thread_id=thread_id,
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
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid4()), thread_id, role, content, name),
            )
            connection.execute(
                """
                UPDATE chat_threads
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (thread_id,),
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    project_id TEXT,
    source_thread_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
CREATE INDEX IF NOT EXISTS idx_memory_index_user_id ON memory_index(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_index_source ON memory_index(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_action_logs_user_id ON action_logs(user_id);
"""


def _title_from_message(content: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        return ""
    title = normalized[:48].rstrip(".,!?;:")
    return f"{title}..." if len(normalized) > len(title) else title
