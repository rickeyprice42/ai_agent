"""HTTP turn stress and tenant isolation against an isolated temporary database."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import importlib
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_BOOTSTRAP = TemporaryDirectory(prefix="avelin-audit-bootstrap-")
BOOTSTRAP_ROOT = Path(_BOOTSTRAP.name)


class AuditStressTests(unittest.TestCase):
    def test_one_hundred_streamed_turns_and_five_tenants_remain_isolated(self) -> None:
        # Patch environment before importing modules with a module-level service.
        # No test creates a session, chat, or artifact in data/avelin.sqlite3.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {
                "DATABASE_FILE": str(BOOTSTRAP_ROOT / "bootstrap.sqlite3"),
                "TOOL_WORKSPACE_DIR": str(BOOTSTRAP_ROOT / "workspace"),
                "MEMORY_FILE": str(BOOTSTRAP_ROOT / "memory.json"),
                "MODEL_PROVIDER": "mock",
                "MODEL_NAME": "mock-local",
            }):
                from ai_agent.config import Settings
                from ai_agent.database import AvelinDatabase
                from fastapi.testclient import TestClient
                service_module = importlib.import_module("backend.service")
                main_module = importlib.import_module("backend.main")
                routes_module = importlib.import_module("backend.routes")
                service = service_module.AgentService.__new__(service_module.AgentService)
                service.root_dir = ROOT
                service.settings = replace(Settings.load(ROOT), database_file=root / "test.sqlite3", tool_workspace_dir=root / "workspace", memory_file=root / "memory.json", model_provider="mock", model_name="mock-local")
                service.database = AvelinDatabase(service.settings.database_file)
                service._agents = {}
                service._connector_factories = []
                with patch.object(routes_module, "agent_service", service), patch.object(main_module, "agent_service", service):
                    client = TestClient(main_module.app)
                    tenants = []
                    for number in range(5):
                        response = client.post("/api/auth/register", json={"email": f"tenant-{number}@example.test", "username": f"stress-{number}", "password": "test-password-strong", "display_name": f"Tenant {number}"})
                        self.assertEqual(response.status_code, 200, response.text)
                        payload = response.json()
                        headers = {"Authorization": f"Bearer {payload['token']}"}
                        thread = client.post("/api/chats", headers=headers, json={"title": f"Private tenant {number}"})
                        self.assertEqual(thread.status_code, 200)
                        upload = client.post("/api/attachments", headers={**headers, "X-Filename": "private.txt", "Content-Type": "text/plain"}, content=f"ONLY-TENANT-{number}".encode())
                        self.assertEqual(upload.status_code, 200, upload.text)
                        tenants.append((headers, thread.json()["thread"]["id"], upload.json()["path"], payload["user"]["id"]))

                    for turn in range(100):
                        headers, thread_id, _, _ = tenants[0]
                        response = client.post("/api/chat/stream", headers=headers, json={"thread_id": thread_id, "message": f"Synthetic turn {turn} unique-marker-{turn}"})
                        self.assertEqual(response.status_code, 200, response.text)
                        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
                        self.assertEqual([item["type"] for item in events].count("done"), 1)
                        self.assertEqual(events[-1]["type"], "done")
                        self.assertFalse(any(item["type"] == "error" for item in events))
                        self.assertTrue(all(item["thread_id"] == thread_id for item in events))

                    main_headers, main_thread, main_upload, _ = tenants[0]
                    main_bootstrap = client.get(f"/api/bootstrap?thread_id={main_thread}", headers=main_headers)
                    self.assertEqual(main_bootstrap.status_code, 200)
                    data = main_bootstrap.json()
                    self.assertEqual(data["active_thread"]["message_count"], 200)
                    with service.database.connect() as connection:
                        rows = connection.execute("SELECT role,status,content FROM messages WHERE thread_id=? ORDER BY id", (main_thread,)).fetchall()
                    self.assertEqual(len(rows), 200)
                    self.assertEqual(sum(row["role"] == "user" for row in rows), 100)
                    self.assertEqual(sum(row["role"] == "assistant" and row["status"] == "complete" for row in rows), 100)

                    for number, (headers, thread_id, upload_path, _) in enumerate(tenants[1:], start=1):
                        forbidden = client.get(f"/api/bootstrap?thread_id={main_thread}", headers=headers)
                        self.assertEqual(forbidden.status_code, 404)
                        forbidden_message = client.post("/api/chat/stream", headers=headers, json={"thread_id": thread_id, "message": "Read this", "attachments": [main_upload]})
                        self.assertEqual(forbidden_message.status_code, 404)
                        own = client.get(f"/api/bootstrap?thread_id={thread_id}", headers=headers)
                        self.assertEqual(own.status_code, 200)
                        self.assertEqual(own.json()["active_thread"]["message_count"], 0)
                        self.assertNotIn("unique-marker-", own.text)
                        own_file = client.get(f"/api/workspace/files/{upload_path}", headers=headers)
                        self.assertEqual(own_file.status_code, 200)
                        self.assertEqual(own_file.text, f"ONLY-TENANT-{number}")
                        other_file = client.get(f"/api/workspace/files/{main_upload}", headers=headers)
                        self.assertEqual(other_file.status_code, 404)

                    def tenant_work(number: int) -> tuple[int, list[dict]]:
                        headers, thread_id, _, _ = tenants[number]
                        local_client = TestClient(main_module.app)
                        for turn in range(10):
                            response = local_client.post("/api/chat/stream", headers=headers, json={"thread_id": thread_id, "message": f"Parallel tenant {number} item {turn}"})
                            self.assertEqual(response.status_code, 200, response.text)
                            events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
                            self.assertEqual(events[-1]["type"], "done")
                            self.assertEqual(sum(event["type"] == "done" for event in events), 1)
                            self.assertTrue(all(event["thread_id"] == thread_id for event in events))
                        with service.database.connect() as connection:
                            rows = connection.execute("SELECT role,status,content FROM messages WHERE thread_id=?", (thread_id,)).fetchall()
                        return number, [dict(row) for row in rows]

                    with ThreadPoolExecutor(max_workers=5) as pool:
                        parallel_results = list(pool.map(tenant_work, range(5)))
                    for number, records in parallel_results:
                        self.assertEqual(len(records), 220 if number == 0 else 20)
                        self.assertTrue(all(record["status"] == "complete" for record in records))
                        own_markers = [record["content"] for record in records if record["role"] == "user" and record["content"].startswith("Parallel tenant")]
                        self.assertEqual(len(own_markers), 10)
                        self.assertTrue(all(f"Parallel tenant {number} " in content for content in own_markers))


if __name__ == "__main__":
    unittest.main()
