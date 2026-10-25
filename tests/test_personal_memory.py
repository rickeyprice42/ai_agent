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

    def test_retrieval_answers_twenty_old_fact_queries_with_sources(self):
        cases = [
            ("Какой соус мне нравится?", "Любит острый соус", "sauce"),
            ("Где я учился?", "Учился в техническом университете", "education"),
            ("В каком городе я живу?", "Живёт в Иркутске", "city"),
            ("Какая у меня видеокарта?", "Использует видеокарту GTX 1650 Ti", "gpu"),
            ("Сколько оперативной памяти?", "На компьютере установлено 24 ГБ оперативной памяти", "ram"),
            ("Какая операционная система?", "Основная операционная система Windows 10", "os"),
            ("Какой формат отчётов предпочитаю?", "Предпочитает краткие проверяемые отчёты", "reports"),
            ("Можно ли удалять исходники?", "Исходные файлы нельзя удалять без разрешения", "files"),
            ("Где я работаю?", "Работает в ОАО РЖД", "work"),
            ("Какая специальность была раньше?", "Ранее учился по специальности Приборостроение", "degree"),
            ("Какие игры мне нравятся?", "Любимые игры Brawl Stars и Grand Theft Auto", "games"),
            ("Какой накопитель установлен?", "Компьютер использует SSD NVMe", "storage"),
            ("Какой процессор у ноутбука?", "Процессор Intel Core i5-10300H", "cpu"),
            ("Какой офис нужен для документов?", "Рабочие документы должны поддерживать Office 2007", "office"),
            ("Какой стиль интерфейса нравится?", "Предпочитает тёмный современный интерфейс", "ui"),
            ("Как обращаться к пользователю?", "Псевдоним пользователя Rickey", "name"),
            ("Чем занимается проект Avelin?", "Avelin является персональным AI агентом", "project"),
            ("Как проверять Excel цвета?", "Для Excel учитывать отображаемое условное форматирование", "excel"),
            ("Где хранить результаты?", "Результаты сохранять в отдельной папке", "output"),
            ("Нужны ли хэши артефактов?", "Для готовых артефактов указывать SHA256 хэш", "hash"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "retrieval.sqlite3", user_id="alice")
            expected: dict[str, str] = {}
            for _, content, source_id in cases:
                record = memory.database.create_memory_record(
                    "alice",
                    kind="fact",
                    content=content,
                    confidence=0.9,
                    consent_granted=True,
                    source_type="chat_message",
                    source_id=source_id,
                )
                expected[source_id] = record["id"]

            for query, _, source_id in cases:
                with self.subTest(query=query):
                    results = memory.relevant_memories(query, limit=3)
                    self.assertTrue(results)
                    by_id = {item.memory_id: item for item in results}
                    self.assertIn(expected[source_id], by_id)
                    self.assertIn(
                        f"chat_message:{source_id}",
                        by_id[expected[source_id]].provenance,
                    )

    def test_retrieval_reports_no_result_metrics_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "retrieval.sqlite3", user_id="alice")
            positive = memory.database.create_memory_record(
                "alice",
                kind="preference",
                content="Любит острый соус",
                consent_granted=True,
                source_type="chat_message",
                source_id="old-message",
            )
            negative = memory.database.create_memory_record(
                "alice",
                kind="preference",
                content="Не любит острый соус",
                consent_granted=True,
                source_type="manual_ui",
                source_id="new-setting",
            )

            conflicts = memory.relevant_memories("Какой острый соус мне нравится?", limit=5)
            by_id = {item.memory_id: item for item in conflicts}
            self.assertIn(negative["id"], by_id[positive["id"]].conflict_ids)
            self.assertIn(positive["id"], by_id[negative["id"]].conflict_ids)
            self.assertGreaterEqual(memory.last_retrieval_trace()["conflict_count"], 2)

            self.assertEqual(memory.relevant_memories("квантовая телепортация", limit=5), [])
            trace = memory.last_retrieval_trace()
            self.assertEqual(trace["result_count"], 0)
            self.assertEqual(trace["estimated_tokens"], 0)
            self.assertGreaterEqual(trace["latency_ms"], 0)

    def test_fts_respects_scope_lifecycle_updates_and_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "retrieval.sqlite3")
            database.ensure_user_defaults("alice")
            project = database.create_memory_record(
                "alice",
                kind="fact",
                content="Проект использует сиреневый логотип",
                scope="project",
                project_id="project-a",
                consent_granted=True,
            )
            revoked = database.create_memory_record(
                "alice",
                kind="fact",
                content="Старый сиреневый черновик",
                consent_granted=True,
            )
            database.update_memory_record("alice", revoked["id"], status="revoked")

            self.assertEqual(
                database.search_memory_records("alice", terms=["сирен"], project_id=None),
                [],
            )
            visible = database.search_memory_records(
                "alice", terms=["сирен"], project_id="project-a"
            )
            self.assertEqual([item["id"] for item in visible], [project["id"]])

            database.update_memory_record(
                "alice", project["id"], content="Проект использует зелёный логотип"
            )
            self.assertEqual(
                database.search_memory_records(
                    "alice", terms=["сирен"], project_id="project-a"
                ),
                [],
            )
            self.assertEqual(
                database.search_memory_records(
                    "alice", terms=["зелен"], project_id="project-a"
                )[0]["id"],
                project["id"],
            )
            database.delete_memory_record("alice", project["id"])
            self.assertEqual(
                database.search_memory_records(
                    "alice", terms=["зелен"], project_id="project-a"
                ),
                [],
            )


