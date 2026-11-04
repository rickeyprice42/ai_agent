"""Chat context must not silently resume the account's unrelated task queue."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_agent.agent import Agent, TASK_MUTATION_TOOLS
from ai_agent.types import Message, ModelResponse, ToolCall
from tests.test_runtime_audit import ScriptedProvider, isolated_agent


class CapturingProvider(ScriptedProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        return super().generate(**kwargs)


def answer(agent, prompt, streaming):
    if not streaming:
        return agent.respond(prompt)
    return "".join(event.get("text", "") for event in agent.respond_stream(prompt) if event["type"] == "delta")


class ChatTaskIsolationTests(unittest.TestCase):
    def test_ordinary_chat_excludes_other_thread_tasks_actions_and_constraints(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                foreign = Agent(agent.settings, user_id=agent.memory.user_id, thread_id="foreign-task-chat")
                foreign.memory.append_message(Message("user", "Создай задачу FOREIGN_TASK_GOAL. Не меняй FOREIGN_CONSTRAINT."))
                task = foreign.tasks.create_task("FOREIGN_TASK_GOAL", steps=["FOREIGN_STEP_SECRET"])
                foreign.tasks.start_next_task()
                foreign.action_log.record("create_task", "completed", {"description": task.description}, "FOREIGN_ACTION_RESULT")
                foreign.memory.append_message(Message("tool", "FOREIGN_TOOL_MESSAGE", name="create_task"))
                own_constraint = "В этом чате не публикуй данные в интернете."
                agent.memory.append_message(Message("user", own_constraint))
                agent.memory.append_message(Message("tool", "OWN_READ_RESULT", name="read_file"))
                provider = CapturingProvider([ModelResponse(text="Четыре.")])
                agent.provider = provider

                self.assertEqual(answer(agent, "Объясни, сколько будет два плюс два.", streaming), "Четыре.")
                request = provider.requests[0]
                supplied = request["system_prompt"] + "\n" + "\n".join(message.content for message in request["messages"])
                for marker in (task.id, "FOREIGN_TASK_GOAL", "FOREIGN_STEP_SECRET", "FOREIGN_ACTION_RESULT", "FOREIGN_TOOL_MESSAGE", "FOREIGN_CONSTRAINT"):
                    self.assertNotIn(marker, supplied)
                self.assertIn(own_constraint, supplied)
                self.assertIn("OWN_READ_RESULT", supplied)
                self.assertFalse(TASK_MUTATION_TOOLS & {tool["name"] for tool in request["tools"]})
                self.assertEqual(foreign.tasks.get_task(task.id).status, "executing")

    def test_hallucinated_task_creation_cannot_execute_in_an_ordinary_chat(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                provider = CapturingProvider([
                    ModelResponse(tool_calls=[ToolCall("create_task", {"description": "UNREQUESTED_TASK"}, "hallucinated-task")]),
                    ModelResponse(text="Отвечаю на вопрос без изменения очереди."),
                ])
                agent.provider = provider
                self.assertIn("без изменения", answer(agent, "Расскажи о возможностях работы с изображениями.", streaming))
                self.assertEqual(agent.tasks.list_tasks(), [])
                self.assertNotIn("create_task", {tool["name"] for tool in provider.requests[0]["tools"]})
                actions = agent.memory.database.list_action_logs(agent.memory.user_id, limit=10)
                rejected = next(action for action in actions if action["tool_name"] == "create_task")
                self.assertEqual(rejected["status"], "validation_error")

    def test_explicit_create_request_keeps_the_task_workflow_available(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                provider = CapturingProvider([
                    ModelResponse(tool_calls=[ToolCall("create_task", {"description": "REQUESTED_TASK", "steps": ["Проверить результат"]}, "requested-task")]),
                    ModelResponse(text="Задача создана в очереди."),
                ])
                agent.provider = provider
                answer(agent, "Создай задачу REQUESTED_TASK и объясни, что она появилась в очереди.", streaming)
                self.assertIn("create_task", {tool["name"] for tool in provider.requests[0]["tools"]})
                tasks = agent.tasks.list_tasks()
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0].description, "REQUESTED_TASK")
                self.assertEqual(tasks[0].steps[0].description, "Проверить результат")

    def test_explicit_resume_includes_the_authoritative_task_and_can_start_it(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([]) as agent:
                task = agent.tasks.create_task("RESUME_REQUESTED_GOAL", steps=["RESUME_REQUESTED_STEP"])
                provider = CapturingProvider([
                    ModelResponse(tool_calls=[ToolCall("start_next_task", {}, "start-requested")]),
                    ModelResponse(text="Работа возобновлена."),
                ])
                agent.provider = provider
                answer(agent, "Продолжи работу и сообщи текущий шаг.", streaming)
                self.assertIn(task.description, provider.requests[0]["system_prompt"])
                self.assertIn(task.id, provider.requests[0]["system_prompt"])
                self.assertIn("start_next_task", {tool["name"] for tool in provider.requests[0]["tools"]})
                started = agent.tasks.get_task(task.id)
                self.assertEqual(started.status, "executing")
                self.assertEqual(started.steps[0].status, "running")

    def test_bare_continue_uses_only_task_intent_from_the_same_chat(self):
        with isolated_agent([]) as agent:
            foreign = Agent(agent.settings, user_id=agent.memory.user_id, thread_id="foreign-task-chat")
            foreign.memory.append_message(Message("user", "Создай задачу FOREIGN_CONTINUE_GOAL"))
            task = foreign.tasks.create_task("FOREIGN_CONTINUE_GOAL")
            provider = CapturingProvider([ModelResponse(text="Продолжаем текущий диалог.")])
            agent.provider = provider
            agent.respond("Продолжай")
            self.assertNotIn(task.description, provider.requests[0]["system_prompt"])
            self.assertFalse(TASK_MUTATION_TOOLS & {tool["name"] for tool in provider.requests[0]["tools"]})

            agent.memory.append_message(Message("user", "Создай задачу OWN_CONTINUE_GOAL"))
            agent.tasks.create_task("OWN_CONTINUE_GOAL")
            provider = CapturingProvider([ModelResponse(text="Продолжаю очередь по твоей просьбе.")])
            agent.provider = provider
            agent.respond("Продолжай")
            self.assertIn("start_next_task", {tool["name"] for tool in provider.requests[0]["tools"]})

    def test_automation_override_blocks_task_queue_even_when_prompt_requests_mutation(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([]) as base:
                base.tasks.create_task("UNRELATED_AUTOMATION_QUEUE_GOAL")
                agent = Agent(base.settings, user_id=base.memory.user_id, thread_id="automation-isolated", task_queue_context=False)
                provider = CapturingProvider([
                    ModelResponse(tool_calls=[ToolCall("create_task", {"description": "FORBIDDEN_NESTED_TASK"}, "nested-task")]),
                    ModelResponse(text="Вложенная очередь не изменена."),
                ])
                agent.provider = provider
                answer(agent, "Создай задачу FORBIDDEN_NESTED_TASK и сообщи результат.", streaming)
                self.assertFalse(TASK_MUTATION_TOOLS & {tool["name"] for tool in provider.requests[0]["tools"]})
                self.assertNotIn("UNRELATED_AUTOMATION_QUEUE_GOAL", provider.requests[0]["system_prompt"])
                self.assertEqual([task.description for task in agent.tasks.list_tasks()], ["UNRELATED_AUTOMATION_QUEUE_GOAL"])


if __name__ == "__main__":
    unittest.main()
