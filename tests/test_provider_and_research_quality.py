from dataclasses import replace
from io import BytesIO
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.llm.base import (
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderError,
    ProviderRegistry,
    ProviderRuntime,
)
from ai_agent.tools.http import HttpSandbox
from ai_agent.types import ModelResponse, ModelStreamChunk, ToolCall
from backend.service import AVAILABLE_MODELS


class FakeHttpResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.body = body
        self.headers = headers or {"Content-Type": "application/json; charset=utf-8"}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int = -1):
        return self.body if limit < 0 else self.body[:limit]

    def getcode(self):
        return self.status


class TaggedProvider:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text

    @property
    def descriptor(self):
        return ProviderDescriptor(self.name, f"{self.name}-model", ProviderCapabilities())

    def generate(self, *_args, **_kwargs):
        return ModelResponse(text=self.text)

    def stream(self, *_args, **_kwargs):
        yield ModelStreamChunk(delta=self.text)
        yield ModelStreamChunk(response=ModelResponse(text=self.text))


class FlakyProvider(TaggedProvider):
    def __init__(self):
        super().__init__("flaky", "ok")
        self.generate_calls = 0
        self.stream_calls = 0

    def generate(self, *_args, **_kwargs):
        self.generate_calls += 1
        if self.generate_calls == 1:
            raise ProviderError("temporary", "unavailable")
        return ModelResponse(text="ok")

    def stream(self, *_args, **_kwargs):
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise ProviderError("temporary", "timeout")
        yield ModelStreamChunk(delta="ok")
        yield ModelStreamChunk(response=ModelResponse(text="ok"))


class FailingProvider(TaggedProvider):
    def stream(self, *_args, **_kwargs):
        raise ProviderError("Провайдер недоступен.", "unavailable")
        yield


class OneResultSearchProvider(TaggedProvider):
    def __init__(self):
        super().__init__("one-result", "")
        self.calls = 0

    def stream(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[ToolCall("web_search", {"query": "Apple Books", "limit": 5})]
                )
            )
            return
        yield ModelStreamChunk(response=ModelResponse(text=""))


class ProviderContractTest(unittest.TestCase):
    def test_public_provider_catalog_exposes_capabilities(self):
        for provider in AVAILABLE_MODELS:
            self.assertEqual(
                set(provider["capabilities"]),
                {"streaming", "tool_calling", "structured_output", "cancellation"},
            )

    def test_registry_switches_two_providers_with_the_same_contract(self):
        registry = ProviderRegistry()
        registry.register("first", lambda: TaggedProvider("first", "one"))
        registry.register("second", lambda: TaggedProvider("second", "two"))

        first = ProviderRuntime(registry.create("first"), max_retries=0)
        second = ProviderRuntime(registry.create("second"), max_retries=0)

        self.assertEqual(first.generate("", [], []).text, "one")
        self.assertEqual(second.generate("", [], []).text, "two")
        self.assertEqual(first.descriptor.capabilities, second.descriptor.capabilities)
        self.assertTrue(first.descriptor.capabilities.tool_calling)
        self.assertTrue(first.descriptor.capabilities.cancellation)

    def test_runtime_retries_only_before_a_stream_emits_data(self):
        provider = FlakyProvider()
        runtime = ProviderRuntime(provider, max_retries=1)

        self.assertEqual(runtime.generate("", [], []).text, "ok")
        self.assertEqual(provider.generate_calls, 2)
        self.assertEqual("".join(chunk.delta for chunk in runtime.stream("", [], [])), "ok")
        self.assertEqual(provider.stream_calls, 2)

    def test_error_event_has_common_provider_and_model_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-contract",
                llm_max_retries=0,
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = FailingProvider("failed", "")
            events = list(agent.respond_stream("Привет"))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "unavailable")
        self.assertEqual(events[-1]["provider"], "mock")
        self.assertEqual(events[-1]["model"], "mock-contract")


