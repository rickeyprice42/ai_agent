from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_agent.agent import Agent, MAX_TOOL_ROUNDS
from ai_agent.config import Settings
from ai_agent.llm.base import ProviderError
from ai_agent.types import ModelResponse, ModelStreamChunk, ToolCall
from ai_agent.tools.base import Tool


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []

    def generate(self, **kwargs):
        self.inputs.append(kwargs["messages"])
        return next(self.responses)

    def stream(self, **kwargs):
        yield ModelStreamChunk(response=self.generate(**kwargs))


@contextmanager
def isolated_agent(responses):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = replace(
            Settings.load(ROOT), model_provider="mock", model_name="mock-local",
            database_file=root / "audit.sqlite3", memory_file=root / "memory.json",
            tool_workspace_dir=root / "workspace", llm_max_retries=0,
        )
        agent = Agent(settings)
        agent.provider = ScriptedProvider(responses)
        yield agent


class RuntimeAuditTests(unittest.TestCase):
    def test_native_tool_call_and_result_ids_survive_followup_without_duplicates(self):
        responses = [ModelResponse(tool_calls=[ToolCall("get_time", {}, "call-audit-1")]), ModelResponse(text="Проверено")]
        with isolated_agent(responses) as agent:
            self.assertEqual(agent.respond("Узнай текущее время инструментом"), "Проверено")
            followup = agent.provider.inputs[1]
            calls = [message for message in followup if message.tool_calls]
            outputs = [message for message in followup if message.tool_call_id]
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(outputs), 1)
            self.assertEqual(calls[0].tool_calls[0].call_id, outputs[0].tool_call_id)
            self.assertEqual(outputs[0].name, "get_time")

    def test_progress_is_emitted_before_invocation_not_after_it(self):
        responses = [ModelResponse(tool_calls=[ToolCall("get_time")]), ModelResponse(text="Готово")]
        with isolated_agent(responses) as agent, patch.object(agent.tools, "invoke", wraps=agent.tools.invoke) as invoke:
            stream = agent.respond_stream("Узнай текущее время инструментом")
            started = next(stream)
            self.assertEqual(started["phase"], "started")
            self.assertEqual(started["tool"], "get_time")
            invoke.assert_not_called()
            finished = next(stream)
            self.assertEqual(finished["phase"], "completed")
            invoke.assert_called_once()
            self.assertEqual(list(stream)[-1]["type"], "done")

    def test_six_step_workflow_is_not_cut_off_after_four_rounds(self):
        responses = [ModelResponse(tool_calls=[ToolCall("get_time")]) for _ in range(6)] + [ModelResponse(text="Все шесть проверок выполнены")]
        with isolated_agent(responses) as agent:
            result = agent.respond("Выполни шесть последовательных проверок времени")
            self.assertEqual(result, "Все шесть проверок выполнены")
            self.assertEqual(len(agent.provider.inputs), 7)

    def test_round_limit_is_truthful_about_unfinished_work(self):
        responses = [ModelResponse(tool_calls=[ToolCall("get_time")]) for _ in range(MAX_TOOL_ROUNDS)]
        with isolated_agent(responses) as agent:
            events = list(agent.respond_stream("Продолжай последовательные проверки времени"))
            text = "".join(event.get("text", "") for event in events if event["type"] == "delta")
            self.assertIn("Задача пока не завершена", text)
            self.assertNotIn("Готово", text)
            self.assertEqual(len(agent.provider.inputs), MAX_TOOL_ROUNDS)

    def test_repeated_unknown_tool_is_bounded_and_persisted_as_not_found(self):
        responses = [ModelResponse(tool_calls=[ToolCall("made_up_tool")]) for _ in range(3)]
        with isolated_agent(responses) as agent:
            events = list(agent.respond_stream("Выполни действие доступным инструментом"))
            phases = [event["phase"] for event in events if event["type"] == "status"]
            self.assertEqual(phases, ["started", "not_found", "started", "blocked"])
            self.assertEqual(len(agent.provider.inputs), 2)
            self.assertIn("Повторный одинаковый вызов остановлен", events[-2]["text"])

    def test_validation_error_can_be_recovered_with_corrected_arguments(self):
        responses = [
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "audit.txt", "unknown": True})]),
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "audit.txt", "content": "verified"})]),
            ModelResponse(text="Файл создан и проверен"),
        ]
        with isolated_agent(responses) as agent:
            events = list(agent.respond_stream("Подготовь результат в отдельном файле"))
            phases = [event["phase"] for event in events if event["type"] == "status"]
            self.assertEqual(phases, ["started", "validation_error", "started", "completed"])
            self.assertEqual((agent.files.workspace_dir / "audit.txt").read_text(), "verified")

    def test_high_risk_tool_waits_for_approval_and_does_not_execute(self):
        responses = [ModelResponse(tool_calls=[ToolCall("run_shell", {"command": "echo audit"})])]
        with isolated_agent(responses) as agent, patch.object(agent.shell, "run") as run:
            events = list(agent.respond_stream("Запусти команду через инструмент"))
            run.assert_not_called()
            self.assertIn("подтверждения", events[-2]["text"])
            self.assertEqual([event.get("phase") for event in events if event["type"] == "status"], ["started", "blocked"])

    def test_compound_browser_request_is_not_short_circuited_by_step_parser(self):
        with isolated_agent([ModelResponse(text="Подготовлен связный ответ")]) as agent:
            self.assertEqual(agent.respond("Открой в браузере https://example.com и сравни содержимое с заданными критериями"), "Подготовлен связный ответ")
            self.assertEqual(len(agent.provider.inputs), 1)

    def test_natural_presentation_request_preserves_exact_three_slides(self):
        slides = [
            {"title": "Безопасная работа с файлами", "body": "Проверяйте источник файла"},
            {"title": "Сохранение оригинала", "body": "Работайте с отдельной копией"},
            {"title": "Проверка результата", "body": "Откройте файл после сохранения"},
        ]
        responses = [ModelResponse(tool_calls=[ToolCall("create_presentation", {"path": "results/slides.pptx", "title": "Безопасная работа с файлами", "slides": slides})]), ModelResponse(text="Подготовлены три слайда")]
        with isolated_agent(responses) as agent:
            events = list(agent.respond_stream("Создай results/slides.pptx из трёх слайдов о безопасной работе с файлами. Используй create_pptx."))
            self.assertEqual(len(agent.provider.inputs), 2)
            with ZipFile(agent.files.workspace_dir / "results/slides.pptx") as archive:
                slide_paths = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
                self.assertEqual(len(slide_paths), 3)
            self.assertEqual(events[-1]["type"], "done")

    def test_search_snippet_document_and_final_are_explicitly_preliminary(self):
        search_result = "Search query: safe files\nResults:\n1. Guide one\n   URL: https://example.com/1\n   Snippet: First guide.\n2. Guide two\n   URL: https://example.com/2\n   Snippet: Second guide."
        responses = [ModelResponse(tool_calls=[ToolCall("web_search", {"query": "safe files"})]), ModelResponse(text="")]
        with isolated_agent(responses) as agent, patch.object(agent.http, "search", return_value=search_result):
            events = list(agent.respond_stream("Найди источники и создай Word-файл sources.docx"))
            final = "".join(event.get("text", "") for event in events if event["type"] == "delta")
            self.assertIn("содержимое страниц не проверено", final)
            with ZipFile(agent.files.workspace_dir / "sources.docx") as archive:
                self.assertIn("содержимое страниц не проверено", archive.read("word/document.xml").decode())

    def test_large_tool_results_are_bounded_without_losing_native_call_ids(self):
        responses = [ModelResponse(tool_calls=[ToolCall("large_fixture", {}, "large-call")]), ModelResponse(text="Получил выдержку")]
        with isolated_agent(responses) as agent:
            agent.tools.register(Tool("large_fixture", "Fixture", {"type": "object", "properties": {}}, lambda _: "доказательство " * 40000))
            self.assertEqual(agent.respond("Прочти большой тестовый результат"), "Получил выдержку")
            output = [message for message in agent.provider.inputs[1] if message.tool_call_id == "large-call"][0]
            self.assertLess(len(output.content), len("доказательство " * 40000) // 10)
            self.assertIn("excerpt", output.content)
            self.assertIn("доказательство", output.content)

    def test_direct_file_command_uses_the_same_progress_contract(self):
        with isolated_agent([]) as agent:
            agent.files.write_file("audit.txt", "готово")
            events = list(agent.respond_stream("покажи файлы"))
            self.assertEqual([event["phase"] for event in events if event["type"] == "status"], ["started", "completed"])

    def test_provider_failure_after_file_creation_is_partial_not_false_success(self):
        class FileThenFailure(ScriptedProvider):
            def stream(self, **kwargs):
                if self.inputs:
                    raise ProviderError("Модель недоступна", "unavailable")
                yield from super().stream(**kwargs)

        with isolated_agent([]) as agent:
            agent.provider = FileThenFailure([ModelResponse(tool_calls=[ToolCall("write_file", {"path": "partial.txt", "content": "verified partial result"})])])
            events = list(agent.respond_stream("Подготовь результат в отдельном файле"))
            final = "".join(event.get("text", "") for event in events if event["type"] == "delta")
            self.assertIn("Вся задача может быть не завершена", final)
            self.assertNotIn("Готово", final)
            stored = [item for item in agent.memory.snapshot().history if item["role"] == "assistant"][-1]
            self.assertEqual(stored["artifacts"][0]["path"], "partial.txt")

    def test_cancel_after_tool_preserves_artifact_and_cancelled_status(self):
        with isolated_agent([ModelResponse(tool_calls=[ToolCall("write_file", {"path": "partial.txt", "content": "verified partial result"})])]) as agent:
            stream = agent.respond_stream("Подготовь результат в отдельном файле")
            next(stream)
            next(stream)
            stream.close()
            stored = [item for item in agent.memory.snapshot().history if item["role"] == "assistant"][-1]
            self.assertEqual(stored["status"], "cancelled")
            self.assertEqual(stored["artifacts"][0]["path"], "partial.txt")


if __name__ == "__main__":
    unittest.main()
