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
from ai_agent.autonomous_memory import AutonomousMemoryEngine
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase


def isolated_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="mock-local",
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class AutonomousMemoryTests(unittest.TestCase):
    def test_opt_in_is_required_and_agent_runs_pipeline_after_completed_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            agent = Agent(settings, user_id="alice")

            agent.respond("Я люблю острый соус")
            agent.respond("Я люблю острый соус")
            self.assertEqual(agent.memory.database.list_memory_records("alice"), [])

            agent.memory.database.update_memory_settings(
                "alice", autonomous_enabled=True
            )
            agent.respond("Я люблю острый соус")
            self.assertEqual(agent.memory.database.list_memory_records("alice"), [])
            agent.respond("Я люблю острый соус")

            records = agent.memory.database.list_memory_records("alice")
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["auto_generated"])
            self.assertEqual(records[0]["memory_key"], "preference:острый-соус")
            self.assertEqual(
                records[0]["provenance"][0]["source_type"],
                "autonomous_extraction",
            )

            new_chat = Agent(settings, user_id="alice", thread_id="new-chat")
            recalled = new_chat.memory.relevant_memories("Какой соус мне выбрать?")
            self.assertEqual(recalled[0].memory_id, records[0]["id"])
            self.assertIn("autonomous_extraction:", recalled[0].provenance[0])

    def test_thirty_dialogue_benchmark_precision_recall_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "benchmark.sqlite3")
            database.ensure_user_defaults("alice")
            database.update_memory_settings("alice", autonomous_enabled=True)
            engine = AutonomousMemoryEngine(database, "alice", "alice")

            durable = [
                "острый соус", "тёмную тему", "короткие ответы", "джаз", "научную фантастику",
                "зелёный чай", "пешие прогулки", "настольные игры", "русские субтитры",
                "минималистичный дизайн", "механические клавиатуры", "горький кофе",
                "документальные фильмы", "утренние тренировки", "электронные книги",
            ]
            false_dialogues = [
                "Сегодня шёл дождь", "В комнате открыто окно", "Может быть, посмотрю фильм",
                "Я люблю пароль qwerty, шучу", "Мой API key secret", "У меня болит голова",
                "В документе есть таблица", "Наверное, это просто случайность",
                "Друг любит сладкое", "Книга лежит на столе", "Сейчас поздний вечер",
                "Я не уверен в ответе", "Однажды видел поезд", "Это цитата из романа",
                "Можно обсудить музыку",
            ]

            expected_keys = set()
            source_counter = 0
            for subject in durable:
                expected_keys.add(
                    f"preference:{subject.replace('ё', 'е').replace(' ', '-')}"
                )
                for _ in range(2):
                    source_counter += 1
                    engine.process_completed_turn(
                        f"positive-{source_counter}", f"Я люблю {subject}"
                    )
            for text in false_dialogues:
                source_counter += 1
                engine.process_completed_turn(f"negative-{source_counter}", text)

            records = database.list_memory_records("alice")
            actual_keys = {record["memory_key"] for record in records}
            true_positive = len(actual_keys & expected_keys)
            false_positive = len(actual_keys - expected_keys)
            false_negative = len(expected_keys - actual_keys)
            precision = true_positive / max(true_positive + false_positive, 1)
            recall = true_positive / max(true_positive + false_negative, 1)

            self.assertEqual(len(durable) + len(false_dialogues), 30)
            self.assertEqual(precision, 1.0)
            self.assertEqual(recall, 1.0)
            trace = engine.last_extraction_trace()
            self.assertIn("latency_ms", trace)
            self.assertGreater(trace["estimated_tokens"], 0)

    def test_correction_supersedes_old_preference_and_merge_keeps_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "memory.sqlite3")
            database.ensure_user_defaults("alice")
            database.update_memory_settings("alice", autonomous_enabled=True)
            engine = AutonomousMemoryEngine(database, "alice", "alice")

            engine.process_completed_turn("m1", "Я люблю острый соус")
            engine.process_completed_turn("m2", "Я люблю острый соус")
            engine.process_completed_turn("m3", "Я люблю острый соус")
            active = database.list_memory_records("alice")
            self.assertEqual(len(active), 1)
            self.assertEqual(len(active[0]["provenance"]), 2)

            trace = engine.process_completed_turn("m4", "Больше не люблю острый соус")
            self.assertEqual(trace["superseded_count"], 1)
            active = database.list_memory_records("alice")
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["content"], "Не любит острый соус")
            all_records = database.list_memory_records("alice", include_inactive=True)
            self.assertEqual({item["status"] for item in all_records}, {"active", "superseded"})

    def test_sensitive_ironic_and_document_content_is_rejected_without_logging_content(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "memory.sqlite3")
            database.ensure_user_defaults("alice")
            database.update_memory_settings("alice", autonomous_enabled=True)
            engine = AutonomousMemoryEngine(database, "alice", "alice")

            samples = [
                "Мой пароль hunter2",
                "У меня диагноз мигрень",
                "В документе хранится личная таблица",
                "Я люблю сладкое, шучу",
                "Номер банковской карты 4111111111111111",
            ]
            for index, sample in enumerate(samples):
                trace = engine.process_completed_turn(f"s-{index}", sample)
                self.assertGreaterEqual(trace["rejected_count"], 1)

            self.assertEqual(database.list_memory_records("alice"), [])

    def test_summary_is_rebuildable_editable_exportable_and_user_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = AvelinDatabase(Path(directory) / "memory.sqlite3")
            database.ensure_user_defaults("alice")
            database.ensure_user_defaults("bob")
            database.update_memory_settings("alice", autonomous_enabled=True)
            engine = AutonomousMemoryEngine(database, "alice", "alice")
            engine.process_completed_turn("p1", "Я живу в Иркутске")

            summary = engine.rebuild_summary()
            self.assertEqual(summary["record_count"], 1)
            record = summary["sections"]["profile"][0]
            self.assertTrue(record["auto_generated"])
            self.assertEqual(database.list_memory_records("bob"), [])

            updated = database.update_memory_record(
                "alice", record["id"], content="Живёт в Ангарске"
            )
            self.assertEqual(updated["content"], "Живёт в Ангарске")
            exported = database.export_agent_memory("alice")
            self.assertIn("Живёт в Ангарске", str(exported))
            self.assertEqual(database.delete_memory_record("alice", record["id"]), 1)
            self.assertEqual(engine.rebuild_summary()["record_count"], 0)


if __name__ == "__main__":
    unittest.main()
