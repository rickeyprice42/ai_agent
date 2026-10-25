from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import logging
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.database import AvelinDatabase
from ai_agent.memory import MemoryStore
from backend.main import app
from backend.service import AgentService


def temporary_service(database_file: Path) -> AgentService:
    service = AgentService.__new__(AgentService)
    service.root_dir = ROOT
    service.settings = None
    service.database = AvelinDatabase(database_file)
    service._agents = {}
    return service


class PersonalMemoryDatabaseTests(unittest.TestCase):
    def test_explicit_remember_tool_dual_writes_lifecycle_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = MemoryStore(root / "memory.sqlite3", user_id="alice")
            self.assertEqual(memory.add_note("Любит тёмную тему"), "Запомнил: Любит тёмную тему")
            records = memory.database.list_memory_records("alice")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["content"], "Любит тёмную тему")
            self.assertEqual(records[0]["provenance"][0]["source_type"], "remember_note_tool")

    def test_consent_crud_provenance_ttl_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "memory.sqlite3")
            database.ensure_user_defaults("alice")
            database.ensure_user_defaults("bob")

            with self.assertRaisesRegex(ValueError, "Explicit consent"):
                database.create_memory_record(
                    "alice", kind="fact", content="never stored", consent_granted=False
                )

            record = database.create_memory_record(
                "alice",
                kind="preference",
                content="Любит краткие ответы",
                confidence=0.9,
                consent_granted=True,
                source_type="chat_message",
                source_id="message-42",
            )
            self.assertEqual(record["provenance"][0]["source_id"], "message-42")
            self.assertIsNone(database.get_memory_record("bob", record["id"]))
            self.assertIsNone(
                database.update_memory_record("bob", record["id"], content="чужая запись")
            )
            self.assertEqual(database.delete_memory_record("bob", record["id"]), 0)

            updated = database.update_memory_record(
                "alice", record["id"], content="Любит точные краткие ответы", confidence=0.95
            )
            self.assertEqual(updated["content"], "Любит точные краткие ответы")
            self.assertEqual(updated["confidence"], 0.95)
            revoked = database.update_memory_record("alice", record["id"], status="revoked")
            self.assertEqual(revoked["consent_status"], "revoked")
            self.assertNotIn(record["id"], {item["id"] for item in database.list_memory_records("alice")})
            with self.assertRaisesRegex(ValueError, "new explicit consent"):
                database.update_memory_record("alice", record["id"], status="active")

            expired = database.create_memory_record(
                "alice",
                kind="episode",
                content="Устаревшая встреча",
                consent_granted=True,
                expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            )
            active_ids = {item["id"] for item in database.list_memory_records("alice")}
            self.assertNotIn(expired["id"], active_ids)
            self.assertEqual(database.get_memory_record("alice", expired["id"])["status"], "expired")

    def test_health_memory_is_excluded_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "memory.sqlite3")
            database.ensure_user_defaults("alice")
            health = database.create_memory_record(
                "alice",
                kind="fact",
                content="Чувствительный медицинский факт",
                sensitive=True,
                consent_granted=True,
                source_type="user",
            )
            self.assertEqual(database.list_memory_records("alice"), [])
            self.assertEqual(database.list_memory_records("alice", include_sensitive=True)[0]["id"], health["id"])
            self.assertNotIn(
                "Чувствительный медицинский факт",
                str(database.export_agent_memory("alice")),
            )
            self.assertIn(
                "Чувствительный медицинский факт",
                str(database.export_agent_memory("alice", include_sensitive=True)),
            )


class PersonalMemoryApiTests(unittest.TestCase):
    def test_crud_requires_consent_and_logs_do_not_contain_content(self):
        with tempfile.TemporaryDirectory() as directory:
            service = temporary_service(Path(directory) / "api.sqlite3")
            with patch("backend.routes.agent_service", service):
                client = TestClient(app)
                registered = client.post(
                    "/api/auth/register",
                    json={
                        "email": "memory@example.com",
                        "username": "memory-user",
                        "password": "password123",
                        "display_name": "Memory Test",
                    },
                )
                headers = {"Authorization": f"Bearer {registered.json()['token']}"}
                secret = "PRIVATE-MEMORY-CONTENT-9f834"

                denied = client.post(
                    "/api/memory/records",
                    headers=headers,
                    json={"kind": "fact", "content": secret, "consent_granted": False},
                )
                self.assertEqual(denied.status_code, 422)

                output = StringIO()
                handler = logging.StreamHandler(output)
                logger = logging.getLogger("avelin.api")
                logger.addHandler(handler)
                try:
                    created = client.post(
                        "/api/memory/records",
                        headers=headers,
                        json={
                            "kind": "profile",
                            "content": secret,
                            "consent_granted": True,
                            "source_type": "manual_ui",
                        },
                    )
                finally:
                    logger.removeHandler(handler)
                self.assertEqual(created.status_code, 200)
                self.assertNotIn(secret, output.getvalue())

                memory_id = created.json()["id"]
                listed = client.get("/api/memory/records", headers=headers)
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()[0]["provenance"][0]["source_type"], "manual_ui")

                updated = client.patch(
                    f"/api/memory/records/{memory_id}",
                    headers=headers,
                    json={"content": "Обновлённый профиль"},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["content"], "Обновлённый профиль")

                deleted = client.delete(f"/api/memory/records/{memory_id}", headers=headers)
                self.assertEqual(deleted.status_code, 204)
                self.assertEqual(client.get("/api/memory/records", headers=headers).json(), [])


if __name__ == "__main__":
    unittest.main()
