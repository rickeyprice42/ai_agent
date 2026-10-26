from dataclasses import replace
from pathlib import Path
from threading import Event, Timer
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.action_log import ActionLogStore
from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.tools.base import (
    RetryPolicy,
    Tool,
    ToolPermission,
    ToolRegistry,
    ToolResult,
    failure_result,
)
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


class ToolRegistryContractTests(unittest.TestCase):
    def test_authenticated_discovery_api_lists_gets_and_groups_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            service = AgentService.__new__(AgentService)
            service.root_dir = ROOT
            service.settings = settings
            service.database = AvelinDatabase(
                settings.database_file,
                default_model_provider="mock",
                default_model_name="mock-local",
            )
            service._agents = {}
            with patch("backend.routes.agent_service", service):
                client = TestClient(app)
                registered = client.post(
                    "/api/auth/register",
                    json={
                        "email": "tools@example.com",
                        "username": "tools-user",
                        "password": "password123",
                        "display_name": "Tools User",
                    },
                )
                headers = {"Authorization": f"Bearer {registered.json()['token']}"}
                listed = client.get("/api/tools", headers=headers)
                self.assertEqual(listed.status_code, 200)
                self.assertGreaterEqual(len(listed.json()), 20)
                detail = client.get("/api/tools/read_file", headers=headers)
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["name"], "read_file")
                capabilities = client.get("/api/tools/capabilities", headers=headers)
                self.assertEqual(capabilities.status_code, 200)
                self.assertIn("files.read", capabilities.json()["read_file"])
                self.assertEqual(client.get("/api/tools/missing", headers=headers).status_code, 404)

    def test_all_builtins_expose_versioned_production_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(isolated_settings(Path(directory)), user_id="alice")
            manifests = agent.tools.list_manifests()
            model_tools = agent.tools.list_for_model()

            self.assertGreaterEqual(len(manifests), 20)
            self.assertEqual(
                {item["name"] for item in manifests},
                {item["name"] for item in model_tools},
            )
            for manifest in manifests:
                with self.subTest(tool=manifest["name"]):
                    self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
                    self.assertEqual(manifest["input_schema"]["type"], "object")
                    self.assertFalse(manifest["input_schema"]["additionalProperties"])
                    self.assertTrue(manifest["capabilities"])
                    self.assertTrue(manifest["permissions"])
                    self.assertTrue(manifest["available"])

    def test_nested_json_schema_rejects_unknown_type_enum_and_bounds_before_handler(self):
        calls = []
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="contract_demo",
                description="Nested contract demo",
                schema={
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string", "enum": ["fast", "safe"]},
                                "items": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 2,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "score": {"type": "integer", "minimum": 1, "maximum": 5}
                                        },
                                        "required": ["score"],
                                    },
                                },
                            },
                            "required": ["mode", "items"],
                        }
                    },
                    "required": ["request"],
                },
                handler=lambda args: calls.append(args) or "ok",
            )
        )

        invalid = [
            {"request": {"mode": "wrong", "items": [{"score": 2}]}},
            {"request": {"mode": "safe", "items": [{"score": "2"}]}},
            {"request": {"mode": "safe", "items": [{"score": 9}]}},
            {"request": {"mode": "safe", "items": [{"score": 2, "extra": True}]}},
            {"request": {"mode": "safe", "items": []}},
            {"request": {"mode": "safe", "items": [{"score": 2}]}, "unknown": 1},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = registry.invoke("contract_demo", payload)
                self.assertEqual(result.status, "validation_error")
        self.assertEqual(calls, [])
        self.assertTrue(
            registry.invoke(
                "contract_demo",
                {"request": {"mode": "safe", "items": [{"score": 5}]}},
            ).ok
        )
        self.assertEqual(len(calls), 1)

    def test_typed_result_does_not_infer_status_from_display_text(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="typed_demo",
                description="Typed demo",
                schema={"type": "object", "properties": {}},
                handler=lambda _: ToolResult(
                    status="failed",
                    display_text="Everything looks successful",
                    error=None,
                ),
            )
        )
        result = registry.invoke("typed_demo", {})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.text(), "Everything looks successful")

    def test_timeout_cancellation_retry_and_idempotency(self):
        registry = ToolRegistry()
        retry_calls = []

        def retry_handler(_args, context):
            retry_calls.append(context.attempt)
            if context.attempt == 1:
                return failure_result(
                    "temporarily_unavailable",
                    "temporary",
                    retryable=True,
                )
            return "ready"

        registry.register(
            Tool(
                name="retry_demo",
                description="Retry demo",
                schema={"type": "object", "properties": {}},
                handler=retry_handler,
                retry_policy=RetryPolicy(max_attempts=2),
            )
        )
        retried = registry.invoke("retry_demo", {})
        self.assertTrue(retried.ok)
        self.assertEqual(retried.attempts, 2)
        self.assertEqual(retry_calls, [1, 2])
        self.assertIn("retrying", [event.type for event in retried.events])

        registry.register(
            Tool(
                name="timeout_demo",
                description="Timeout demo",
                schema={"type": "object", "properties": {}},
                handler=lambda _: time.sleep(0.05) or "late",
                timeout_seconds=0.005,
            )
        )
        timed_out = registry.invoke("timeout_demo", {})
        self.assertEqual(timed_out.error.code, "timeout")

        cancelled = Event()
        cancelled.set()
        self.assertEqual(
            registry.invoke("retry_demo", {}, cancellation=cancelled).status,
            "cancelled",
        )

        cooperative_cancel = Event()

        def cancellable_handler(_args, context):
            while not context.cancelled:
                time.sleep(0.001)
            return "handler stopped"

        registry.register(
            Tool(
                name="cancellable_demo",
                description="Cancellation demo",
                schema={"type": "object", "properties": {}},
                handler=cancellable_handler,
                timeout_seconds=0.2,
            )
        )
        timer = Timer(0.01, cooperative_cancel.set)
        timer.start()
        try:
            running_cancelled = registry.invoke(
                "cancellable_demo", {}, cancellation=cooperative_cancel
            )
        finally:
            timer.cancel()
        self.assertEqual(running_cancelled.status, "cancelled")

        idempotent_calls = []
        registry.register(
            Tool(
                name="idempotent_demo",
                description="Idempotency demo",
                schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
                handler=lambda args: idempotent_calls.append(args["value"]) or str(args["value"]),
                idempotent=True,
            )
        )
        first = registry.invoke("idempotent_demo", {"value": 1}, idempotency_key="same")
        second = registry.invoke("idempotent_demo", {"value": 1}, idempotency_key="same")
        conflict = registry.invoke("idempotent_demo", {"value": 2}, idempotency_key="same")
        self.assertTrue(first.ok)
        self.assertTrue(second.cached)
        self.assertEqual(idempotent_calls, [1])
        self.assertEqual(conflict.error.code, "idempotency_conflict")

    def test_permissions_affect_discovery_and_sensitive_logs_are_redacted(self):
        registry = ToolRegistry(granted_permissions={"local.read"})
        registry.register(
            Tool(
                name="restricted_demo",
                description="Restricted demo",
                schema={"type": "object", "properties": {}},
                handler=lambda _: "never",
                capabilities=("workspace.write",),
                permissions=(ToolPermission("workspace.write", "write"),),
            )
        )
        manifest = registry.get_manifest("restricted_demo")
        self.assertFalse(manifest["available"])
        self.assertFalse(registry.has("restricted_demo"))
        self.assertEqual(registry.invoke("restricted_demo", {}).status, "blocked")

        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "logs.sqlite3")
            database.ensure_user_defaults("alice")
            logs = ActionLogStore(database, "alice")
            logs.record(
                "remember_note",
                "completed",
                {"note": "API key secret-value", "scope": "global"},
                "Запомнил: API key secret-value",
            )
            item = logs.recent(1)[0]
            self.assertEqual(item.arguments["note"], "[REDACTED]")
            self.assertNotIn("secret-value", item.result)


if __name__ == "__main__":
    unittest.main()
