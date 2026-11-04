from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_agent.tools.base import ToolResult
from ai_agent.types import ModelResponse, ToolCall
from ai_agent.agent import ToolOutcome, _unfinished_completion_text
from tests.test_runtime_audit import isolated_agent, ScriptedProvider


class RecordingProvider(ScriptedProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        return super().generate(**kwargs)


def search(index):
    return ModelResponse(tool_calls=[ToolCall("web_search", {"query": f"product query variation {index}"}, f"search-{index}")])


def stub_search(agent, calls):
    tool = agent.tools.get("web_search")
    tool.handler = lambda args: calls.append(args) or ToolResult("failed", display_text="Web search returned no readable results.")


class SearchConvergenceTests(unittest.TestCase):
    def test_repeated_result_sets_move_to_reading_before_more_query_variations(self):
        with isolated_agent([]) as agent:
            provider = RecordingProvider([search(1), search(2),
                ModelResponse(tool_calls=[ToolCall("research_read", {"url": "https://example.com/source"}, "read")]),
                ModelResponse(text="Прочитанный источник не подтверждает ID.")])
            agent.provider = provider
            calls = []
            finder = agent.tools.get("web_search")
            finder.handler = lambda args: calls.append(args) or ToolResult("completed", output="Search query: varies\nResults:\n1. Source\n   URL: https://example.com/source\n   Snippet: Discovery only.")
            reader = agent.tools.get("research_read")
            reader.handler = lambda _: ToolResult("completed", output="The source has no identification evidence.")
            reader.output_schema = {"type": "string"}
            self.assertIn("не подтверждает", agent.respond("Найди ID персонажа по источникам"))
            self.assertEqual(len(calls), 2)
            self.assertNotIn("web_search", [tool["name"] for tool in provider.requests[2]["tools"]])

    def test_npc_fallback_preserves_observations_without_unrelated_price_language(self):
        outcome = ToolOutcome(tool_names=["analyze_image", "web_search"], statuses=["completed", "blocked"],
                              results=['{"text":"NPC в оранжевой куртке, номер 12. ID не виден."}', 'Repeated call stopped.'])
        text = _unfinished_completion_text(outcome, "Какой ID у этого NPC?")
        self.assertIn("номер 12", text)
        self.assertIn("идентификацию", text)
        self.assertNotIn("цен", text)

    def test_varied_queries_are_bounded_and_final_answer_is_reserved(self):
        for streaming in [False, True]:
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                provider = RecordingProvider([search(i) for i in range(4)] + [ModelResponse(text="Модель и цена не подтверждены. Нужна читаемая маркировка.")])
                agent.provider = provider
                calls = []
                stub_search(agent, calls)
                text = ("".join(event.get("text", "") for event in agent.respond_stream("Определи товар и проверь текущую цену") if event["type"] == "delta")
                        if streaming else agent.respond("Определи товар и проверь текущую цену"))
                self.assertEqual(len(calls), 3)
                self.assertEqual(len(provider.requests), 5)
                self.assertNotIn("web_search", [tool["name"] for tool in provider.requests[3]["tools"]])
                self.assertEqual(provider.requests[4]["tools"], [])
                self.assertIn("не подтверждены", text)
                self.assertNotIn("предел шагов", text)
                native_calls = [call.call_id for message in provider.requests[-1]["messages"] for call in message.tool_calls]
                native_results = [message.tool_call_id for message in provider.requests[-1]["messages"] if message.tool_call_id]
                self.assertEqual(native_calls, native_results)

    def test_reading_a_source_reopens_search_for_subsequent_evidence(self):
        with isolated_agent([]) as agent:
            provider = RecordingProvider([search(i) for i in range(3)] + [
                ModelResponse(tool_calls=[ToolCall("research_read", {"url": "https://example.com/product"}, "read-1")]),
                search(5), ModelResponse(text="Источник прочитан, данные проверены.")])
            agent.provider = provider
            calls = []
            stub_search(agent, calls)
            reader = agent.tools.get("research_read")
            reader.handler = lambda _: ToolResult("completed", output="Verified source facts")
            reader.output_schema = {"type": "string"}
            text = agent.respond("Сравни сведения о товаре по источникам")
            self.assertEqual(text, "Источник прочитан, данные проверены.")
            self.assertEqual(len(calls), 4)
            self.assertIn("web_search", [tool["name"] for tool in provider.requests[4]["tools"]])

    def test_provider_ignoring_final_phase_cannot_execute_late_write_and_keeps_photo_result(self):
        with isolated_agent([]) as agent:
            provider = RecordingProvider([
                ModelResponse(tool_calls=[ToolCall("analyze_image", {"path": "attachments/watch.png"}, "vision-1")]),
                *[search(i) for i in range(4)],
                ModelResponse(tool_calls=[ToolCall("write_file", {"path": "invented-price.txt", "content": "Verified price: 999"}, "write-late")])])
            agent.provider = provider
            calls = []
            stub_search(agent, calls)
            agent.policy.image_attachment_consent = {"attachments/watch.png"}
            vision = agent.tools.get("analyze_image")
            vision.handler = lambda _: ToolResult("completed", output={"text": "Видно TISSOT и SWISSMATIC. Точный референс не виден.", "provider": "fixture", "model": "fixture"})
            events = list(agent.respond_stream("Определи часы на фото и их цену"))
            final = "".join(event.get("text", "") for event in events if event["type"] == "delta")
            self.assertEqual(len(calls), 3)
            self.assertIn("SWISSMATIC", final)
            self.assertIn("подтвердить не удалось", final)
            self.assertFalse((agent.files.workspace_dir / "invented-price.txt").exists())
            self.assertEqual(provider.requests[-1]["tools"], [])


if __name__ == "__main__":
    unittest.main()
