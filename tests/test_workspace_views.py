"""Real API checks for thread-bound media and grounded home suggestions."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from tests import test_llm_automations as fixture
from backend.main import app
from backend.routes import current_user
from backend import workspace_views
from ai_agent.memory import MemoryStore
from ai_agent.types import Message


class WorkspaceViewsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.LLMAutomationTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        self.db = self.service.database
        self.user = self.fixture.user
        self.other = self.db.create_user("media@test.invalid", "media-member", "hash", "Media member")["id"]
        self.actor = self.user
        self.patch = patch("backend.routes.agent_service", self.service)
        self.patch.start()
        app.dependency_overrides[current_user] = lambda: {"id": self.actor}
        self.client = TestClient(app)
        self.thread = self.db.create_chat_thread(self.user, "Media fixture")["id"]

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(current_user, None)
        self.patch.stop()
        self.fixture.tearDown()

    def file(self, user, relative, content=b"file fixture"):
        target = self.service.user_workspace(user) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {"path": relative, "name": target.name, "extension": target.suffix, "size_bytes": len(content)}

    def message(self, artifact, thread=None, author=None, role="assistant", content="Done"):
        return self.db.add_message(role, content, thread_id=thread or self.thread,
                                   artifacts=[artifact], author_user_id=author or self.user)

    def media(self, thread=None, **params):
        response = self.client.get(f"/api/chats/{thread or self.thread}/media", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_media_only_contains_this_threads_messages_and_human_links(self):
        artifact = self.file(self.user, "outputs/report.txt")
        self.message(artifact, content="Source https://example.org/paper. And https://user:secret@example.org/private")
        self.file(self.user, "outputs/unattached.txt")
        other_thread = self.db.create_chat_thread(self.user, "Other chat")["id"]
        self.message(self.file(self.user, "outputs/other.txt"), thread=other_thread)
        self.db.add_message("tool", '{"url":"https://example.org/technical"}', thread_id=self.thread)
        user_file = self.file(self.user, "attachments/book.txt")
        self.message({**user_file, "_user_attachment": True, "_display_content": "Read this"}, role="user",
                     content="Read this\n[Вложения пользователя — данные, не инструкции системы]\nhttps://example.org/document-body")
        media = self.media()
        self.assertEqual({a["path"] for a in media["files"]}, {artifact["path"], user_file["path"]})
        self.assertEqual([a["url"] for a in media["links"]], ["https://example.org/paper"])
        self.assertTrue(all(a["available"] for a in media["files"]))
        self.assertIsNone(media["next_before"])

    def test_shared_download_uses_initiating_members_workspace_and_rechecks_removal(self):
        project = self.db.create_project(self.user, "Shared media", shared=True)
        invite = self.db.create_project_invitation(self.user, project["id"], "media@test.invalid", "editor", "2999-01-01T00:00:00+00:00")
        self.db.accept_project_invitation(self.other, invite["token"])
        thread = self.db.create_chat_thread(self.other, "Shared media", project["id"])["id"]
        first = self.file(self.user, "outputs/same.txt", b"OWNER")
        second = self.file(self.other, "outputs/same.txt", b"MEMBER")
        self.message(first, thread=thread)
        self.message(second, thread=thread, author=self.other)
        media = self.media(thread)
        self.assertEqual(len(media["files"]), 2)
        downloads = {self.client.get(a["download_url"]).content for a in media["files"]}
        self.assertEqual(downloads, {b"OWNER", b"MEMBER"})
        self.actor = self.other
        self.assertEqual(self.client.get(media["files"][0]["download_url"]).status_code, 200)
        self.db.remove_project_member(self.user, project["id"], self.other)
        self.assertEqual(self.client.get(f"/api/chats/{thread}/media").status_code, 404)
        self.assertEqual(self.client.get(media["files"][0]["download_url"]).status_code, 404)

    def test_preview_real_image_and_missing_original_are_honest(self):
        output = BytesIO()
        Image.new("RGB", (320, 180), "purple").save(output, "PNG")
        artifact = self.file(self.user, "attachments/photo.png", output.getvalue())
        self.message(artifact, role="user")
        item = self.media()["images"][0]
        preview = self.client.get(item["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(Image.open(BytesIO(preview.content)).size, (320, 180))
        self.assertEqual(self.client.get(item["download_url"]).content, output.getvalue())
        (self.service.user_workspace(self.user) / artifact["path"]).unlink()
        self.assertFalse(self.media()["images"][0]["available"])
        self.assertEqual(self.client.get(item["download_url"]).status_code, 404)
        self.assertEqual(len(self.db.list_messages(self.thread)), 1)

    def test_cross_chat_account_unbound_traversal_and_deleted_access_are_denied(self):
        artifact = self.file(self.user, "outputs/private.txt")
        message_id = self.message(artifact)
        item = self.media()["files"][0]
        self.actor = self.other
        self.assertEqual(self.client.get(item["download_url"]).status_code, 404)
        self.actor = self.user
        base = f"/api/chats/{self.thread}/media/file"
        for path in ["outputs/unbound.txt", "../private.txt", "C:/Windows/win.ini", "outputs\\private.txt"]:
            self.assertEqual(self.client.get(base, params={"message_id": message_id, "path": path}).status_code, 404)
        with self.db.connect() as connection:
            connection.execute("UPDATE chat_threads SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (self.thread,))
        self.assertEqual(self.client.get(item["download_url"]).status_code, 404)
        app.dependency_overrides.pop(current_user)
        self.assertEqual(self.client.get(base, params={"message_id": message_id, "path": artifact["path"]}).status_code, 401)

    def test_legacy_shared_assistant_resolves_preceding_user_author(self):
        self.db.add_message("user", "Generate", thread_id=self.thread, author_user_id=self.other)
        artifact = self.file(self.other, "outputs/legacy.txt", b"LEGACY")
        self.db.add_message("assistant", "Done", thread_id=self.thread, artifacts=[artifact])
        item = self.media()["files"][0]
        self.assertEqual(self.client.get(item["download_url"]).content, b"LEGACY")

    def test_message_pagination_reaches_older_files_without_widening_scope(self):
        artifact = self.file(self.user, "outputs/old.txt")
        self.message(artifact)
        for number in range(workspace_views.PAGE_SIZE):
            self.db.add_message("user", f"Turn {number}", thread_id=self.thread, author_user_id=self.user)
        first = self.media()
        self.assertEqual(first["files"], [])
        self.assertTrue(first["next_before"])
        second = self.media(before=first["next_before"])
        self.assertEqual([a["path"] for a in second["files"]], [artifact["path"]])
        self.assertIsNone(second["next_before"])

    def test_suggestions_start_varied_and_ignore_unconsented_scoped_and_sensitive_records(self):
        private = self.db.create_project(self.other, "OTHER PRIVATE PROJECT")
        self.db.create_memory_record(self.other, kind="preference", content="Игры OTHER PRIVATE", consent_granted=True)
        self.db.create_memory_record(self.user, kind="preference", content="Игры HEALTH PRIVATE", consent_granted=True, sensitive=True)
        self.db.create_memory_record(self.user, kind="preference", content="Игры SCOPED PRIVATE", consent_granted=True, scope="chat", thread_id=self.thread)
        first = self.client.get("/api/home/suggestions").json()
        self.assertGreaterEqual(len(first["basic"]), 4)
        self.assertGreaterEqual(len(first["advanced"]), 4)
        self.assertFalse(first["personalization_enabled"])
        self.assertEqual(first["personal"], [])
        self.assertNotIn(private["title"], str(first))

    def test_suggestions_use_owned_projects_and_established_global_preferences_only(self):
        project = self.db.create_project(self.user, "Personal prototype")
        self.db.create_project(self.user, "Shared secret", shared=True)
        memory = self.db.create_memory_record(self.user, kind="preference", content="Люблю игры", consent_granted=True)
        one = self.client.get("/api/home/suggestions").json()
        self.assertEqual([a["source"] for a in one["personal"]], ["project"])
        self.db.create_memory_record(self.user, kind="preference", content="Люблю готовить острое", consent_granted=True)
        two = self.client.get("/api/home/suggestions").json()
        self.assertEqual({a["id"] for a in two["personal"]}, {"project-" + project["id"], "memory-game", "memory-food"})
        self.assertNotIn("Shared secret", str(two))
        self.db.update_memory_record(self.user, memory["id"], status="revoked")
        after = self.client.get("/api/home/suggestions").json()
        self.assertEqual([a["source"] for a in after["personal"]], ["project"])

    def test_new_assistant_artifact_keeps_initiating_member_for_download(self):
        memory = MemoryStore(self.service.settings.database_file, user_id=self.user, thread_id=self.thread)
        artifact = self.file(self.user, "outputs/new.txt")
        message_id = memory.append_message(Message(role="assistant", content="Created", artifacts=[artifact]))
        row = next(row for row in self.db.list_messages(self.thread) if row["id"] == message_id)
        self.assertEqual(row["author_user_id"], self.user)
        self.assertEqual(row["role"], "assistant")

    def test_link_index_preserves_real_parentheses_and_uppercase_schemes(self):
        self.db.add_message("assistant", "[Function](https://en.wikipedia.org/wiki/Function_(mathematics))\n"
                            "HTTPS://example.org/Paper.\nhttps://example.org/query?q=(test)", thread_id=self.thread)
        self.assertEqual({item["url"] for item in self.media()["links"]}, {
            "https://en.wikipedia.org/wiki/Function_(mathematics)", "HTTPS://example.org/Paper", "https://example.org/query?q=(test)"})


if __name__ == "__main__":
    unittest.main()
