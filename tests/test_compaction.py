from dataclasses import replace
from pathlib import Path
import json
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
from ai_agent.types import Message
from ai_agent.database import AvelinDatabase
from backend.main import app
from backend.service import AgentService


def compacting_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="beam:cloud",
        context_window_tokens=4096,
        context_output_reserve_tokens=512,
        context_compaction_threshold=0.40,
        context_compaction_keep_recent=4,
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class ContextCompactionTest(unittest.TestCase):
    def test_three_reviewed_cycles_preserve_constraints_and_references(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = compacting_settings(Path(directory))
            agent = Agent(settings)
            prohibition = "Никогда не удаляй исходные файлы без моего разрешения."
            agent.memory.append_message(Message(role="user", content=prohibition))
            for index in range(48):
                role = "user" if index % 2 == 0 else "assistant"
                agent.memory.append_message(
                    Message(
                        role=role,
                        content=f"Историческая деталь {index}: " + ("x" * 210),
                    )
                )

            source_sets: list[set[str]] = []
            for expected_cycle in range(1, 4):
                context = agent.context.build("SYSTEM", model="beam:cloud", tools=[])
                snapshots = agent.context.compaction.list()
                pending = [item for item in snapshots if item["status"] == "pending_review"]
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["cycle_number"], expected_cycle)
                self.assertIn(prohibition, context.system_prompt)
                source_sets.append(set(pending[0]["source_message_ids"]))
                verified = agent.context.compaction.verify(pending[0]["id"])
                self.assertEqual(verified["status"], "verified")

            self.assertTrue(source_sets[0].isdisjoint(source_sets[1]))
            self.assertTrue(source_sets[0].isdisjoint(source_sets[2]))
            self.assertTrue(source_sets[1].isdisjoint(source_sets[2]))

            restarted = Agent(
                settings,
                user_id=agent.memory.user_id,
                thread_id=agent.memory.thread_id,
            )
            resumed = restarted.context.build("SYSTEM", model="beam:cloud", tools=[])
            snapshots = restarted.context.compaction.list()
            self.assertEqual(len(snapshots), 3)
            self.assertTrue(all(item["status"] == "verified" for item in snapshots))
            self.assertIn("Verified conversation compactions", resumed.system_prompt)
            self.assertIn(prohibition, resumed.system_prompt)
            self.assertEqual(resumed.trace.compaction_cycle_count, 3)
            self.assertEqual(resumed.trace.pending_compaction_count, 0)

    def test_lost_summary_detail_is_recoverable_and_rollback_uses_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(compacting_settings(Path(directory)))
            hidden_detail = "DETAIL-AT-END-9f9b"
            agent.memory.append_message(
                Message(
                    role="assistant",
                    content=("A" * 900) + hidden_detail,
                    artifacts=[{"path": "reports/original-result.docx", "name": "original-result.docx"}],
                )
            )
            for index in range(18):
                agent.memory.append_message(
                    Message(role="user", content=f"Нужно учесть пункт {index}: " + ("y" * 180))
                )

            agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            snapshot = agent.context.compaction.list()[0]
            summary_text = json.dumps(snapshot["summary"], ensure_ascii=False)
            self.assertNotIn(hidden_detail, summary_text)
            self.assertIn("reports/original-result.docx", snapshot["artifact_refs"])

            originals = agent.context.compaction.sources(snapshot["id"])
            self.assertIsNotNone(originals)
            self.assertIn(hidden_detail, originals[0]["content"])
            original_count = len(agent.memory.database.list_messages(agent.memory.thread_id))

            agent.context.compaction.verify(snapshot["id"])
            verified_context = agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            self.assertIn("Verified conversation compactions", verified_context.system_prompt)

            rolled_back = agent.context.compaction.rollback(snapshot["id"])
            self.assertEqual(rolled_back["status"], "rolled_back")
            rollback_context = agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            self.assertNotIn("Verified conversation compactions", rollback_context.system_prompt)
            self.assertEqual(
                len(agent.memory.database.list_messages(agent.memory.thread_id)),
                original_count,
            )
            restored = agent.context.compaction.sources(snapshot["id"])
            self.assertIn(hidden_detail, restored[0]["content"])
            self.assertEqual(len(agent.context.compaction.list()), 1)

    def test_pending_snapshot_blocks_additional_unreviewed_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(compacting_settings(Path(directory)))
            for index in range(50):
                agent.memory.append_message(
                    Message(role="user", content=f"Goal {index} " + ("z" * 220))
                )
            agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            snapshots = agent.context.compaction.list()
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["status"], "pending_review")

    def test_verification_rejects_changed_source_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = Agent(compacting_settings(Path(directory)))
            for index in range(24):
                agent.memory.append_message(
                    Message(role="user", content=f"Нужно сохранить исходник {index}: " + ("v" * 200))
                )
            agent.context.build("SYSTEM", model="beam:cloud", tools=[])
            snapshot = agent.context.compaction.list()[0]
            source_id = snapshot["source_message_ids"][0]
            agent.memory.database.update_message(source_id, content="Изменённый исходник")

            self.assertFalse(agent.context.compaction.source_hash_matches(snapshot["id"]))
            with self.assertRaises(ValueError):
                agent.context.compaction.verify(snapshot["id"])
            self.assertEqual(agent.context.compaction.get(snapshot["id"])["status"], "pending_review")

    def test_review_verify_and_rollback_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = compacting_settings(root)
            service = AgentService.__new__(AgentService)
            service.root_dir = ROOT
            service.settings = settings
            service.database = AvelinDatabase(
                settings.database_file,
                default_model_provider="mock",
                default_model_name="beam:cloud",
            )
            service._agents = {}

            with patch("backend.routes.agent_service", service):
                client = TestClient(app)
                registered = client.post(
                    "/api/auth/register",
                    json={
                        "email": "compaction@example.com",
                        "username": "compaction-user",
                        "password": "password123",
                        "display_name": "Compaction Test",
                    },
                )
                self.assertEqual(registered.status_code, 200)
                token = registered.json()["token"]
                user_id = registered.json()["user"]["id"]
                headers = {"Authorization": f"Bearer {token}"}
                thread = service.resolve_thread(user_id)
                agent = service.agent_for_user(user_id, thread["id"])
                for index in range(24):
                    agent.memory.append_message(
                        Message(role="user", content=f"Нужно сохранить факт {index}: " + ("q" * 200))
                    )
                agent.context.build("SYSTEM", model="beam:cloud", tools=[])

                listed = client.get(
                    f"/api/chats/{thread['id']}/compactions",
                    headers=headers,
                )
                self.assertEqual(listed.status_code, 200)
                compaction_id = listed.json()[0]["id"]
                review = client.get(
                    f"/api/chats/{thread['id']}/compactions/{compaction_id}",
                    headers=headers,
                )
                self.assertEqual(review.status_code, 200)
                self.assertGreater(len(review.json()["source_messages"]), 0)
                self.assertTrue(review.json()["source_hash_matches"])

                verified = client.post(
                    f"/api/chats/{thread['id']}/compactions/{compaction_id}/verify",
                    headers=headers,
                )
                self.assertEqual(verified.status_code, 200)
                self.assertEqual(verified.json()["status"], "verified")
                repeated_verify = client.post(
                    f"/api/chats/{thread['id']}/compactions/{compaction_id}/verify",
                    headers=headers,
                )
                self.assertEqual(repeated_verify.status_code, 409)
                rolled_back = client.post(
                    f"/api/chats/{thread['id']}/compactions/{compaction_id}/rollback",
                    headers=headers,
                )
                self.assertEqual(rolled_back.status_code, 200)
                self.assertEqual(rolled_back.json()["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