class PersonalMemoryApiTests(unittest.TestCase):
    def test_autonomous_settings_and_summary_are_opt_in_and_user_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            service = temporary_service(Path(directory) / "api.sqlite3")
            with patch("backend.routes.agent_service", service):
                client = TestClient(app)

                def register(email: str, username: str) -> dict[str, str]:
                    response = client.post(
                        "/api/auth/register",
                        json={
                            "email": email,
                            "username": username,
                            "password": "password123",
                            "display_name": username,
                        },
                    )
                    return {"Authorization": f"Bearer {response.json()['token']}"}

                alice = register("alice-memory@example.com", "alice-memory")
                bob = register("bob-memory@example.com", "bob-memory")

                initial = client.get("/api/memory/settings", headers=alice)
                self.assertEqual(initial.status_code, 200)
                self.assertFalse(initial.json()["autonomous_enabled"])

                enabled = client.put(
                    "/api/memory/settings",
                    headers=alice,
                    json={
                        "autonomous_enabled": True,
                        "health_autonomous_enabled": False,
                    },
                )
                self.assertEqual(enabled.status_code, 200)
                self.assertTrue(enabled.json()["autonomous_enabled"])

                created = client.post(
                    "/api/memory/records",
                    headers=alice,
                    json={
                        "kind": "preference",
                        "content": "Любит острый соус",
                        "consent_granted": True,
                        "source_type": "manual_ui",
                    },
                )
                self.assertEqual(created.status_code, 200)

                summary = client.post("/api/memory/summary/rebuild", headers=alice)
                self.assertEqual(summary.status_code, 200)
                self.assertEqual(summary.json()["record_count"], 1)
                self.assertEqual(
                    summary.json()["sections"]["preference"][0]["content"],
                    "Любит острый соус",
                )
                self.assertFalse(summary.json()["sections"]["preference"][0]["auto_generated"])

                bob_summary = client.get("/api/memory/summary", headers=bob)
                self.assertEqual(bob_summary.status_code, 200)
                self.assertEqual(bob_summary.json()["record_count"], 0)
                self.assertFalse(
                    client.get("/api/memory/settings", headers=bob).json()["autonomous_enabled"]
                )

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
