from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import sys
import tempfile
import threading
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
from ai_agent.security import arguments_digest
from ai_agent.tools.base import Tool, ToolPermission, ToolResult
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


class StaticPlanner:
    def __init__(self, name: str, arguments: dict | None = None) -> None:
        self.name = name
        self.arguments = arguments or {}

    def decide(self, task, step, checkpoints) -> ExecutionDecision:
        return ExecutionDecision(self.name, self.arguments, "test plan")


class CodingPlanner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def decide(self, task, step, checkpoints) -> ExecutionDecision:
        if step.description == "fix implementation":
            source = self.workspace / "sample.py"
            return ExecutionDecision(
                "edit_file_exact",
                {
                    "path": "sample.py",
                    "expected_sha256": sha256(source.read_bytes()).hexdigest(),
                    "old_text": "return 1",
                    "new_text": "return 2",
                },
                "apply exact coding fix",
            )
        return ExecutionDecision(
            "run_code_check",
            {"runner": "python_unittest", "target": ".", "timeout_seconds": 30},
            "verify coding fix",
        )


class DurableWorkerTests(unittest.TestCase):
    def test_end_to_end_coding_task_finishes_with_test_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            workspace = settings.tool_workspace_dir
            workspace.mkdir(parents=True)
            (workspace / "sample.py").write_text(
                "def value():\n    return 1\n", encoding="utf-8"
            )
            (workspace / "test_sample.py").write_text(
                "import unittest\nfrom sample import value\n\n"
                "class SampleTests(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(value(), 2)\n",
                encoding="utf-8",
            )
            agent = Agent(settings, user_id="alice")
            agent.executor.action_planner = CodingPlanner(workspace)
            task = agent.tasks.create_task(
                "repair and verify sample", steps=["fix implementation", "run tests"]
            )

            self.assertIn("edit_file_exact", agent.executor.execute_next_step())
            blocked = agent.executor.execute_next_step()
            self.assertIn("Approval ID", blocked)
            running = agent.tasks.get_task(task.id)
            blocked_step = next(step for step in running.steps if step.status == "blocked")
            verified = agent.executor.approve_blocked_step(blocked_step.id)

            self.assertIn("run_code_check", verified)
            self.assertIn("return 2", (workspace / "sample.py").read_text(encoding="utf-8"))
            self.assertEqual(agent.tasks.get_task(task.id).status, "completed")
            run = agent.tasks.task_run(task.id)
            self.assertEqual(run["state"], "completed")
            checkpoints = agent.tasks.checkpoints(run["id"])
            self.assertEqual([item["status"] for item in checkpoints], ["completed", "completed"])
            self.assertTrue(all(item["evidence"].get("verified") for item in checkpoints))

    def test_durable_queue_api_exposes_runs_checkpoints_and_controls(self):
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
                registered = client.post(
                    "/api/auth/register",
                    json={
                        "email": "durable@example.com",
                        "username": "durable-user",
                        "password": "password123",
                        "display_name": "Durable User",
                    },
                )
                self.assertEqual(registered.status_code, 200)
                payload = registered.json()
                headers = {"Authorization": f"Bearer {payload['token']}"}
                user_id = payload["user"]["id"]
                task = service.agent_for_user(user_id).tasks.create_task(
                    "api control", steps=["show files"]
                )

                runs = client.get("/api/task-runs", headers=headers)
                self.assertEqual(runs.status_code, 200)
                run = next(item for item in runs.json() if item["task_id"] == task.id)
                self.assertEqual(
                    client.get(f"/api/task-runs/{run['id']}/checkpoints", headers=headers).status_code,
                    200,
                )
                for action, state in (("pause", "paused"), ("resume", "queued"), ("cancel", "cancelled")):
                    response = client.post(f"/api/tasks/{task.id}/{action}", headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["run"]["state"], state)

    def test_lease_is_exclusive_and_stale_run_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(isolated_settings(Path(directory)), user_id="alice")
            task = agent.tasks.create_task("inspect", steps=["show files"])
            first = agent.tasks.acquire_durable_run("worker-one", 30)
            self.assertEqual(first["task_id"], task.id)
            self.assertIsNone(agent.tasks.acquire_durable_run("worker-two", 30))

            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE task_runs SET lease_expires_at = ? WHERE id = ?",
                    (expired, first["id"]),
                )
            recovered = agent.tasks.acquire_durable_run("worker-two", 30)
            self.assertEqual(recovered["id"], first["id"])
            self.assertEqual(recovered["worker_id"], "worker-two")

    def test_checkpoint_survives_restart_and_completed_step_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            calls = []
            agent = Agent(settings, user_id="alice")
            agent.tools.register(
                Tool(
                    name="durable_probe",
                    description="Durable test probe",
                    schema={"type": "object", "properties": {}},
                    handler=lambda args: calls.append(args) or {"ok": True},
                    capabilities=("test.read",),
                    permissions=(ToolPermission("test.read", "read"),),
                    idempotent=True,
                    output_schema={"type": "object"},
                )
            )
            agent.executor.action_planner = StaticPlanner("durable_probe")
            task = agent.tasks.create_task("probe", steps=["probe once"])
            agent.executor.execute_next_step()
            self.assertEqual(len(calls), 1)
            run = agent.tasks.task_run(task.id)
            self.assertEqual(run["state"], "completed")
            self.assertEqual(agent.tasks.checkpoints(run["id"])[0]["status"], "completed")

            restarted = Agent(settings, user_id="alice")
            restarted.tools.register(agent.tools.get("durable_probe"))
            restarted.executor.action_planner = StaticPlanner("durable_probe")
            with restarted.memory.database.connect() as connection:
                connection.execute("UPDATE tasks SET status = 'executing' WHERE id = ?", (task.id,))
                connection.execute(
                    "UPDATE task_steps SET status = 'running' WHERE task_id = ?", (task.id,)
                )
                connection.execute(
                    "UPDATE task_runs SET state = 'queued', finished_at = NULL WHERE id = ?", (run["id"],)
                )
            result = restarted.executor.execute_next_step()
            self.assertIn("восстановлен из проверенного checkpoint", result)
            self.assertEqual(len(calls), 1)

    def test_uncertain_external_checkpoint_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(isolated_settings(root), user_id="alice")
            calls = []
            tool = Tool(
                name="external_probe",
                description="External test probe",
                schema={"type": "object", "properties": {}},
                handler=lambda args: calls.append(args) or "sent",
                capabilities=("test.external",),
                permissions=(ToolPermission("test.external", "external"),),
            )
            agent.tools.register(tool)
            agent.executor.action_planner = StaticPlanner("external_probe")
            task = agent.tasks.create_task("external", steps=["send once"])
            run = agent.tasks.acquire_durable_run("crashed-worker", 30)
            step = task.steps[0]
            digest = arguments_digest({})
            key = f"plan:{run['plan_version']}:step:{step.id}:external_probe:{digest}"
            agent.memory.database.create_task_checkpoint(
                "alice", run_id=run["id"], task_id=task.id, step_id=step.id,
                plan_version=run["plan_version"], status="started", risk="external",
                tool_name="external_probe", arguments_digest=digest,
                deduplication_key=key, tool_call={"name": "external_probe", "arguments": {}},
                evidence={"verified": False}, next_action="record_result",
            )
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE task_runs SET lease_expires_at = ? WHERE id = ?",
                    ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), run["id"]),
                )
            result = agent.executor.execute_next_step()
            self.assertIn("Повторный запуск заблокирован", result)
            self.assertEqual(calls, [])
            self.assertEqual(agent.tasks.task_run(task.id)["state"], "waiting_approval")

    def test_budget_and_user_controls_stop_work_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(isolated_settings(Path(directory)), user_id="alice")
            task = agent.tasks.create_task("budget", steps=["show files"])
            run = agent.tasks.task_run(task.id)
            budget = dict(run["budget"])
            budget["max_steps"] = 1
            usage = dict(run["usage"])
            usage["steps"] = 1
            with agent.memory.database.connect() as connection:
                connection.execute(
                    "UPDATE task_runs SET budget_json = ?, usage_json = ? WHERE id = ?",
                    (json.dumps(budget), json.dumps(usage), run["id"]),
                )
            self.assertIn("исчерпан лимит шагов", agent.executor.execute_next_step())
            self.assertEqual(agent.tasks.task_run(task.id)["state"], "failed")

            controlled = agent.tasks.create_task("control", steps=["show files"])
            agent.tasks.pause_task(controlled.id)
            self.assertEqual(agent.tasks.task_run(controlled.id)["state"], "paused")
            agent.tasks.resume_task(controlled.id)
            self.assertEqual(agent.tasks.task_run(controlled.id)["state"], "queued")
            agent.tasks.cancel_task(controlled.id)
            self.assertEqual(agent.tasks.task_run(controlled.id)["state"], "cancelled")

    def test_pause_interrupts_active_tool_without_worker_overwriting_state(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(isolated_settings(Path(directory)), user_id="alice")
            started = threading.Event()

            def slow_probe(args, context):
                started.set()
                context.cancellation.wait(2)
                return ToolResult(status="cancelled", display_text="interrupted")

            agent.tools.register(
                Tool(
                    name="slow_probe",
                    description="Cooperative cancellation probe",
                    schema={"type": "object", "properties": {}},
                    handler=slow_probe,
                    capabilities=("test.read",),
                    permissions=(ToolPermission("test.read", "read"),),
                )
            )
            agent.executor.action_planner = StaticPlanner("slow_probe")
            task = agent.tasks.create_task("slow", steps=["wait"])
            output: list[str] = []
            worker = threading.Thread(target=lambda: output.append(agent.executor.execute_next_step()))
            worker.start()
            self.assertTrue(started.wait(2))
            run = agent.tasks.task_run(task.id)
            agent.tasks.pause_task(task.id)
            self.assertTrue(agent.executor.interrupt_run(run["id"]))
            worker.join(3)

            self.assertFalse(worker.is_alive())
            self.assertEqual(agent.tasks.task_run(task.id)["state"], "paused")
            self.assertEqual(agent.tasks.get_task(task.id).status, "paused")
            self.assertIn("приостановлено", output[0])


if __name__ == "__main__":
    unittest.main()
