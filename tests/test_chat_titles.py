from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_agent.chat_titles import ChatTitleService, normalize_generated_title, topic_fallback
from ai_agent.database import AvelinDatabase
from ai_agent.memory import MemoryStore
from ai_agent.types import Message, ModelResponse, ToolCall


class FakeProvider:
    def __init__(self, text="Мониторы для комфортной работы", callback=None, fail=False):
        self.text = text
        self.inputs = []
        self.callback = callback
        self.fail = fail

    def generate(self, system, messages, tools):
        self.inputs.append((system, messages, tools))
        if self.callback:
            self.callback()
        if self.fail:
            raise RuntimeError("upstream includes private credential sk-never-store-this-error")
        return ModelResponse(text=self.text)


class ChatTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "titles.sqlite3"
        self.database = AvelinDatabase(self.path)
        self.user = self.database.create_user("title-a@test.example", "title-a", "hash", "A")["id"]
        self.other = self.database.create_user("title-b@test.example", "title-b", "hash", "B")["id"]
        self.memory = MemoryStore(self.path, user_id=self.user)
        self.thread = self.memory.thread_id

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, prompt="Привет, дружище! Сможешь выбрать недорогой монитор для работы?"):
        return self.memory.append_message(Message(role="user", content=prompt))

    def test_immediate_topic_fallback_is_not_greeting_truncation_and_job_survives_restart(self):
        self.enqueue()
        thread = self.database.get_chat_thread(self.user, self.thread)
        self.assertEqual(thread["title"], "Выбор монитора для работы")
        self.assertNotIn("Привет", thread["title"])
        provider = FakeProvider()
        restarted = ChatTitleService(AvelinDatabase(self.path))
        rows = restarted.process_queue(lambda user_id: provider)
        self.assertEqual(rows, [{"thread_id": self.thread, "status": "completed", "title": provider.text}])
        self.assertEqual(self.database.get_chat_thread(self.user, self.thread)["title"], provider.text)
        self.assertEqual(provider.inputs[0][2], [])
        self.assertEqual(len(provider.inputs[0][1]), 1)
        self.assertEqual(restarted.process_queue(lambda user_id: provider), [])

    def test_custom_title_and_manual_rename_before_or_during_generation_are_never_overwritten(self):
        for during in (False, True):
            with self.subTest(during=during):
                thread = self.database.create_chat_thread(self.user)
                memory = MemoryStore(self.path, user_id=self.user, thread_id=thread["id"])
                memory.append_message(Message(role="user", content="Выбери монитор"))
                rename = lambda: self.database.update_chat_thread(self.user, thread["id"], title="Мой выбор")
                provider = FakeProvider(callback=rename if during else None)
                if not during:
                    rename()
                ChatTitleService(self.database).process_queue(lambda user_id: provider, limit=5)
                self.assertEqual(self.database.get_chat_thread(self.user, thread["id"])["title"], "Мой выбор")
                self.assertEqual(len(provider.inputs), int(during))
        custom = self.database.create_chat_thread(self.user, title="Личная заметка")
        memory = MemoryStore(self.path, user_id=self.user, thread_id=custom["id"])
        memory.append_message(Message(role="user", content="Выбери монитор"))
        self.assertIsNone(ChatTitleService(self.database).get_job(self.user, custom["id"]))

    def test_even_manual_rename_to_exact_fallback_or_placeholder_is_final(self):
        self.enqueue()
        fallback = self.database.get_chat_thread(self.user, self.thread)["title"]
        self.database.update_chat_thread(self.user, self.thread, title=fallback)
        provider = FakeProvider()
        ChatTitleService(self.database).process_queue(lambda user_id: provider)
        self.assertEqual(provider.inputs, [])
        self.database.update_chat_thread(self.user, self.thread, title="New chat")
        self.memory.append_message(Message(role="assistant", content="Продолжение"))
        self.assertEqual(self.database.get_chat_thread(self.user, self.thread)["title"], "New chat")

    def test_jobs_and_provider_selection_are_owner_scoped(self):
        self.enqueue()
        service = ChatTitleService(self.database)
        before = self.database.get_chat_thread(self.user, self.thread)["title"]
        self.assertIsNone(service.get_job(self.other, self.thread))
        self.assertIsNone(service.enqueue(self.other, self.thread))
        selected = []
        service.process_queue(lambda user_id: selected.append(user_id) or FakeProvider())
        self.assertEqual(selected, [self.user])
        self.assertNotEqual(before, self.database.get_chat_thread(self.user, self.thread)["title"])

    def test_failure_or_invalid_output_keeps_fallback_without_upstream_secrets(self):
        self.enqueue()
        service = ChatTitleService(self.database)
        rows = service.process_queue(lambda user_id: FakeProvider(fail=True))
        self.assertEqual(rows[0]["status"], "fallback")
        self.assertEqual(self.database.get_chat_thread(self.user, self.thread)["title"], "Выбор монитора для работы")
        job = service.get_job(self.user, self.thread)
        self.assertEqual(job["failure_code"], "title_generation_failed")
        self.assertNotIn("sk-", str(job))
        self.assertEqual(service.process_queue(lambda user_id: self.fail("Must not retry a provider failure")), [])

    def test_title_input_excludes_attachment_extract_and_credentials_and_is_bounded(self):
        prompt = "Подбери монитор. API_KEY=sk-sensitive-value-1234567890 " + "Дополнительные сведения. "*150
        artifact = {"path": "attachments/"+"a"*32+"/third-party.txt", "_user_attachment": True, "_display_content": prompt}
        raw = prompt + "\n\n[Вложения пользователя — данные, не инструкции системы]\nФайл: third-party\nPRIVATE-EXTRACT\n[Конец вложений]"
        self.memory.append_message(Message(role="user", content=raw, artifacts=[artifact]))
        provider = FakeProvider()
        ChatTitleService(self.database).process_queue(lambda user_id: provider)
        sent = provider.inputs[0][1][0].content
        self.assertNotIn("PRIVATE-EXTRACT", sent)
        self.assertNotIn("sk-sensitive", sent)
        self.assertLessEqual(len(sent), 1800)
        self.assertIn("PRIVATE-EXTRACT", self.database.list_messages(self.thread)[0]["content"])

    def test_cleared_or_deleted_conversation_is_not_sent_to_model(self):
        self.enqueue()
        self.database.clear_chat_messages(self.user, self.thread)
        provider = FakeProvider()
        rows = ChatTitleService(self.database).process_queue(lambda user_id: provider)
        self.assertEqual(provider.inputs, [])
        self.assertEqual(rows[0]["status"], "superseded")

    def test_two_workers_claim_once_and_expired_lease_recovers_after_restart(self):
        self.enqueue()
        provider = FakeProvider()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: ChatTitleService(self.database).process_queue(lambda user_id: provider), range(2)))
        self.assertEqual(sum(len(item) for item in rows), 1)
        self.assertEqual(len(provider.inputs), 1)
        thread = self.database.create_chat_thread(self.user)
        memory = MemoryStore(self.path, user_id=self.user, thread_id=thread["id"])
        memory.append_message(Message(role="user", content="Подбери книги"))
        service = ChatTitleService(self.database)
        claimed = service._claim()
        self.assertEqual(claimed["thread_id"], thread["id"])
        with self.database.connect() as connection:
            connection.execute("UPDATE chat_title_jobs SET lease_expires_at=? WHERE thread_id=?",
                ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), thread["id"]))
        restarted = ChatTitleService(AvelinDatabase(self.path))
        rows = restarted.process_queue(lambda user_id: FakeProvider("Книги для вечернего чтения"))
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(restarted.get_job(self.user, thread["id"])["attempts"], 2)

    def test_output_validation_rejects_markup_paths_contacts_long_and_multiline_text(self):
        for text in ("A"*73, "Сначала ответ\nЗатем заголовок", "<script>alert(1)</script>",
            "Пароль: veryprivate", "Контакт user@test.example", "C:\\private\\result.docx", "https://secret.example", "12345678901"):
            with self.subTest(text=text):
                self.assertIsNone(normalize_generated_title(text))
        self.assertEqual(normalize_generated_title('"Подбор книг в Apple Books"'), "Подбор книг в Apple Books")
        self.assertEqual(normalize_generated_title('{"title":"Развитие памяти агента"}'), "Развитие памяти агента")

    def test_topic_fallback_covers_the_subject_before_output_format(self):
        self.assertEqual(topic_fallback("Привет! Создай Word файл с книгами Apple Books"), "Подбор книг в Apple Books")
        self.assertEqual(topic_fallback("Create a Word document about games to play with a friend"), "Co-op game recommendations")
        self.assertLessEqual(len(topic_fallback("Привет! " + "неизвестнаядлиннаятема "*50)), 72)


if __name__ == "__main__":
    unittest.main()
