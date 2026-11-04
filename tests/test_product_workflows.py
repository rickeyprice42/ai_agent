"""User-visible scheduled results and explicit group/invitation boundaries."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import asyncio
import unittest

from fastapi.testclient import TestClient
from backend.main import app, _automation_scheduler
from backend.service import AgentService
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase

ROOT = Path(__file__).resolve().parents[1]


class ProductWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = AgentService.__new__(AgentService)
        self.service.root_dir = ROOT
        self.service.settings = replace(Settings.load(ROOT), model_provider="mock", model_name="mock-local",
            reasoning_fallback_enabled=False, database_file=root/"test.sqlite3", memory_file=root/"memory.json",
            tool_workspace_dir=root/"files")
        self.service.database = AvelinDatabase(self.service.settings.database_file,
            default_model_provider="mock", default_model_name="mock-local")
        self.service._agents = {}
        self.service._connector_factories = []
        self.patch = patch("backend.routes.agent_service", self.service)
        self.patch.start()
        self.client = TestClient(app)
        self.owner, self.owner_id = self.register("owner")
        self.member, self.member_id = self.register("member")
        self.other, self.other_id = self.register("other")

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def register(self, name):
        response = self.client.post("/api/auth/register", json={"email": f"{name}@test.example",
            "username": name, "display_name": name.title(), "password": "password123"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return {"Authorization": "Bearer " + body["token"]}, body["user"]["id"]

    def automation(self):
        response = self.client.post("/api/automations", headers=self.owner, json={
            "name": "Проверяемый результат", "prompt": "Создай документ drafts/test-{date}.docx с заголовком: Проверка с текстом: Автоматизация работает.",
            "schedule_type": "weekly", "timezone": "Asia/Irkutsk", "weekday": 2, "time_of_day": "12:00"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_manual_run_preserves_schedule_consumes_only_automation_and_exposes_file(self):
        item = self.automation()
        personal = self.service.agent_for_user(self.owner_id).tasks.create_task(
            "Personal draft must stay queued", steps=["Создай документ personal.docx с текстом: Private"])
        response = self.client.post(f"/api/automations/{item['id']}/run", headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        occurrence = response.json()
        duplicate = self.client.post(f"/api/automations/{item['id']}/run", headers=self.owner)
        self.assertEqual(duplicate.status_code, 400)
        processed = self.service.process_automation_queue()
        self.assertEqual([r["state"] for r in processed], ["completed"])
        self.assertEqual(self.service.process_automation_queue(), [])
        after = self.service.database.get_automation(self.owner_id, item["id"])
        self.assertEqual(after["next_run_at"], item["next_run_at"])
        personal_run = self.service.database.get_task_run_for_task(self.owner_id, personal.id)
        self.assertEqual(personal_run["state"], "queued")
        rows = self.client.get(f"/api/automations/{item['id']}/runs", headers=self.owner).json()
        self.assertEqual(rows[0]["task_run_id"], occurrence["task_run_id"])
        self.assertEqual(rows[0]["state"], "completed")
        self.assertIn("test-", rows[0]["result"])
        self.assertEqual(len(rows[0]["artifacts"]), 1)
        path = rows[0]["artifacts"][0]["path"]
        download = self.client.get("/api/workspace/files/" + path, headers=self.owner)
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b"PK"))
        self.assertEqual(self.client.get("/api/workspace/files/" + path, headers=self.other).status_code, 404)
        self.assertEqual(self.client.get(f"/api/automations/{item['id']}/runs", headers=self.other).status_code, 404)
        self.assertEqual(self.client.post(f"/api/automations/{item['id']}/run", headers=self.other).status_code, 400)
        self.service._agents.clear()
        self.assertEqual(self.service.automation_runs(self.owner_id, item["id"])[0]["artifacts"][0]["path"], path)

    def test_scheduler_executes_due_work_and_publishes_terminal_notification(self):
        item = self.automation()
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.service.database.connect() as connection:
            connection.execute("UPDATE automations SET next_run_at=? WHERE id=?", (due, item["id"]))

        async def run_one_tick():
            stop = asyncio.Event()
            original = self.service.process_automation_queue
            def consume():
                result = original()
                stop.set()
                return result
            with patch("backend.main.agent_service", self.service), patch.object(self.service, "process_automation_queue", consume):
                await _automation_scheduler(stop)
        asyncio.run(run_one_tick())
        run = self.service.automation_runs(self.owner_id, item["id"])[0]
        self.assertEqual(run["state"], "completed")
        notifications = self.service.database.list_in_app_notifications(self.owner_id)
        self.assertTrue(any(n["kind"] == "task_report" for n in notifications))

    def test_automation_repeated_same_day_keeps_each_result_and_preserves_user_file(self):
        item = self.automation()
        workspace = self.service.user_workspace(self.owner_id)
        from zoneinfo import ZoneInfo
        date = datetime.now(ZoneInfo("Asia/Irkutsk")).date().isoformat()
        original = workspace / f"drafts/test-{date}.docx"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"USER-ORIGINAL-MUST-SURVIVE")
        paths = []
        for _ in range(3):
            occurrence = self.service.run_automation_now(self.owner_id, item["id"])
            self.assertEqual(self.service.process_automation_queue()[0]["state"], "completed")
            run = next(row for row in self.service.automation_runs(self.owner_id, item["id"])
                if row["task_run_id"] == occurrence["task_run_id"])
            path = run["artifacts"][0]["path"]
            paths.append(path)
            self.assertTrue((workspace / path).is_file())
            self.assertIn(occurrence["task_run_id"], path)
        self.assertEqual(len(set(paths)), 3)
        self.assertEqual(original.read_bytes(), b"USER-ORIGINAL-MUST-SURVIVE")
        self.service._agents.clear()
        after = self.service.automation_runs(self.owner_id, item["id"])
        self.assertEqual({row["artifacts"][0]["path"] for row in after}, set(paths))

    def test_automation_output_prefix_preserves_invalid_paths_for_sandbox_rejection(self):
        item = self.automation()
        occurrence = self.service.run_automation_now(self.owner_id, item["id"])
        run = self.service.database.get_task_run(self.owner_id, occurrence["task_run_id"])
        engine = self.service.agent_for_user(self.owner_id).executor
        for path in ("../../private.docx", "C:/private.docx", "/private.docx"):
            arguments = {"path": path, "paragraphs": ["data"]}
            self.assertEqual(engine._automation_output_arguments(run, "create_docx", arguments), arguments)

    def test_deleted_automation_cancels_unexecuted_occurrences(self):
        item = self.automation()
        self.service.run_automation_now(self.owner_id, item["id"])
        self.client.delete(f"/api/automations/{item['id']}", headers=self.owner)
        self.assertEqual(self.service.process_automation_queue(), [])
        self.assertEqual(self.service.automation_runs(self.owner_id, item["id"])[0]["state"], "cancelled")

    def test_unsupported_scheduled_action_reports_failure_instead_of_unactionable_approval(self):
        item = self.automation()
        self.service.update_automation(self.owner_id, item["id"], {"prompt": "Сравни три стратегии и предложи план на год."})
        self.service.run_automation_now(self.owner_id, item["id"])
        self.service.process_automation_queue()
        run = self.service.automation_runs(self.owner_id, item["id"])[0]
        self.assertEqual(run["state"], "failed")
        self.assertIn("Не удалось подобрать действие", run["result"])
        self.assertEqual(run["artifacts"], [])
        self.assertEqual(self.service.approval_requests(self.owner_id, status="pending"), [])

    def test_approving_automation_never_consumes_higher_priority_personal_task(self):
        from ai_agent.tools.base import Tool, ToolPermission
        from ai_agent.executor import ExecutionDecision
        item = self.automation()
        agent = self.service.agent_for_user(self.owner_id)
        personal = agent.tasks.create_task("Private draft", priority=1, steps=["Private step"])
        calls = []
        agent.tools.register(Tool(name="external_probe", description="Test an explicit approval without external traffic",
            schema={"type": "object", "properties": {}}, handler=lambda _: calls.append(1) or "Confirmed result",
            permissions=(ToolPermission("test.external", "external"),)))
        class Planner:
            def decide(self, task, step, checkpoints):
                return ExecutionDecision("external_probe", {})
        agent.executor.action_planner = Planner()
        occurrence = self.service.run_automation_now(self.owner_id, item["id"])
        self.service.process_automation_queue()
        self.assertEqual(self.service.database.get_task_run(self.owner_id, occurrence["task_run_id"])["state"], "waiting_approval")
        approval = self.service.approval_requests(self.owner_id, "pending")[0]
        self.service.decide_approval(self.owner_id, approval["id"], "approved")
        self.assertEqual(calls, [1])
        self.assertEqual(self.service.database.get_task_run(self.owner_id, occurrence["task_run_id"])["state"], "completed")
        self.assertEqual(agent.tasks.task_run(personal.id)["state"], "queued")

    def test_groups_are_explicit_and_link_is_single_use_without_owner_downgrade(self):
        private = self.client.post("/api/projects", headers=self.owner, json={"title": "Личный"}).json()["project"]
        group_response = self.client.post("/api/projects", headers=self.owner, json={"title": "Наша группа", "shared": True})
        self.assertEqual(group_response.status_code, 200, group_response.text)
        group = group_response.json()["project"]
        self.assertFalse(private["shared"])
        self.assertTrue(group["shared"])
        self.assertEqual(group["agent_mode"], "mention_only")
        def invitation():
            response = self.client.post(f"/api/projects/{group['id']}/invitations", headers=self.owner, json={"role": "editor"})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        invite = invitation()
        self.assertEqual(self.client.post("/api/project-invitations/accept", headers=self.owner,
            json={"token": invite["token"]}).status_code, 400)
        self.assertEqual(self.service.database.project_role(self.owner_id, group["id"]), "owner")
        with ThreadPoolExecutor(max_workers=2) as pool:
            def accept(user_id):
                try:
                    return self.service.accept_project_invitation(user_id, invite["token"])["id"]
                except ValueError:
                    return None
            results = list(pool.map(accept, [self.member_id, self.other_id]))
        self.assertEqual(results.count(group["id"]), 1)
        self.assertEqual(self.service.database.get_project(self.owner_id, private["id"])["shared"], False)
        revoked = invitation()
        self.client.delete(f"/api/projects/{group['id']}/invitations/{revoked['id']}", headers=self.owner)
        self.assertEqual(self.client.post("/api/project-invitations/accept", headers=self.member,
            json={"token": revoked["token"]}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/projects/{group['id']}/invitations", headers=self.other,
            json={"role": "editor"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
