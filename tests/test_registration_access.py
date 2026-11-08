from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient

from backend.auth import hash_password, hash_registration_access_code, verify_password
from backend.main import app
from backend.service import AgentService
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from scripts.set_registration_access_code import set_access_code


class RegistrationAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = AgentService.__new__(AgentService)
        self.service.root_dir = root
        self.service.settings = replace(
            Settings.load(Path(__file__).resolve().parents[1]),
            model_provider="mock",
            model_name="mock-local",
            reasoning_fallback_enabled=False,
            registration_access_code_hash=hash_registration_access_code("Alpha2026"),
            database_file=root / "db.sqlite",
            memory_file=root / "memory.json",
            tool_workspace_dir=root / "workspace",
        )
        self.service.database = AvelinDatabase(
            self.service.settings.database_file,
            default_model_provider="mock",
            default_model_name="mock-local",
        )
        self.service._agents = {}
        self.service._connector_factories = []

    def tearDown(self):
        self.temp.cleanup()

    def test_registration_policy_and_server_side_code_gate(self):
        body = {
            "email": "closed@example.test",
            "username": "closed-user",
            "password": "password-strong-123",
            "display_name": "Closed Test",
        }
        with patch("backend.routes.agent_service", self.service):
            client = TestClient(app)
            policy = client.get("/api/auth/registration-policy")
            self.assertEqual(policy.status_code, 200)
            self.assertEqual(policy.json()["access_code_required"], True)
            self.assertNotIn("Alpha2026", policy.text)

            self.assertEqual(client.post("/api/auth/register", json=body).status_code, 403)
            self.assertEqual(
                client.post("/api/auth/register", json={**body, "access_code": "Wrong2026"}).status_code,
                403,
            )
            registered = client.post(
                "/api/auth/register",
                json={**body, "access_code": "Alpha2026"},
            )
            self.assertEqual(registered.status_code, 200, registered.text)

    def test_empty_configuration_keeps_existing_open_registration_contract(self):
        self.service.settings = replace(self.service.settings, registration_access_code_hash="")
        with patch("backend.routes.agent_service", self.service):
            client = TestClient(app)
            self.assertEqual(
                client.get("/api/auth/registration-policy").json()["access_code_required"],
                False,
            )
            response = client.post(
                "/api/auth/register",
                json={
                    "email": "open@example.test",
                    "username": "open-user",
                    "password": "password-strong-123",
                    "display_name": "Open Test",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

    def test_password_reset_preserves_chats_and_revokes_sessions(self):
        old_password = "old-password-123"
        new_password = "new-password-456"
        user = self.service.database.create_user(
            "mobile@example.test",
            "iphone16",
            hash_password(old_password),
            "Mobile Test",
        )
        self.service.database.create_session(user["id"], "session-hash", "2999-01-01 00:00:00")
        with self.service.database.connect() as connection:
            chats_before = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_threads WHERE user_id = ?",
                    (user["id"],),
                ).fetchone()[0]
            )

        updated = self.service.database.reset_user_password("iPhone16", hash_password(new_password))
        self.assertIsNotNone(updated)
        credentials = self.service.database.get_user_for_login("iphone16")
        self.assertIsNotNone(credentials)
        self.assertFalse(verify_password(old_password, credentials["password_hash"]))
        self.assertTrue(verify_password(new_password, credentials["password_hash"]))
        self.assertIsNone(self.service.database.get_user_by_session_token_hash("session-hash"))
        with self.service.database.connect() as connection:
            chats_after = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_threads WHERE user_id = ?",
                    (user["id"],),
                ).fetchone()[0]
            )
        self.assertEqual(chats_after, chats_before)

    def test_access_code_setup_stores_only_one_hash(self):
        env_file = Path(self.temp.name) / "avelin.env"
        env_file.write_text(
            "MODEL_PROVIDER=mock\nREGISTRATION_ACCESS_CODE_HASH=old\n",
            encoding="utf-8",
        )
        with patch(
            "scripts.set_registration_access_code.getpass",
            side_effect=["Private2026", "Private2026"],
        ):
            set_access_code(env_file)
        payload = env_file.read_text(encoding="utf-8")
        self.assertNotIn("Private2026", payload)
        self.assertEqual(payload.count("REGISTRATION_ACCESS_CODE_HASH="), 1)
        self.assertIn(hash_registration_access_code("Private2026"), payload)


if __name__ == "__main__":
    unittest.main()
