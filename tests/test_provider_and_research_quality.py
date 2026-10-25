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

from ai_agent.agent import Agent, ToolOutcome, _tool_completion_text
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


class RepeatingSearchProvider(TaggedProvider):
    def __init__(self):
        super().__init__("repeating-search", "")
        self.calls = 0

    def stream(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls % 2 == 1:
            yield ModelStreamChunk(
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            "web_search",
                            {"query": "cheap fiction Apple Books", "limit": 5, "country": "us"},
                        )
                    ]
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

    def test_apple_api_timeout_falls_back_without_leaking_raw_error(self):
        polluted_rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Mail.ru</title><link>https://mail.ru/</link><description>Mail and news</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch(
            "ai_agent.tools.http.request.urlopen",
            side_effect=[TimeoutError("timed out"), polluted_rss],
        ):
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
                    "trackViewUrl": "https://books.apple.com/us/book/popular-b/id22",
                    "genres": ["Fiction"],
                },
                {
                    "kind": "ebook",
                    "trackId": 11,
                    "trackName": "Popular A",
                    "artistName": "Author A",
                    "formattedPrice": "199 ₽",
                    "price": 199.0,
                    "trackViewUrl": "https://books.apple.com/us/book/popular-a/id11",
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
        self.assertIn("/us/books/top-paid/", urls[0])
        self.assertIn("/lookup?", urls[1])
        self.assertLess(result.index("Popular A"), result.index("Popular B"))
        self.assertIn("199 ₽", result)
        self.assertIn("Top Paid", result)

    def test_apple_books_filters_adult_catalog_items(self):
        payload = {
            "resultCount": 3,
            "results": [
                {
                    "kind": "ebook",
                    "trackName": "Python for Beginners",
                    "artistName": "Safe Author",
                    "formattedPrice": "$4.99",
                    "price": 4.99,
                    "trackViewUrl": "https://books.apple.com/us/book/python-for-beginners/id1",
                    "genres": ["Computers"],
                },
                {
                    "kind": "ebook",
                    "trackName": "Python Adult Adventures",
                    "artistName": "Unsafe Author",
                    "formattedPrice": "$1.99",
                    "price": 1.99,
                    "trackViewUrl": "https://books.apple.com/us/book/python-adult/id2",
                    "genres": ["Erotica"],
                    "trackExplicitness": "explicit",
                },
                {
                    "kind": "ebook",
                    "trackName": "Unrelated Cooking",
                    "artistName": "Other Author",
                    "formattedPrice": "$2.99",
                    "price": 2.99,
                    "trackViewUrl": "https://books.apple.com/us/book/cooking/id3",
                    "genres": ["Cooking"],
                },
            ],
        }
        sandbox = HttpSandbox()
        with patch(
            "ai_agent.tools.http.request.urlopen",
            return_value=FakeHttpResponse(json.dumps(payload).encode()),
        ):
            result = sandbox.search("Python Apple Books", limit=5, country="us")

        self.assertIn("Python for Beginners", result)
        self.assertNotIn("Adult Adventures", result)
        self.assertNotIn("Unrelated Cooking", result)
        self.assertIn("topic_mismatch=1", result)

    def test_apple_tv_rejects_homepage_login_and_support_results(self):
        polluted_rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Apple</title><link>https://www.apple.com/</link><description>Apple homepage</description></item>
            <item><title>Apple Account</title><link>https://account.apple.com/login</link><description>Sign in</description></item>
            <item><title>Apple Support</title><link>https://support.apple.com/apple-tv</link><description>Support</description></item>
            <item><title>Apple TV</title><link>https://tv.apple.com/</link><description>Apple TV homepage</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", return_value=polluted_rss):
            with self.assertRaisesRegex(ValueError, "no relevant results") as raised:
                sandbox.search(
                    "топ гангстерских фильмов доступных по подписке Apple TV",
                    limit=5,
                )

        self.assertIn("wrong_catalog", str(raised.exception))
        self.assertIn("generic_homepage", str(raised.exception))

    def test_apple_tv_accepts_only_direct_subscription_evidence(self):
        rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Crime Movie - Apple TV+</title><link>https://tv.apple.com/us/movie/crime-movie/umc.cmc.1</link><description>Gangster crime movie available with Apple TV+ subscription.</description></item>
            <item><title>Apple TV</title><link>https://tv.apple.com/</link><description>Watch shows and movies.</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", return_value=rss) as urlopen:
            result = sandbox.search(
                "gangster crime movies available with Apple TV subscription",
                limit=5,
                country="us",
            )

        requested_url = urlopen.call_args.args[0].full_url
        self.assertIn("site%3Atv.apple.com", requested_url)
        self.assertIn("Search intent: apple_tv_plus", result)
        self.assertIn("Crime Movie", result)
        self.assertNotIn("2. Apple TV", result)

    def test_apple_tv_rejects_a_different_storefront(self):
        rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Crime Movie - Apple TV+</title><link>https://tv.apple.com/gb/movie/crime-movie/umc.cmc.1</link><description>Gangster crime movie included with Apple TV+.</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", return_value=rss):
            with self.assertRaisesRegex(ValueError, "storefront_mismatch"):
                sandbox.search(
                    "gangster crime movies available with Apple TV subscription",
                    limit=5,
                    country="us",
                )

    def test_russian_apple_tv_query_adds_english_genre_terms(self):
        rss = FakeHttpResponse(
            b"""<?xml version="1.0"?><rss><channel>
            <item><title>Crime Movie - Apple TV+</title><link>https://tv.apple.com/us/movie/crime-movie/umc.cmc.1</link><description>Gangster crime movie included with Apple TV+.</description></item>
            </channel></rss>""",
            {"Content-Type": "application/rss+xml; charset=utf-8"},
        )
        sandbox = HttpSandbox()
        with patch("ai_agent.tools.http.request.urlopen", return_value=rss) as urlopen:
            result = sandbox.search(
                "топ гангстерских фильмов доступных по подписке Apple TV",
                limit=5,
            )

        requested_url = urlopen.call_args.args[0].full_url
        self.assertIn("gangster", requested_url)
        self.assertIn("Crime Movie", result)

    def test_docx_failure_is_not_reported_as_an_internet_failure(self):
        outcome = ToolOutcome(
            results=["Инструмент 'create_docx' не смог выполнить действие: access denied"],
            tool_names=["create_docx"],
        )

        message = _tool_completion_text(outcome)

        self.assertIn("не удалось создать Word-файл", message)
        self.assertNotIn("интернет-запрос", message)

    def test_repeated_research_request_creates_unique_docx_artifacts(self):
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
            agent.provider = RepeatingSearchProvider()
            search_result = (
                "Search query: cheap fiction Apple Books\n"
                "Search intent: apple_books\n"
                "Search provider: apple_search_api\n"
                "Storefront: us\n"
                "Quality: accepted=2; rejected=0\n"
                "Rejected reasons: none\nResults:\n"
                "1. Book One\n"
                "   URL: https://books.apple.com/us/book/book-one/id1\n"
                "   Snippet: Catalog: Apple Books. Price: $1.99. Genres: Fiction\n"
                "2. Book Two\n"
                "   URL: https://books.apple.com/us/book/book-two/id2\n"
                "   Snippet: Catalog: Apple Books. Price: $2.99. Genres: Fiction"
            )
            with patch.object(agent.http, "search", return_value=search_result):
                first = list(agent.respond_stream("Найди книги и создай Word-файл books.docx"))
                second = list(agent.respond_stream("Найди книги и создай Word-файл books.docx"))

            self.assertEqual(first[-1]["type"], "done")
            self.assertEqual(second[-1]["type"], "done")
            self.assertTrue((root / "workspace" / "books.docx").is_file())
            self.assertTrue((root / "workspace" / "books_2.docx").is_file())
            assistants = [
                item for item in agent.memory.snapshot().history if item["role"] == "assistant"
            ]
            self.assertEqual(assistants[-2]["artifacts"][0]["path"], "books.docx")
            self.assertEqual(assistants[-1]["artifacts"][0]["path"], "books_2.docx")

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
