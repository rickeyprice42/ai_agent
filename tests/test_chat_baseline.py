from dataclasses import replace
from io import BytesIO
from pathlib import Path
import gzip
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib import error


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.chat_output import history_for_ui
from ai_agent.config import Settings
from ai_agent.executor import _tool_status
from ai_agent.llm.base import ProviderError
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.memory import MemoryStore
from ai_agent.tools.http import HttpSandbox
from ai_agent.types import Message, ModelResponse, ModelStreamChunk, ToolCall


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.lines)


class FakeHttpResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, status: int = 200):
        self.body = body
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int = -1):
        return self.body if limit < 0 else self.body[:limit]

    def getcode(self):
        return self.status


class TwoChunkProvider:
    def stream(self, **_kwargs):
        yield ModelStreamChunk(delta="Частичный ")
        yield ModelStreamChunk(delta="ответ")
        yield ModelStreamChunk(response=ModelResponse(text="Частичный ответ"))


class FileThenEmptyProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name="write_file",
                            arguments={
                                "path": "results/games.txt",
                                "content": "1. It Takes Two\n2. Portal 2",
                            },
                        )
                    ]
                )
            )
            return
        yield ModelStreamChunk(response=ModelResponse(text=""))


class HttpThenEmptyProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name="http_request",
                            arguments={"url": "https://example.com/search", "method": "GET"},
                        )
                    ]
                )
            )
            return
        yield ModelStreamChunk(response=ModelResponse(text=""))


class NewDocxWithOverwriteProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name="create_docx",
                            arguments={
                                "path": "research/books.docx",
                                "title": "Книги",
                                "paragraphs": ["Подборка со ссылками"],
                                "overwrite": True,
                            },
                        )
                    ]
                )
            )
            return
        yield ModelStreamChunk(response=ModelResponse(text=""))


class SearchThenEmptyProvider:
    def __init__(self):
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name="web_search",
                            arguments={"query": "Python books Apple Books", "limit": 3},
                        )
                    ]
                )
            )
            return
        yield ModelStreamChunk(response=ModelResponse(text=""))


