"""Draft reuse is opt-in, atomic, owner/project scoped, and never deletes history."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_agent.database import AvelinDatabase


class ChatDraftDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.database = AvelinDatabase(Path(self.temp.name) / "test.sqlite3")
        self.user = self.database.create_user("draft@example.test", "draft-user", "not-a-real-password", "Draft User")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_default_api_semantics_create_distinct_threads(self):
        first = self.database.create_chat_thread(self.user)
        second = self.database.create_chat_thread(self.user)
        self.assertNotEqual(first["id"], second["id"])

    def test_opt_in_reuses_empty_draft_without_deleting_old_rows(self):
        first = self.database.create_chat_thread(self.user)
        second = self.database.create_chat_thread(self.user)
        before = len(self.database.list_chat_threads(self.user))
        selected = self.database.create_chat_thread(self.user, reuse_empty=True)
        again = self.database.create_chat_thread(self.user, reuse_empty=True)
        self.assertIn(selected["id"], {first["id"], second["id"], self.user})
        self.assertEqual(selected["id"], again["id"])
        self.assertEqual(len(self.database.list_chat_threads(self.user)), before)

    def test_owner_and_project_boundaries_are_preserved(self):
        second_user = self.database.create_user("other@example.test", "draft-other", "not-a-real-password", "Other User")["id"]
        first_project = self.database.create_project(self.user, "Project one")["id"]
        second_project = self.database.create_project(self.user, "Project two")["id"]
        threads = [self.database.create_chat_thread(owner, project_id=project, reuse_empty=True) for owner, project in [(self.user,None),(self.user,first_project),(self.user,second_project),(second_user,None)]]
        self.assertEqual(len({thread["id"] for thread in threads}), 4)
        self.assertEqual(self.database.create_chat_thread(self.user, project_id=first_project, reuse_empty=True)["id"], threads[1]["id"])

    def test_named_pinned_and_shared_threads_are_never_reused(self):
        named = self.database.create_chat_thread(self.user, title="Travel planning")
        pinned = self.database.create_chat_thread(self.user)
        self.database.update_chat_thread(self.user, pinned["id"], pinned=True)
        draft = self.database.create_chat_thread(self.user, reuse_empty=True)
        self.assertNotIn(draft["id"], {named["id"], pinned["id"]})
        explicit = self.database.create_chat_thread(self.user, title="New chat", reuse_empty=True)
        self.assertNotEqual(explicit["id"], draft["id"])
        group = self.database.create_project(self.user, "Shared room", shared=True)
        first = self.database.create_chat_thread(self.user, project_id=group["id"], reuse_empty=True)
        second = self.database.create_chat_thread(self.user, project_id=group["id"], reuse_empty=True)
        self.assertNotEqual(first["id"], second["id"])

    def test_parallel_new_clicks_share_one_draft(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            threads = list(executor.map(lambda _: self.database.create_chat_thread(self.user, reuse_empty=True), range(12)))
        self.assertEqual(len({thread["id"] for thread in threads}), 1)
        self.assertEqual(len(self.database.list_chat_threads(self.user)), 1)

    def test_canonical_empty_conversation_is_reused_without_second_auto_draft(self):
        draft = self.database.create_chat_thread(self.user, reuse_empty=True)
        self.assertEqual(draft["id"], self.user)
        self.assertEqual(len(self.database.list_chat_threads(self.user)), 1)
        named = self.database.create_chat_thread(self.user, title="Main conversation")
        self.database.update_chat_thread(self.user, draft["id"], pinned=True)
        next_draft = self.database.create_chat_thread(self.user, reuse_empty=True)
        self.assertNotEqual(next_draft["id"], named["id"])


class ChatDraftApiTests(unittest.TestCase):
    def test_first_streamed_message_persists_and_next_new_draft_is_separate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"DATABASE_FILE":str(root / "bootstrap.sqlite3"),"TOOL_WORKSPACE_DIR":str(root / "workspace"),"MEMORY_FILE":str(root / "memory.json"),"MODEL_PROVIDER":"mock","MODEL_NAME":"mock-local"}):
                from ai_agent.config import Settings
                from backend.service import AgentService
                from backend.main import app
                from fastapi.testclient import TestClient
                service = AgentService.__new__(AgentService)
                service.root_dir = ROOT
                service.settings = replace(Settings.load(ROOT), database_file=root / "test.sqlite3", tool_workspace_dir=root / "workspace", memory_file=root / "memory.json", model_provider="mock", model_name="mock-local")
                service.database = AvelinDatabase(service.settings.database_file)
                service._agents = {}
                service._connector_factories = []
                with patch("backend.routes.agent_service", service), patch("backend.main.agent_service", service):
                    client = TestClient(app)
                    registered = client.post("/api/auth/register",json={"email":"api-draft@example.test","username":"api-draft","password":"password123","display_name":"Draft Test"})
                    self.assertEqual(registered.status_code,200)
                    headers={"Authorization":f"Bearer {registered.json()['token']}"}
                    first=client.post("/api/chats",headers=headers,json={"reuse_empty":True}).json()["thread"]
                    again=client.post("/api/chats",headers=headers,json={"reuse_empty":True}).json()["thread"]
                    self.assertEqual(first["id"],again["id"])
                    response=client.post("/api/chat/stream",headers=headers,json={"thread_id":first["id"],"message":"First persistent turn"})
                    self.assertEqual(response.status_code,200,response.text)
                    events=[json.loads(line) for line in response.text.splitlines() if line.strip()]
                    self.assertEqual(events[-1]["type"],"done")
                    second=client.post("/api/chats",headers=headers,json={"reuse_empty":True}).json()["thread"]
                    self.assertNotEqual(second["id"],first["id"])
                    persisted=client.get(f"/api/bootstrap?thread_id={first['id']}",headers=headers).json()
                    self.assertEqual(persisted["active_thread"]["message_count"],2)
                    self.assertEqual(persisted["history"][0]["content"],"First persistent turn")
                    self.assertEqual(persisted["history"][-1]["status"],"complete")


if __name__ == "__main__":
    unittest.main()
