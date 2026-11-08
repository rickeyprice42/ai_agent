"""Approval notices explain their cause without leaking tool inputs to push."""
import json
import unittest

from tests import test_web_push as fixture
from ai_agent.chat_approvals import PendingChatActions


class InteractionNotificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.WebPushTests()
        self.fixture.setUp()
        self.database = self.fixture.db
        self.actions = PendingChatActions(self.database)
        self.thread = self.database.create_chat_thread("alice", "Private fixture")["id"]
        self.fixture.service.subscribe("alice", self.fixture.subscription)

    def tearDown(self):
        self.fixture.tearDown()

    def approval(self, expires="2099-01-01T00:00:00+00:00"):
        return self.database.create_approval_request("alice", tool_name="analyze_image", arguments_digest="fixture",
            arguments={"path": "secret-personal-image.png"}, scope={"kind": "workspace_path", "path": "secret-personal-image.png"},
            risk="external", expires_at=expires, step_id="chat:" + self.thread)

    def save(self, approval):
        self.actions.save("alice", self.thread, approval["id"], "analyze_image",
                          {"path": "secret-personal-image.png"}, "SECRET ORIGINAL PROMPT")

    def test_notice_persists_reason_and_chat_link_and_recovery_does_not_duplicate(self):
        approval = self.approval()
        self.save(approval)
        self.save(approval)
        notices = self.database.list_in_app_notifications("alice")
        self.assertEqual(len(notices), 1)
        notice = notices[0]
        self.assertEqual(notice["kind"], "approval_required")
        self.assertEqual(notice["entity_type"], "chat")
        self.assertEqual(notice["entity_id"], self.thread)
        self.assertIn("облачной модели", notice["body"])
        self.assertEqual(PendingChatActions(self.database).list("alice", self.thread)[0]["id"], approval["id"])
        self.assertEqual(self.database.list_in_app_notifications("bob"), [])

    def test_push_explains_category_without_prompt_file_or_arguments_and_deeplinks_chat(self):
        self.save(self.approval())
        outcomes = self.fixture.service.deliver_pending()
        self.assertEqual(outcomes[0]["state"], "delivered")
        payload = self.fixture.calls[0][1]
        self.assertIn("облачной модели", payload["body"])
        self.assertEqual(payload["data"]["url"], "/?view=chat&thread=" + self.thread)
        self.assertNotIn("SECRET", json.dumps(payload))
        self.assertNotIn("secret-personal", json.dumps(payload))
        self.assertEqual(self.fixture.service.deliver_pending(), [])

    def test_expired_permission_creates_no_new_action_required_notice(self):
        self.save(self.approval("2000-01-01T00:00:00+00:00"))
        self.assertEqual(self.database.list_in_app_notifications("alice"), [])
        self.assertEqual(self.actions.list("alice", self.thread), [])


if __name__ == "__main__":
    unittest.main()