class ChatRegressionTest(unittest.TestCase):
    def test_history_survives_store_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "avelin.sqlite3"
            first = MemoryStore(database_path=database)
            first.append_message(Message(role="user", content="Первый вопрос"))
            first.append_message(Message(role="assistant", content="Первый ответ"))

            reopened = MemoryStore(
                database_path=database,
                user_id=first.user_id,
                thread_id=first.thread_id,
            )
            self.assertEqual(
                [(item["role"], item["content"]) for item in reopened.snapshot().history],
                [("user", "Первый вопрос"), ("assistant", "Первый ответ")],
            )
            threads = reopened.database.list_chat_threads(reopened.user_id)
            self.assertEqual(threads[0]["message_count"], 2)

    def test_unavailable_ollama_raises_safe_error(self):
        provider = OllamaProvider(model="baseline", base_url="http://127.0.0.1:11434")
        with patch(
            "ai_agent.llm.ollama_provider.request.urlopen",
            side_effect=error.URLError("internal host details"),
        ):
            with self.assertRaises(ProviderError) as raised:
                provider.generate("test", [Message(role="user", content="Привет")], [])
        self.assertEqual(raised.exception.code, "unavailable")
        self.assertNotIn("internal host details", raised.exception.user_message)

    def test_ollama_timeout_raises_safe_error(self):
        provider = OllamaProvider(
            model="baseline", base_url="http://127.0.0.1:11434", timeout_seconds=10
        )
        with patch(
            "ai_agent.llm.ollama_provider.request.urlopen",
            side_effect=socket.timeout("private timeout detail"),
        ):
            with self.assertRaises(ProviderError) as raised:
                provider.generate("test", [Message(role="user", content="Привет")], [])
        self.assertEqual(raised.exception.code, "timeout")
        self.assertNotIn("private timeout detail", raised.exception.user_message)

    def test_http_error_body_is_not_exposed(self):
        provider = OllamaProvider(model="baseline", base_url="https://ollama.example")
        failure = error.HTTPError(
            provider.url, 500, "failure", hdrs=None, fp=BytesIO(b"secret upstream payload")
        )
        with patch("ai_agent.llm.ollama_provider.request.urlopen", side_effect=failure):
            with self.assertRaises(ProviderError) as raised:
                provider.generate("test", [Message(role="user", content="Привет")], [])
        self.assertEqual(raised.exception.code, "http_error")
        self.assertNotIn("secret upstream payload", raised.exception.user_message)

    def test_ollama_stream_yields_deltas_and_final_response(self):
        provider = OllamaProvider(model="baseline", base_url="https://ollama.example")
        response = FakeResponse(
            [
                b'{"message":{"content":"Privet, "},"done":false}\n',
                b'{"message":{"content":"mir!"},"done":true}\n',
            ]
        )
        with patch("ai_agent.llm.ollama_provider.request.urlopen", return_value=response):
            chunks = list(provider.stream("test", [Message(role="user", content="Привет")], []))
        self.assertEqual("".join(item.delta for item in chunks), "Privet, mir!")
        self.assertEqual(chunks[-1].response.text, "Privet, mir!")

    def test_http_sandbox_decodes_gzip_error_and_marks_4xx_failed(self):
        sandbox = HttpSandbox()
        failure = error.HTTPError(
            "https://example.com/search",
            400,
            "bad request",
            hdrs={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            fp=BytesIO(gzip.compress('{"error":"invalid media"}'.encode("utf-8"))),
        )
        with patch("ai_agent.tools.http.request.urlopen", side_effect=failure):
            result = sandbox.request("https://example.com/search")
        self.assertIn("Status: 400", result)
        self.assertIn("invalid media", result)
        self.assertNotIn("�", result)
        self.assertEqual(_tool_status(result), "failed")

    def test_web_search_returns_readable_results_without_html(self):
        html = b"""<?xml version="1.0" encoding="utf-8"?>
        <rss><channel><item>
          <title>Python Essentials</title>
          <link>https://books.apple.com/us/book/id1</link>
          <description>Read the Python book on &lt;b&gt;Apple Books&lt;/b&gt;.</description>
        </item></channel></rss>"""
        sandbox = HttpSandbox()
        response = FakeHttpResponse(html, {"Content-Type": "text/html; charset=utf-8"})
        with patch("ai_agent.tools.http.request.urlopen", return_value=response):
            result = sandbox.search("Python books Apple Books", limit=3)
        self.assertIn("Python Essentials", result)
        self.assertIn("https://books.apple.com/us/book/id1", result)
        self.assertIn("Read the Python book on Apple Books.", result)
        self.assertNotIn("<a", result)

    def test_http_failure_never_becomes_raw_assistant_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = HttpThenEmptyProvider()
            raw_result = (
                "URL: https://example.com/search\nMethod: GET\nStatus: 400\n"
                "Content-Type: text/html\nBody:\n<binary garbage>"
            )
            with patch.object(agent.http, "request", return_value=raw_result):
                events = list(agent.respond_stream("Найди актуальные книги в интернете"))
            assistant_text = [event["text"] for event in events if event["type"] == "delta"][-1]
            self.assertIn("HTTP 400", assistant_text)
            self.assertNotIn("binary garbage", assistant_text)

    def test_new_file_is_not_blocked_when_model_unnecessarily_sets_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = NewDocxWithOverwriteProvider()
            events = list(agent.respond_stream("Создай новый Word-файл с подборкой книг"))
            self.assertEqual([event["type"] for event in events], ["status", "delta", "done"])
            self.assertTrue((root / "workspace" / "research" / "books.docx").is_file())
            assistant = [
                item for item in agent.memory.snapshot().history if item["role"] == "assistant"
            ][-1]
            self.assertEqual(assistant["artifacts"][0]["path"], "research/books.docx")

    def test_research_docx_is_completed_when_model_stops_after_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = SearchThenEmptyProvider()
            search_result = (
                "Search query: Python books Apple Books\nResults:\n"
                "1. Python Essentials\n"
                "   URL: https://books.apple.com/book/id1\n"
                "   Snippet: A practical Python guide.\n"
                "2. Mastering Python\n"
                "   URL: https://books.apple.com/book/id2\n"
                "   Snippet: An advanced Python guide."
            )
            with patch.object(agent.http, "search", return_value=search_result):
                events = list(
                    agent.respond_stream(
                        "Найди книги в интернете и создай Word-файл python_books.docx"
                    )
                )
            self.assertEqual(events[-1]["type"], "done")
            self.assertTrue((root / "workspace" / "python_books.docx").is_file())
            assistant = [
                item for item in agent.memory.snapshot().history if item["role"] == "assistant"
            ][-1]
            self.assertEqual(assistant["artifacts"][0]["path"], "python_books.docx")

    def test_cancelled_generation_keeps_partial_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = TwoChunkProvider()
            stream = agent.respond_stream("Обычный вопрос для модели")
            self.assertEqual(next(stream), {"type": "delta", "text": "Частичный "})
            stream.close()
            assistant = [
                item for item in agent.memory.snapshot().history if item["role"] == "assistant"
            ][-1]
            self.assertEqual(assistant["content"], "Частичный ")
            self.assertEqual(assistant["status"], "cancelled")
            self.assertIn("остановлена", assistant["error"])

    def test_successful_file_tool_has_one_user_facing_result_with_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = FileThenEmptyProvider()

            events = list(agent.respond_stream("Подготовь подборку игр в отдельном файле"))
            self.assertEqual([event["type"] for event in events], ["status", "delta", "done"])
            self.assertIn("games.txt", events[1]["text"])

            raw_history = agent.memory.snapshot().history
            assistant = [item for item in raw_history if item["role"] == "assistant"][-1]
            self.assertNotEqual(assistant["content"], "Не смог сформировать ответ.")
            self.assertEqual(assistant["artifacts"][0]["path"], "results/games.txt")

            visible_history = history_for_ui(raw_history, agent.files)
            self.assertNotIn("tool", [item["role"] for item in visible_history])
            self.assertEqual(len(visible_history), 2)
            self.assertEqual(visible_history[-1]["artifacts"][0]["name"], "games.txt")

            legacy_history = [
                {"role": "user", "content": "Создай файл", "artifacts": []},
                {
                    "role": "tool",
                    "name": "write_file",
                    "content": "Файл создан: results/games.txt\nЗаписано символов: 31",
                    "artifacts": [],
                },
                {
                    "role": "assistant",
                    "content": "Не смог сформировать ответ.",
                    "artifacts": [],
                },
            ]
            repaired = history_for_ui(legacy_history, agent.files)
            self.assertEqual([item["role"] for item in repaired], ["user", "assistant"])
            self.assertIn("games.txt", repaired[-1]["content"])
            self.assertEqual(repaired[-1]["artifacts"][0]["path"], "results/games.txt")

            legacy_http_history = [
                {"role": "user", "content": "Найди книги", "artifacts": []},
                {
                    "role": "tool",
                    "name": "http_request",
                    "content": "URL: https://example.com\nStatus: 400\nBody:\n<garbage>",
                    "artifacts": [],
                },
                {
                    "role": "assistant",
                    "content": "URL: https://example.com\nStatus: 400\nBody:\n<garbage>",
                    "artifacts": [],
                },
            ]
            repaired_http = history_for_ui(legacy_http_history, agent.files)
            self.assertEqual([item["role"] for item in repaired_http], ["user", "assistant"])
            self.assertIn("HTTP 400", repaired_http[-1]["content"])
            self.assertNotIn("<garbage>", repaired_http[-1]["content"])

            reopened = MemoryStore(
                database_path=settings.database_file,
                user_id=agent.memory.user_id,
                thread_id=agent.memory.thread_id,
            )
            reopened_assistant = [
                item for item in reopened.snapshot().history if item["role"] == "assistant"
            ][-1]
            self.assertEqual(reopened_assistant["artifacts"][0]["path"], "results/games.txt")

            with self.assertRaises(ValueError):
                agent.files.resolve_download_path("../outside.txt")


if __name__ == "__main__":
    unittest.main()
