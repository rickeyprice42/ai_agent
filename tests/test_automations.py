from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.automations import AutomationService
from ai_agent.config import Settings
from backend.main import app
from backend.service import AgentService


def isolated_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="mock-local",
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class AutomationTests(unittest.TestCase):
    def test_one_shot_weekly_coalescing_restart_and_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = isolated_settings(Path(directory))
            agent = Agent(settings, user_id="alice")
            scheduler = AutomationService(agent.memory.database, "alice")
            now = datetime(2026, 10, 27, 4, 0, tzinfo=timezone.utc)

            once = scheduler.create(
                name="Local draft",
                prompt=(
                    "Создай документ automation-draft.docx с заголовком: Автоматический черновик "
                    "с текстом: Локальный проверяемый черновик. Ничего не публикуй и не отправляй."
                ),
                schedule_type="once",
                timezone_name="Asia/Irkutsk",
                run_at="2026-10-28T12:00",
                budget={"max_steps": 7, "max_tokens": 12000},
                max_parallel_runs=1,
            )
            due = (now - timedelta(minutes=1)).isoformat()
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?", (due, once["id"])
                )
            queued = scheduler.run_due(now)
            self.assertEqual(len(queued), 1)
            self.assertEqual(scheduler.run_due(now), [])
            completed_once = agent.memory.database.get_automation("alice", once["id"])
            self.assertEqual(completed_once["status"], "completed")
            self.assertIsNone(completed_once["next_run_at"])
            run = agent.memory.database.get_task_run("alice", queued[0]["task_run_id"])
            self.assertEqual(run["state"], "queued")
            self.assertEqual(run["budget"]["max_steps"], 7)
            task = agent.memory.database.get_task(queued[0]["task_id"])
            self.assertEqual(task["steps"][0]["description"], once["prompt"])
            result = agent.executor.execute_next_step()
            self.assertIn("create_docx", result)
            self.assertTrue((settings.tool_workspace_dir / "automation-draft.docx").is_file())
            checkpoint = agent.memory.database.list_task_checkpoints(
                "alice", queued[0]["task_run_id"]
            )[0]
            self.assertTrue(checkpoint["evidence"]["verified"])
            self.assertEqual(checkpoint["artifacts"][0]["path"], "automation-draft.docx")

            weekly = scheduler.create(
                name="Weekly research draft",
                prompt="Prepare a local weekly research draft.",
                schedule_type="weekly",
                timezone_name="Asia/Irkutsk",
                weekday=0,
                time_of_day="09:00",
                budget={},
                max_parallel_runs=2,
            )
            three_weeks_ago = (now - timedelta(days=21)).isoformat()
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?",
                    (three_weeks_ago, weekly["id"]),
                )
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: scheduler.run_due(now), range(2)))
            self.assertEqual(sum(len(item) for item in results), 1)
            updated_weekly = agent.memory.database.get_automation("alice", weekly["id"])
            next_run = datetime.fromisoformat(updated_weekly["next_run_at"])
            self.assertGreater(next_run, now)
            self.assertLessEqual(next_run, now + timedelta(days=7))
            local_next = next_run.astimezone(ZoneInfo("Asia/Irkutsk"))
            self.assertEqual((local_next.weekday(), local_next.hour, local_next.minute), (0, 9, 0))
            self.assertEqual(len(agent.memory.database.list_automation_runs("alice", weekly["id"])), 1)

            restarted = Agent(settings, user_id="alice")
            self.assertEqual(AutomationService(restarted.memory.database, "alice").run_due(now), [])
            self.assertEqual(
                len(restarted.memory.database.list_automation_runs("alice", weekly["id"])), 1
            )
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?", (due, weekly["id"])
                )
            self.assertEqual(len(scheduler.run_due(now)), 1)
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?", (due, weekly["id"])
                )
            self.assertEqual(scheduler.run_due(now), [])
            self.assertEqual(len(agent.memory.database.list_automation_runs("alice", weekly["id"])), 2)

            paused = scheduler.create(
                name="Paused",
                prompt="Do not run while paused.",
                schedule_type="weekly",
                timezone_name="UTC",
                weekday=1,
                time_of_day="08:00",
            )
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?", (due, paused["id"])
                )
            scheduler.pause(paused["id"])
            self.assertEqual(scheduler.run_due(now), [])
            resumed = scheduler.resume(paused["id"], now)
            self.assertEqual(resumed["status"], "active")
            self.assertGreater(datetime.fromisoformat(resumed["next_run_at"]), now)
            scheduler.delete(paused["id"])
            scheduler.pause(weekly["id"])
            self.assertEqual(scheduler.run_due(now + timedelta(days=8)), [])
            self.assertEqual(agent.memory.database.list_automation_runs("alice", paused["id"]), [])

            for index in range(20):
                scheduler.create(
                    name=f"Limit {index}",
                    prompt="Prepare a local draft.",
                    schedule_type="once",
                    timezone_name="UTC",
                    run_at="2027-01-01T10:00",
                )
            with self.assertRaisesRegex(ValueError, "не более 20"):
                scheduler.create(
                    name="Over limit",
                    prompt="Must not be created.",
                    schedule_type="once",
                    timezone_name="UTC",
                    run_at="2027-01-01T11:00",
                )

    def test_crud_api_exposes_schedule_budget_and_last_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            service = AgentService.__new__(AgentService)
            service.root_dir = ROOT
            service.settings = settings
            service.database = Agent(settings, user_id="bootstrap").memory.database
            service._agents = {}

            with patch("backend.routes.agent_service", service):
                client = TestClient(app)
                registration = client.post(
                    "/api/auth/register",
                    json={
                        "email": "automations@example.com",
                        "username": "automations-user",
                        "password": "password123",
                        "display_name": "Automations User",
                    },
                ).json()
                headers = {"Authorization": f"Bearer {registration['token']}"}
                created = client.post(
                    "/api/automations",
                    headers=headers,
                    json={
                        "name": "Monday draft",
                        "prompt": "Prepare a local draft only.",
                        "schedule_type": "weekly",
                        "timezone": "Asia/Irkutsk",
                        "weekday": 0,
                        "time_of_day": "09:00",
                        "budget": {"max_steps": 9, "max_tokens": 15000},
                        "max_parallel_runs": 1,
                    },
                )
                self.assertEqual(created.status_code, 200)
                item = created.json()
                self.assertEqual(item["budget"]["max_steps"], 9)
                self.assertIn("Asia/Irkutsk", item["human_rule"])
                bootstrap = client.get("/api/bootstrap", headers=headers).json()
                self.assertEqual(bootstrap["automations"][0]["id"], item["id"])
                self.assertEqual(
                    client.post(f"/api/automations/{item['id']}/pause", headers=headers).json()["status"],
                    "paused",
                )
                self.assertEqual(
                    client.post(f"/api/automations/{item['id']}/resume", headers=headers).json()["status"],
                    "active",
                )
                updated = client.put(
                    f"/api/automations/{item['id']}",
                    headers=headers,
                    json={"name": "Updated draft", "max_parallel_runs": 2},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["name"], "Updated draft")
                deleted = client.delete(f"/api/automations/{item['id']}", headers=headers)
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(deleted.json()["status"], "deleted")
                self.assertEqual(client.get("/api/automations", headers=headers).json(), [])


if __name__ == "__main__":
    unittest.main()
