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

from ai_agent.database import AvelinDatabase
from ai_agent.memory import MemoryStore
from backend.service import AgentService
from backend.main import app


def temporary_service(database_file: Path) -> AgentService:
    service = AgentService.__new__(AgentService)
    service.root_dir = ROOT
    service.settings = None
    service.database = AvelinDatabase(database_file)
    service._agents = {}
    service._connector_factories = []
    return service


class SharedWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "shared.sqlite3"
        self.database = AvelinDatabase(self.path)
        self.owner = self._user("owner@example.com", "owner")
        self.editor = self._user("editor@example.com", "editor")
        self.viewer = self._user("viewer@example.com", "viewer")
        self.outsider = self._user("outsider@example.com", "outsider")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _user(self, email: str, username: str) -> str:
        return self.database.create_user(email, username, "hash", username.title())["id"]

    def _invite_and_accept(self, project_id: str, user_id: str, email: str, role: str) -> None:
        invitation = self.database.create_project_invitation(
            self.owner, project_id, email, role, "2999-01-01T00:00:00+00:00"
        )
        self.database.accept_project_invitation(user_id, invitation["token"])

    def test_roles_shared_chat_and_tenant_isolation(self) -> None:
        project = self.database.create_project(self.owner, "Shared launch", "Release workspace")
        self._invite_and_accept(project["id"], self.editor, "editor@example.com", "editor")
        self._invite_and_accept(project["id"], self.viewer, "viewer@example.com", "viewer")

        self.assertEqual(self.database.get_project(self.editor, project["id"])["role"], "editor")
        self.assertEqual(self.database.get_project(self.viewer, project["id"])["role"], "viewer")
        self.assertIsNone(self.database.get_project(self.outsider, project["id"]))

        private_chat = self.database.create_chat_thread(self.owner, "Private")
        shared_chat = self.database.create_chat_thread(self.owner, "Shared", project["id"])
        self.assertIsNone(self.database.get_chat_thread(self.editor, private_chat["id"]))
        self.assertIsNotNone(self.database.get_chat_thread(self.editor, shared_chat["id"]))
        with self.assertRaises(PermissionError):
            self.database.create_chat_thread(self.viewer, "Denied", project["id"])

        item = self.database.create_shared_project_item(
            self.editor, project["id"], kind="task", title="Ship MVP", content={"text": "Run tests"}
        )
        self.assertEqual(self.database.get_shared_project_item(self.viewer, project["id"], item["id"])["title"], "Ship MVP")
        with self.assertRaises(PermissionError):
            self.database.create_shared_project_item(
                self.viewer, project["id"], kind="task", title="Denied", content={}
            )
        with self.assertRaises(PermissionError):
            self.database.create_project_invitation(
                self.editor, project["id"], "new@example.com", "viewer", "2999-01-01T00:00:00+00:00"
            )

    def test_invitation_lifecycle_removal_and_personal_data_survival(self) -> None:
        project = self.database.create_project(self.owner, "Workspace")
        invitation = self.database.create_project_invitation(
            self.owner, project["id"], "editor@example.com", "editor", "2999-01-01T00:00:00+00:00"
        )
        with self.assertRaises(PermissionError):
            self.database.accept_project_invitation(self.viewer, invitation["token"])
        self.database.accept_project_invitation(self.editor, invitation["token"])
        with self.assertRaises(ValueError):
            self.database.accept_project_invitation(self.editor, invitation["token"])

        personal = self.database.create_memory_record(
            self.editor, kind="preference", content="Private preference",
            consent_granted=True, source_type="test",
        )
        self.database.remove_project_member(self.owner, project["id"], self.editor)
        self.assertIsNone(self.database.get_project(self.editor, project["id"]))
        self.assertEqual(self.database.get_memory_record(self.editor, personal["id"])["content"], "Private preference")

    def test_shared_memory_is_explicit_and_personal_memory_never_enters_context(self) -> None:
        project = self.database.create_project(self.owner, "Workspace")
        self._invite_and_accept(project["id"], self.editor, "editor@example.com", "editor")
        thread = self.database.create_chat_thread(self.owner, "Shared", project["id"])
        self.database.create_memory_record(
            self.editor, kind="preference", content="PRIVATE-MEMORY-MARKER",
            consent_granted=True, source_type="test",
        )
        self.database.create_shared_project_item(
            self.editor, project["id"], kind="memory", title="Shared fact",
            content={"text": "SHARED-MEMORY-MARKER"}, source_type="explicit_share",
        )
        memory = MemoryStore(self.path, user_id=self.editor, thread_id=thread["id"])
        results = memory.relevant_memories("marker", limit=10)
        contents = {item.content for item in results}
        self.assertIn("SHARED-MEMORY-MARKER", contents)
        self.assertNotIn("PRIVATE-MEMORY-MARKER", contents)
        self.assertEqual(memory.snapshot().notes, [])

    def test_idor_concurrent_edits_agent_modes_and_audit(self) -> None:
        project = self.database.create_project(self.owner, "Workspace")
        self._invite_and_accept(project["id"], self.editor, "editor@example.com", "editor")
        item = self.database.create_shared_project_item(
            self.editor, project["id"], kind="decision", title="Runtime", content={"text": "Beam"}
        )
        updated = self.database.update_shared_project_item(
            self.editor, project["id"], item["id"], {"text": "Beam + fallback"}, 0
        )
        self.assertEqual(updated["revision"], 1)
        with self.assertRaisesRegex(ValueError, "другим участником"):
            self.database.update_shared_project_item(
                self.owner, project["id"], item["id"], {"text": "stale"}, 0
            )
        with self.assertRaises(ValueError):
            self.database.list_shared_project_items(self.outsider, project["id"])

        current = self.database.get_project(self.owner, project["id"])
        changed = self.database.update_project(
            self.owner, project["id"], agent_mode="explicit", expected_revision=current["revision"]
        )
        self.assertEqual(changed["agent_mode"], "explicit")
        with self.assertRaisesRegex(ValueError, "другим участником"):
            self.database.update_project(
                self.editor, project["id"], title="stale", expected_revision=current["revision"]
            )
        self.assertFalse(AgentService._agent_should_respond({"agent_mode": "mention_only"}, "hello"))
        self.assertTrue(AgentService._agent_should_respond({"agent_mode": "mention_only"}, "@Avelin help"))
        self.assertFalse(AgentService._agent_should_respond({"agent_mode": "off"}, "@Avelin help"))
        actions = {event["action"] for event in self.database.list_project_audit(self.owner, project["id"])}
        self.assertIn("shared_item.updated", actions)
        self.assertIn("invitation.accepted", actions)


