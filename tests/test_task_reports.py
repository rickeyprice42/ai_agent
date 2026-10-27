from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.executor import ExecutionDecision
from ai_agent.tools.base import Tool, ToolPermission
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


class ApprovalPlanner:
    def decide(self, task, step, checkpoints) -> ExecutionDecision:
        return ExecutionDecision("approval_probe", {"operation": step.description}, "approval test")


class TaskApprovalAndReportTests(unittest.TestCase):
    def test_approve_deny_expiry_restart_reports_and_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            service = AgentService.__new__(AgentService)
            service.root_dir = ROOT
            service.settings = settings
            service.database = Agent(settings, user_id="bootstrap").memory.database
            service._agents = {}
            calls: list[str] = []

            with patch("backend.routes.agent_service", service):
                client = TestClient(app)
                registration = client.post(
                    "/api/auth/register",
                    json={
                        "email": "reports@example.com",
                        "username": "reports-user",
                        "password": "password123",
                        "display_name": "Reports User",
                    },
                ).json()
                user_id = registration["user"]["id"]
                headers = {"Authorization": f"Bearer {registration['token']}"}
                agent = service.agent_for_user(user_id)
                probe_tool = Tool(
                        name="approval_probe",
                        description="Approval lifecycle probe",
                        schema={
                            "type": "object",
                            "properties": {"operation": {"type": "string"}},
                            "required": ["operation"],
                        },
                        handler=lambda args: calls.append(args["operation"]) or {
                            "path": "reports/evidence.txt", "name": "evidence.txt", "sha256": "abc"
                        },
                        output_schema={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "name": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["path", "name", "sha256"],
                            "additionalProperties": False,
                        },
                        capabilities=("test.external",),
                        permissions=(ToolPermission("test.external", "external"),),
                    )
                agent.tools.register(probe_tool)
                agent.executor.action_planner = ApprovalPlanner()

                approved_task = agent.tasks.create_task("approved", steps=["approved-operation"])
                blocked = client.post("/api/tasks/execute-next", headers=headers)
                self.assertEqual(blocked.status_code, 200)
                self.assertEqual(calls, [])
                approved_run = agent.tasks.task_run(approved_task.id)
                self.assertEqual(approved_run["state"], "waiting_approval")
                self.assertIn("нет задач", client.post("/api/tasks/execute-next", headers=headers).json()["result"].lower())
                pending = client.get("/api/approvals", headers=headers).json()[0]
                self.assertEqual(pending["arguments"], {"operation": "approved-operation"})
                self.assertEqual(pending["risk"], "external")
                self.assertTrue(pending["scope"])
                self.assertTrue(pending["expires_at"])
                self.assertTrue(client.get("/api/notifications?unread_only=true", headers=headers).json())

                decision = client.post(
                    f"/api/approvals/{pending['id']}/decision",
                    headers=headers,
                    json={"decision": "approved"},
                )
                self.assertEqual(decision.status_code, 200)
                self.assertEqual(calls, ["approved-operation"])
                self.assertEqual(agent.tasks.task_run(approved_task.id)["state"], "completed")
                self.assertEqual(service.database.get_approval_request(user_id, pending["id"])["status"], "consumed")

                denied_task = agent.tasks.create_task("denied", steps=["denied-operation"])
                client.post("/api/tasks/execute-next", headers=headers)
                denied_approval = client.get("/api/approvals", headers=headers).json()[0]
                denied = client.post(
                    f"/api/approvals/{denied_approval['id']}/decision",
                    headers=headers,
                    json={"decision": "denied"},
                )
                self.assertEqual(denied.status_code, 200)
                self.assertEqual(calls, ["approved-operation"])
                denied_run = agent.tasks.task_run(denied_task.id)
                self.assertEqual(denied_run["state"], "paused")
                self.assertEqual(agent.tasks.checkpoints(denied_run["id"])[0]["status"], "denied")

                expired_task = agent.tasks.create_task("expired", steps=["expired-operation"])
                client.post("/api/tasks/execute-next", headers=headers)
                expired_approval = client.get("/api/approvals", headers=headers).json()[0]
                with service.database.connect() as connection:
                    connection.execute(
                        "UPDATE approval_requests SET expires_at = ? WHERE id = ?",
                        ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), expired_approval["id"]),
                    )
                expired = client.post(
                    f"/api/approvals/{expired_approval['id']}/decision",
                    headers=headers,
                    json={"decision": "approved"},
                )
                self.assertEqual(expired.status_code, 400)
                expired_run = agent.tasks.task_run(expired_task.id)
                self.assertEqual(expired_run["state"], "waiting_approval")
                self.assertEqual(calls, ["approved-operation"])

                service._drop_user_agents(user_id)
                restarted = service.agent_for_user(user_id)
                self.assertEqual(restarted.tasks.task_run(denied_task.id)["state"], "paused")
                self.assertEqual(restarted.tasks.task_run(expired_task.id)["state"], "waiting_approval")
                restarted.tools.register(probe_tool)
                restarted.executor.action_planner = ApprovalPlanner()
                self.assertEqual(
                    client.post(f"/api/tasks/{expired_task.id}/resume", headers=headers).status_code,
                    200,
                )
                retried = client.post("/api/tasks/execute-next", headers=headers)
                self.assertEqual(retried.status_code, 200)
                replacement = client.get("/api/approvals", headers=headers).json()[0]
                self.assertNotEqual(replacement["id"], expired_approval["id"])
                self.assertEqual(calls, ["approved-operation"])
                self.assertEqual(restarted.tasks.task_run(expired_task.id)["state"], "waiting_approval")

                checkpoint = agent.tasks.checkpoints(approved_run["id"])[0]
                service.database.create_task_checkpoint(
                    user_id,
                    run_id=approved_run["id"],
                    task_id=approved_task.id,
                    step_id=None,
                    plan_version=1,
                    status="completed",
                    risk="read",
                    tool_name="run_code_check",
                    arguments_digest="verified-test",
                    deduplication_key="verified-test",
                    tool_call={"name": "run_code_check", "arguments": {}},
                    observation="1 test passed",
                    artifacts=[{"path": "reports/test.log", "name": "test.log"}],
                    evidence={"verified": True, "return_code": 0},
                    next_action="none",
                )
                service.database.create_task_checkpoint(
                    user_id,
                    run_id=approved_run["id"],
                    task_id=approved_task.id,
                    step_id=None,
                    plan_version=1,
                    status="completed",
                    risk="read",
                    tool_name="unverified_claim",
                    arguments_digest="unverified",
                    deduplication_key="unverified",
                    tool_call={"name": "unverified_claim", "arguments": {}},
                    observation="model said that work is done",
                    evidence={"verified": False},
                    next_action="none",
                )
                service.database.create_task_checkpoint(
                    user_id,
                    run_id=approved_run["id"],
                    task_id=approved_task.id,
                    step_id=None,
                    plan_version=1,
                    status="completed",
                    risk="high",
                    tool_name="git_commit",
                    arguments_digest="commit",
                    deduplication_key="commit",
                    tool_call={"name": "git_commit", "arguments": {}},
                    observation="commit abc123",
                    evidence={"verified": True, "commit": "abc123"},
                    next_action="none",
                )
                report = client.post(f"/api/task-runs/{approved_run['id']}/report", headers=headers)
                self.assertEqual(report.status_code, 200)
                report_payload = report.json()["payload"]
                self.assertTrue(report_payload["evidence_only"])
                self.assertEqual(report_payload["checks"][0]["reference"].split(":")[0], "checkpoint")
                self.assertEqual({item["path"] for item in report_payload["artifacts"]}, {"reports/evidence.txt", "reports/test.log"})
                self.assertEqual(report_payload["commits"][0]["tool"], "git_commit")
                self.assertNotIn(
                    "unverified_claim",
                    {item["tool"] for item in report_payload["completed_actions"]},
                )

                daily = client.post(
                    f"/api/task-reports/daily?date={date.today().isoformat()}", headers=headers
                )
                self.assertEqual(daily.status_code, 200)
                self.assertTrue(daily.json()["payload"]["evidence_only"])
                self.assertIn("reports/test.log", {item["path"] for item in daily.json()["payload"]["artifacts"]})
                self.assertGreaterEqual(len(client.get("/api/task-reports", headers=headers).json()), 2)

                notifications = client.get("/api/notifications?unread_only=true", headers=headers).json()
                self.assertTrue(any(item["kind"] == "daily_report" for item in notifications))
                marked = client.post(f"/api/notifications/{notifications[0]['id']}/read", headers=headers)
                self.assertEqual(marked.status_code, 200)
                self.assertIsNotNone(marked.json()["read_at"])


if __name__ == "__main__":
    unittest.main()
