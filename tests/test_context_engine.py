from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.context import ContextBudgetError, ModelTokenEstimator
from ai_agent.types import Message, ModelResponse, ModelStreamChunk


class CapturingProvider:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.messages = []

    def stream(self, system_prompt, messages, tools):
        self.system_prompt = system_prompt
        self.messages = messages
        yield ModelStreamChunk(response=ModelResponse(text="Продолжаю с подтверждённого шага."))


def isolated_settings(root: Path, **overrides) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="beam:cloud",
        llm_max_retries=0,
        context_window_tokens=16384,
        context_output_reserve_tokens=2048,
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
        **overrides,
    )


class ContextEngineTest(unittest.TestCase):
    def test_model_profiles_are_explicit_and_unicode_aware(self):
        beam = ModelTokenEstimator("beam:cloud")
        gpt = ModelTokenEstimator("gpt-oss:120b")
        self.assertEqual(beam.profile.name, "beam-conservative")
        self.assertEqual(gpt.profile.name, "gpt-like")
        self.assertGreater(beam.count_text("Длинный русский контекст"), 1)
        self.assertNotEqual(
            beam.count_text("context " * 50),
            gpt.count_text("context " * 50),
        )

    def test_long_history_keeps_system_constraints_task_and_latest_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(isolated_settings(root))
            agent.memory.append_message(
                Message(
                    role="user",
                    content="Никогда не удаляй исходные файлы без моего явного разрешения.",
                )
            )
            for index in range(60):
                agent.memory.append_message(
                    Message(role="user", content=f"Старый вопрос {index}: " + "данные " * 35)
                )
                agent.memory.append_message(
                    Message(role="assistant", content=f"Старый ответ {index}: " + "результат " * 35)
                )
            latest = "Продолжи с последнего подтверждённого шага и назови его."
            agent.memory.append_message(Message(role="user", content=latest))
            task = agent.tasks.create_task(
                "Подготовить проверяемый отчёт",
                steps=["Собрать источники", "Проверить вывод", "Создать отчёт"],
            )
            started = agent.tasks.start_next_task()
            self.assertEqual(started.steps[0].status, "running")

            context = agent.context.build(
                "IMMUTABLE SYSTEM INSTRUCTION",
                model="beam:cloud",
                tools=agent.tools.list_for_model(),
            )

            self.assertIn("IMMUTABLE SYSTEM INSTRUCTION", context.system_prompt)
            self.assertIn("Никогда не удаляй исходные файлы", context.system_prompt)
            self.assertIn("Собрать источники", context.system_prompt)
            self.assertIn("status=running", context.system_prompt)
            self.assertEqual(context.messages[-1].content, latest)
            self.assertGreater(context.trace.omitted_message_count, 0)
            self.assertLessEqual(
                context.trace.used_input_tokens,
                context.trace.input_budget_tokens,
            )
            self.assertEqual(context.trace.output_reserve_tokens, 2048)
            self.assertGreater(context.trace.tool_schema_tokens, 0)
            pinned = {item.name for item in context.trace.components if item.pinned}
            self.assertTrue({"core_system", "grounding_rules", "user_constraints", "task"} <= pinned)

    def test_trace_is_reproducible_and_persists_across_agent_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            first = Agent(settings)
            first.memory.append_message(Message(role="user", content="Не придумывай результаты тестов."))
            first.memory.append_message(Message(role="assistant", content="Ограничение принято."))
            task = first.tasks.create_task("Проверить сборку", steps=["Запустить тесты", "Сохранить результат"])
            first.tasks.start_next_task()
            tools = first.tools.list_for_model()
            built = first.context.build("SYSTEM", model=settings.model_name, tools=tools)
            repeated = first.context.build("SYSTEM", model=settings.model_name, tools=tools)
            self.assertEqual(built.trace.fingerprint, repeated.trace.fingerprint)

            second = Agent(settings, user_id=first.memory.user_id, thread_id=first.memory.thread_id)
            persisted = second.context.latest_trace()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted["fingerprint"], built.trace.fingerprint)
            resumed = second.context.build("SYSTEM", model=settings.model_name, tools=tools)
            self.assertIn(task.description, resumed.system_prompt)
            self.assertIn("Запустить тесты", resumed.system_prompt)
            self.assertIn("status=running", resumed.system_prompt)

    def test_pinned_content_fails_closed_instead_of_being_evicted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(isolated_settings(root))
            huge_system_prompt = "NONNEGOTIABLE " * 5000
            with self.assertRaises(ContextBudgetError) as raised:
                agent.context.build(
                    huge_system_prompt,
                    model="beam:cloud",
                    tools=agent.tools.list_for_model(),
                )
            self.assertEqual(raised.exception.code, "context_overflow")
            self.assertIn("Pinned system instructions", raised.exception.user_message)

    def test_sync_agent_records_context_overflow_as_failed_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root, system_prompt="NONNEGOTIABLE " * 5000)
            agent = Agent(settings)

            with self.assertRaises(ContextBudgetError):
                agent.respond("Продолжи работу, не теряя ограничения.")

            messages = agent.memory.database.list_messages(agent.memory.thread_id)
            self.assertEqual(messages[-1]["role"], "assistant")
            self.assertEqual(messages[-1]["status"], "failed")
            self.assertIn("Pinned system instructions", messages[-1]["error"])

    def test_agent_restart_passes_running_step_to_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            first = Agent(settings)
            task = first.tasks.create_task(
                "Длинная работа",
                steps=["Уже завершённый шаг", "Текущий проверяемый шаг", "Будущий шаг"],
            )
            started = first.tasks.start_next_task()
            first.tasks.complete_running_step("Готово и проверено")
            self.assertEqual(started.id, task.id)

            restarted = Agent(
                settings,
                user_id=first.memory.user_id,
                thread_id=first.memory.thread_id,
            )
            capture = CapturingProvider()
            restarted.provider = capture
            events = list(restarted.respond_stream("Продолжи работу"))

            self.assertEqual(events[-1]["type"], "done")
            self.assertIn("Уже завершённый шаг", capture.system_prompt)
            self.assertIn("status=completed", capture.system_prompt)
            self.assertIn("Текущий проверяемый шаг", capture.system_prompt)
            self.assertIn("status=running", capture.system_prompt)
            self.assertEqual(capture.messages[-1].content, "Продолжи работу")

    def test_unrelated_memory_is_not_injected_into_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(isolated_settings(root))
            agent.memory.add_note("Секретный рецепт яблочного пирога")
            agent.memory.append_message(
                Message(role="user", content="Продолжим настройку сетевого таймаута провайдера")
            )
            context = agent.context.build(
                "SYSTEM",
                model="beam:cloud",
                tools=agent.tools.list_for_model(),
            )
            self.assertNotIn("яблочного пирога", context.system_prompt)

    def test_personal_memory_context_contains_source_and_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = Agent(isolated_settings(root))
            record = agent.memory.database.create_memory_record(
                agent.memory.user_id,
                kind="preference",
                content="Любит острый соус",
                confidence=0.87,
                consent_granted=True,
                source_type="chat_message",
                source_id="message-spicy",
            )
            agent.memory.append_message(
                Message(role="user", content="Какой острый соус добавить к рецепту?")
            )

            context = agent.context.build(
                "SYSTEM",
                model="beam:cloud",
                tools=agent.tools.list_for_model(),
            )

            self.assertIn("Любит острый соус", context.system_prompt)
            self.assertIn(f"source={record['id']}", context.system_prompt)
            self.assertIn("confidence=0.87", context.system_prompt)
            self.assertIn("provenance=chat_message:message-spicy", context.system_prompt)
            trace = agent.memory.last_retrieval_trace()
            self.assertEqual(trace["source_ids"], [record["id"]])
            self.assertGreater(trace["estimated_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