class SharedWorkspaceApiTests(unittest.TestCase):
    def test_invitation_roles_idor_and_concurrent_update_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = temporary_service(Path(directory) / "api.sqlite3")
            with patch("backend.routes.agent_service", service):
                client = TestClient(app)

                def register(email: str, username: str) -> dict[str, str]:
                    response = client.post("/api/auth/register", json={
                        "email": email, "username": username,
                        "password": "password123", "display_name": username,
                    })
                    self.assertEqual(response.status_code, 200)
                    return {"Authorization": f"Bearer {response.json()['token']}"}

                owner = register("owner-api@example.com", "owner-api")
                editor = register("editor-api@example.com", "editor-api")
                viewer = register("viewer-api@example.com", "viewer-api")
                outsider = register("outsider-api@example.com", "outsider-api")
                project_response = client.post(
                    "/api/projects", headers=owner,
                    json={"title": "Shared API", "description": "tenant test"},
                )
                project = project_response.json()["project"]

                def invite(headers: dict[str, str], email: str, role: str) -> None:
                    response = client.post(
                        f"/api/projects/{project['id']}/invitations", headers=owner,
                        json={"email": email, "role": role, "expires_in_hours": 24},
                    )
                    self.assertEqual(response.status_code, 200)
                    accepted = client.post(
                        "/api/project-invitations/accept", headers=headers,
                        json={"token": response.json()["token"]},
                    )
                    self.assertEqual(accepted.status_code, 200)

                invite(editor, "editor-api@example.com", "editor")
                invite(viewer, "viewer-api@example.com", "viewer")
                self.assertEqual(
                    client.post(f"/api/projects/{project['id']}/archive", headers=editor).status_code,
                    403,
                )
                shared_chat = client.post(
                    "/api/chats", headers=owner,
                    json={"title": "Shared chat", "project_id": project["id"]},
                )
                self.assertEqual(shared_chat.status_code, 200)
                shared_thread_id = shared_chat.json()["thread"]["id"]
                self.assertEqual(
                    client.post(
                        "/api/chats", headers=viewer,
                        json={"title": "Denied", "project_id": project["id"]},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(f"/api/chats/{shared_thread_id}/remember", headers=editor).status_code,
                    403,
                )
                denied = client.post(
                    f"/api/projects/{project['id']}/shared-items", headers=viewer,
                    json={"kind": "task", "title": "Denied", "content": {}},
                )
                self.assertEqual(denied.status_code, 403)
                created = client.post(
                    f"/api/projects/{project['id']}/shared-items", headers=editor,
                    json={"kind": "task", "title": "Allowed", "content": {"text": "v1"}},
                )
                self.assertEqual(created.status_code, 200)
                item = created.json()
                first = client.patch(
                    f"/api/projects/{project['id']}/shared-items/{item['id']}", headers=editor,
                    json={"content": {"text": "v2"}, "expected_revision": 0},
                )
                self.assertEqual(first.status_code, 200)
                stale = client.patch(
                    f"/api/projects/{project['id']}/shared-items/{item['id']}", headers=owner,
                    json={"content": {"text": "stale"}, "expected_revision": 0},
                )
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(
                    client.get(f"/api/projects/{project['id']}/shared-items", headers=outsider).status_code,
                    404,
                )


if __name__ == "__main__":
    unittest.main()