class ResearchQualityTest(unittest.TestCase):
    def test_polluted_mail_results_are_rejected_for_apple_books(self):
        empty_apple = FakeHttpResponse(json.dumps({"resultCount": 0, "results": []}).encode())
        polluted_rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Mail.ru</title><link>https://mail.ru/</link><description>Mail and news</description></item>
            <item><title>Account Mail.Ru</title><link>https://account.mail.ru/login</link><description>Sign in</description></item>
            <item><title>News Mail.ru</title><link>https://news.mail.ru/</link><description>Latest news</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", side_effect=[empty_apple, polluted_rss]):
            with self.assertRaisesRegex(ValueError, "no relevant results"):
                sandbox.search("недорогие книги Apple Books", limit=5)

    def test_apple_books_api_provides_catalog_and_price_evidence(self):
        payload = {
            "resultCount": 2,
            "results": [
                {
                    "kind": "ebook",
                    "trackName": "Book B",
                    "artistName": "Author B",
                    "formattedPrice": "$8.99",
                    "price": 8.99,
                    "trackViewUrl": "https://books.apple.com/us/book/book-b/id2",
                    "genres": ["Fiction"],
                },
                {
                    "kind": "ebook",
                    "trackName": "Book A",
                    "artistName": "Author A",
                    "formattedPrice": "$3.99",
                    "price": 3.99,
                    "trackViewUrl": "https://books.apple.com/us/book/book-a/id1",
                    "genres": ["Fiction"],
                },
            ],
        }
        sandbox = HttpSandbox()
        with patch(
            "ai_agent.tools.http.request.urlopen",
            return_value=FakeHttpResponse(json.dumps(payload).encode()),
        ) as urlopen:
            result = sandbox.search("cheap fiction Apple Books", limit=2)

        requested_url = urlopen.call_args.args[0].full_url
        self.assertIn("media=ebook", requested_url)
        self.assertIn("entity=ebook", requested_url)
        self.assertIn("Search provider: apple_search_api", result)
        self.assertLess(result.index("Book A"), result.index("Book B"))
        self.assertIn("$3.99", result)
        self.assertIn("https://books.apple.com/us/book/book-a/id1", result)

    def test_generic_recommendation_uses_apple_chart_then_lookup(self):
        chart = {
            "feed": {
                "results": [
                    {"id": "22", "name": "Popular B"},
                    {"id": "11", "name": "Popular A"},
                ]
            }
        }
        lookup = {
            "resultCount": 2,
            "results": [
                {
                    "kind": "ebook",
                    "trackId": 22,
                    "trackName": "Popular B",
                    "artistName": "Author B",
                    "formattedPrice": "699 ₽",
                    "price": 699.0,
                    "trackViewUrl": "https://books.apple.com/ru/book/popular-b/id22",
                    "genres": ["Fiction"],
                },
                {
                    "kind": "ebook",
                    "trackId": 11,
                    "trackName": "Popular A",
                    "artistName": "Author A",
                    "formattedPrice": "199 ₽",
                    "price": 199.0,
                    "trackViewUrl": "https://books.apple.com/ru/book/popular-a/id11",
                    "genres": ["Fiction"],
                },
            ],
        }
        responses = [
            FakeHttpResponse(json.dumps(chart).encode()),
            FakeHttpResponse(json.dumps(lookup).encode()),
        ]
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", side_effect=responses) as urlopen:
            result = sandbox.search(
                "лучшие недорогие книги Apple Books рекомендации 2024",
                limit=2,
            )

        urls = [call.args[0].full_url for call in urlopen.call_args_list]
        self.assertIn("/books/top-paid/", urls[0])
        self.assertIn("/lookup?", urls[1])
        self.assertLess(result.index("Popular A"), result.index("Popular B"))
        self.assertIn("199 ₽", result)
        self.assertIn("Top Paid", result)

    def test_docx_quality_gate_refuses_a_single_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.load(ROOT),
                model_provider="mock",
                model_name="mock-local",
                llm_max_retries=0,
                database_file=root / "avelin.sqlite3",
                memory_file=root / "memory.json",
                tool_workspace_dir=root / "workspace",
            )
            agent = Agent(settings)
            agent.provider = OneResultSearchProvider()
            search_result = (
                "Search query: cheap books Apple Books\n"
                "Search provider: apple_search_api\n"
                "Quality: accepted=1; rejected=4\n"
                "Rejected reasons: irrelevant=4\nResults:\n"
                "1. Only Book\n"
                "   URL: https://books.apple.com/us/book/only/id1\n"
                "   Snippet: Catalog: Apple Books. Price: $1.99"
            )
            with patch.object(agent.http, "search", return_value=search_result):
                events = list(agent.respond_stream("Найди книги и создай Word-файл books.docx"))

            self.assertFalse((root / "workspace" / "books.docx").exists())
            text = "".join(event.get("text", "") for event in events)
            self.assertIn("Недостаточно релевантных", text)


if __name__ == "__main__":
    unittest.main()
