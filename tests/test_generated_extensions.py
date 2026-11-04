from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import json
import os
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_agent.database import AvelinDatabase, DEFAULT_USER_ID
from ai_agent.security import ToolPolicyEngine
from ai_agent.tool_factory import ToolFactory, _proposal_digest, _run_sandboxed
from ai_agent.tools.base import ToolRegistry, ToolResult
from ai_agent.types import ModelResponse, ToolCall
from tests.test_tool_factory import _tool_payload
from tests.test_runtime_audit import isolated_agent, ScriptedProvider


class GeneratedExtensionTests(unittest.TestCase):
    def test_rejected_draft_is_partial_with_review_artifact_even_after_discovery(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            payload = _tool_payload()
            payload["source_code"] = 'import os\ndef run(arguments):\n    return {}\n'
            agent.provider = ScriptedProvider([
                ModelResponse(tool_calls=[ToolCall("propose_extension", payload, "proposal")]),
                ModelResponse(tool_calls=[ToolCall("tool_search", {"query":"a missing alternate"}, "discovery")]),
                ModelResponse(text="Черновик отклонён; установка не выполнена."),
            ])
            events = list(agent.respond_stream("Подготовь и установи переиспользуемый инструмент"))
            self.assertEqual(events[-1]["outcome"], "partial")
            self.assertEqual(agent.last_turn_outcome, "partial")
            artifacts = [artifact for row in agent.memory.database.list_messages(agent.memory.thread_id) for artifact in row.get("artifacts", [])]
            self.assertTrue(any(item["extension"] == ".zip" for item in artifacts))
            self.assertFalse(agent.tools.has("friendly_greeting"))
            execute.assert_not_called()

    def test_actual_openai_and_beam_contract_alias_payloads_stage_without_execution(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            for patch_version, trailing_newline in enumerate([True, False]):
                payload = _tool_payload()
                payload.update(name="qa_sum_pair", version=f"1.0.{patch_version}",
                    source_code='def run(arguments):\n    return {"sum": arguments["left"] + arguments["right"]}' + ('\n' if trailing_newline else ''),
                    manifest={"idempotent": True,
                        "input_schema":{"type":"object","properties":{"left":{"type":"number"},"right":{"type":"number"}},"required":["left","right"],"additionalProperties":False},
                        "output_schema":{"type":"object","properties":{"sum":{"type":"number"}},"required":["sum"],"additionalProperties":False}},
                    tests=[{"arguments":{"left":19,"right":23},"expected":{"sum":42}}, {"arguments":{"left":2,"right":3},"expected":{"sum":5}}])
                result = agent.tools.invoke("propose_extension", payload)
                self.assertTrue(result.ok, result.text())
                self.assertEqual(result.output["status"], "ready", result.text())
                proposal = agent.memory.database.get_extension_proposal(agent.memory.user_id, result.output["proposal_id"])
                self.assertEqual(proposal["tests"][0], {"input":{"left":19,"right":23},"expected":{"sum":42}})
            execute.assert_not_called()

    def test_conflicting_test_aliases_and_immutable_draft_recovery_feedback(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            conflicting = _tool_payload()
            conflicting["tests"][0]["arguments"] = {"name":"Different"}
            with self.assertRaisesRegex(ValueError, "conflicting input"):
                agent.tool_factory.create_proposal(**conflicting)
            bad = _tool_payload()
            bad["source_code"] = 'import os\ndef run(arguments):\n    return {}\n'
            rejected = agent.tool_factory.create_proposal(**bad)
            self.assertEqual(rejected["status"], "rejected")
            with self.assertRaisesRegex(ValueError, "version=1.0.1"):
                agent.tool_factory.create_proposal(**_tool_payload())
            corrected = agent.tool_factory.create_proposal(**_tool_payload("1.0.1"))
            self.assertEqual(corrected["status"], "ready")
            self.assertEqual(agent.memory.database.get_extension_proposal(agent.memory.user_id, rejected["id"])["source_code"], bad["source_code"])
            execute.assert_not_called()

    def test_timed_out_install_never_activates_later(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.policy.decide(waiting.approval_id, "approved")
            agent.tool_factory._install_tool.timeout_seconds = 0.02
            def slow_worker(*_args, **_kwargs):
                time.sleep(0.08)
                return ToolResult("completed", output={"message": "Hello Avelin"})
            with patch("ai_agent.tool_factory._run_sandboxed", side_effect=slow_worker):
                with self.assertRaises(ValueError):
                    agent.tool_factory.request_install(proposal["id"], proposal["version"])
                time.sleep(0.15)
            self.assertEqual(agent.memory.database.get_extension_proposal(agent.memory.user_id, proposal["id"])["status"], "ready")
            self.assertFalse(agent.tools.has("friendly_greeting"))
            self.assertFalse((agent.files.workspace_dir / "skills/friendly_greeting/1.0.0/SKILL.md").exists())

    def test_timed_out_generated_execution_never_writes_late_artifacts(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.policy.decide(waiting.approval_id, "approved")
            agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.tools.get("friendly_greeting").timeout_seconds = 0.02
            def slow_worker(*_args, **_kwargs):
                time.sleep(0.08)
                return ToolResult("completed", output={"message": "Hello Tester"})
            with patch("ai_agent.tool_factory._run_sandboxed", side_effect=slow_worker):
                result = agent.tools.invoke("friendly_greeting", {"name": "Tester"})
                self.assertFalse(result.ok)
                time.sleep(0.15)
            self.assertEqual(list((agent.files.workspace_dir / "outputs").rglob("result-*.json")), [])
            self.assertEqual(list((agent.files.workspace_dir / ".cache").rglob("*.json")), [])

    def test_corrupted_artifact_is_preserved_and_recovered_cache_checksum_is_verified(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.policy.decide(waiting.approval_id, "approved")
            agent.tool_factory.request_install(proposal["id"], proposal["version"])
            first = agent.tools.invoke("friendly_greeting", {"name": "Tester"})
            original = agent.files.workspace_dir / json.loads(first.text())["artifact"]["path"]
            original.write_text('User edit must be preserved', encoding="utf-8")
            with patch("ai_agent.tool_factory._run_sandboxed", side_effect=AssertionError("Use intact cache")):
                second = agent.tools.invoke("friendly_greeting", {"name": "Tester"})
            self.assertTrue(second.ok, second.text())
            recovered = json.loads(second.text())["artifact"]
            self.assertNotEqual(recovered["path"], original.relative_to(agent.files.workspace_dir).as_posix())
            self.assertEqual(original.read_text("utf-8"), "User edit must be preserved")
            self.assertEqual(json.loads((agent.files.workspace_dir / recovered["path"]).read_text("utf-8"))["result"], {"message": "Hello Tester"})
            self.assertEqual(agent.tool_factory.artifact_for_invocation("friendly_greeting", {"name": "Tester"})["path"], recovered["path"])
            _, cache, _ = agent.tool_factory._execution_paths(proposal, {"name": "Tester"})
            corrupted = json.loads(cache.read_text("utf-8"))
            corrupted["result"] = {"message": "Wrong but schema-valid cache"}
            cache.write_text(json.dumps(corrupted), encoding="utf-8")
            with patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
                third = agent.tools.invoke("friendly_greeting", {"name": "Tester"})
                self.assertEqual(execute.call_count, 1)
            self.assertEqual(third.output, {"message": "Hello Tester"})
            self.assertFalse(json.loads(third.text())["cached"])
            cache.write_text("[]", encoding="utf-8")
            with patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
                self.assertTrue(agent.tools.invoke("friendly_greeting", {"name": "Tester"}).ok)
                self.assertEqual(execute.call_count, 1)

    def test_proposal_never_executes_and_exports_a_reviewable_bundle(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            result = agent.tools.invoke("propose_extension", _tool_payload())
            self.assertTrue(result.ok, result.text())
            execute.assert_not_called()
            self.assertEqual(result.output["status"], "ready")
            target = agent.files.workspace_dir / result.output["review_artifact"]["path"]
            self.assertTrue(target.is_relative_to(agent.files.workspace_dir / "outputs"))
            with ZipFile(target) as archive:
                self.assertTrue({"SKILL.md", "tool.py", "tests.json", "manifest.json", "REVIEW.md"}.issubset(archive.namelist()))
                self.assertIn("NOT been executed", archive.read("REVIEW.md").decode())
            self.assertFalse(agent.tools.has("friendly_greeting"))
            search = agent.tools.invoke("tool_search", {"query": "greeting"})
            self.assertTrue(search.ok, search.text())

    def test_native_chat_install_and_execution_each_require_approval_and_return_artifact(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            proposal = agent.tools.invoke("propose_extension", _tool_payload()).output
            install = {key: proposal[key] for key in ("proposal_id", "version", "content_sha256")}
            agent.provider = ScriptedProvider([ModelResponse(tool_calls=[ToolCall("install_extension", install, "install")])])
            events = list(agent.respond_stream("Создай и установи повторяемый инструмент приветствия."))
            approval = next(event["approval"] for event in events if event["type"] == "approval")
            execute.assert_not_called()
            self.assertFalse(agent.tools.has("friendly_greeting"))
            agent.policy.decide(approval["id"], "approved")
            agent.provider = ScriptedProvider([ModelResponse(tool_calls=[ToolCall("friendly_greeting", {"name": "Tester"}, "run")])])
            events = list(agent.resume_chat_action_stream(approval["id"]))
            self.assertEqual(execute.call_count, 1)  # Contract test, only after approval.
            run_approval = next(event["approval"] for event in events if event["type"] == "approval")
            self.assertTrue(agent.tools.has("friendly_greeting"))
            self.assertTrue((agent.files.workspace_dir / "skills/friendly_greeting/1.0.0/SKILL.md").is_file())
            agent.policy.decide(run_approval["id"], "approved")
            agent.provider = ScriptedProvider([ModelResponse(text="Готово, результат доступен в файле.")])
            list(agent.resume_chat_action_stream(run_approval["id"]))
            self.assertEqual(execute.call_count, 2)
            rows = agent.memory.database.list_messages(agent.memory.thread_id)
            artifacts = [artifact for row in rows for artifact in row.get("artifacts", [])]
            self.assertTrue(any(item["path"].startswith("outputs/extensions/friendly_greeting/") for item in artifacts))

    def test_install_digest_and_failing_contract_never_activate(self):
        with isolated_agent([]) as agent:
            payload = _tool_payload()
            payload["tests"][0]["expected"] = {"message": "Wrong expectation"}
            proposal = agent.tool_factory.create_proposal(**payload)
            self.assertEqual(proposal["status"], "ready")
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.policy.decide(waiting.approval_id, "approved")
            with self.assertRaisesRegex(ValueError, "contract tests"):
                agent.tool_factory.request_install(proposal["id"], proposal["version"])
            self.assertFalse(agent.tools.has("friendly_greeting"))
            self.assertEqual(agent.memory.database.get_extension_proposal(agent.memory.user_id, proposal["id"])["status"], "rejected")

    def test_exact_cache_survives_restart_but_disabled_or_changed_code_never_reuses_it(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            agent.policy.decide(waiting.approval_id, "approved")
            agent.tool_factory.request_install(proposal["id"], proposal["version"])
            first = agent.tools.invoke("friendly_greeting", {"name": "Tester"})
            self.assertTrue(first.ok)
            self.assertFalse(json.loads(first.text())["cached"])
            registry = ToolRegistry()
            fresh = ToolFactory(agent.memory.database, agent.memory.user_id, registry, ToolPolicyEngine(agent.memory.database, agent.memory.user_id), agent.files.workspace_dir)
            with patch("ai_agent.tool_factory._run_sandboxed", side_effect=AssertionError("Cache must not execute code")):
                cached = registry.invoke("friendly_greeting", {"name": "Tester"})
                self.assertTrue(cached.ok, cached.text())
                self.assertTrue(json.loads(cached.text())["cached"])
            # Cache never changes the action's approval requirement.
            decision = fresh.policy.authorize(registry.get("friendly_greeting"), {"name": "Tester"})
            self.assertEqual(decision.outcome, "approval_required")
            fresh.disable("friendly_greeting")
            self.assertFalse(registry.has("friendly_greeting"))
            self.assertEqual(registry.invoke("friendly_greeting", {"name": "Tester"}).status, "blocked")

    def test_input_isolation_and_bounded_allocations(self):
        source = 'def run(arguments):\n    return {"value": "x" * arguments["count"]}\n'
        normal = _run_sandboxed(source, {"count": 3})
        self.assertEqual(normal.output, {"value": "xxx"})
        huge = _run_sandboxed(source, {"count": 1_000_000_000})
        self.assertFalse(huge.ok)
        self.assertIn("sequence limit", huge.text())
        hostile = _run_sandboxed('def run(arguments):\n    return open("secret.txt").read()\n', {})
        self.assertFalse(hostile.ok)
        self.assertIn("Forbidden", hostile.text())
        amplified = _run_sandboxed('def run(arguments):\n    return [arguments["text"]] * arguments["count"]\n', {"text": "x" * 10000, "count": 10000})
        self.assertFalse(amplified.ok)
        self.assertIn("byte limit", amplified.text())
        formatted = _run_sandboxed('def run(arguments):\n    return f"{[arguments[\'text\']] * arguments[\'count\']}"\n', {"text": "x" * 10000, "count": 10000})
        self.assertFalse(formatted.ok)
        self.assertIn("scalar JSON", formatted.text())

    def test_cache_key_binds_owner_content_and_inputs(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            base = agent.tool_factory._execution_paths(proposal, {"name": "A"})[0]
            changed_input = agent.tool_factory._execution_paths(proposal, {"name": "B"})[0]
            changed_version = agent.tool_factory._execution_paths({**proposal, "version": "2.0.0"}, {"name": "A"})[0]
            other = ToolFactory(agent.memory.database, "another-owner", ToolRegistry(), ToolPolicyEngine(agent.memory.database, "another-owner"), agent.files.workspace_dir)
            changed_owner = other._execution_paths(proposal, {"name": "A"})[0]
            self.assertEqual(len({base, changed_input, changed_version, changed_owner}), 4)

    def test_cache_pruning_expires_only_managed_entries_and_preserves_other_files(self):
        with isolated_agent([]) as agent:
            directory = agent.files.workspace_dir / ".cache/extensions"
            directory.mkdir(parents=True)
            for number in range(4):
                target = directory / f"{number:064x}.json"
                target.write_text("{}", encoding="utf-8")
                os.utime(target, (time.time() - number, time.time() - number))
            expired = directory / ("f" * 64 + ".json")
            expired.write_text("{}", encoding="utf-8")
            os.utime(expired, (1, 1))
            unrelated = directory / "user-notes.json"
            unrelated.write_text("preserve", encoding="utf-8")
            with patch("ai_agent.tool_factory.MAX_CACHE_ENTRIES", 2):
                agent.tool_factory._prune_cache()
            self.assertFalse(expired.exists())
            self.assertEqual(len(list(directory.glob("[0-9]*.json"))), 2)
            self.assertEqual(unrelated.read_text("utf-8"), "preserve")

    def test_skill_materializes_after_approval_and_disk_edits_do_not_change_approved_instructions(self):
        with isolated_agent([]) as agent, patch("ai_agent.tool_factory._run_sandboxed", wraps=_run_sandboxed) as execute:
            proposal = agent.tool_factory.create_proposal(kind="skill", name="qa_summary", version="1.0.0", description="Reusable short summary procedure.",
                manifest={"name":"qa_summary", "input_schema":{"type":"object"}, "output_schema":{"type":"string"}},
                permissions=[], threat_model="Instruction-only recipe; all tools retain their own permission checks.",
                skill_markdown="# Summary\n\nList verified decisions and unresolved questions.\n")
            waiting = agent.tool_factory.request_install(proposal["id"], proposal["version"])
            target = agent.files.workspace_dir / "skills/qa_summary/1.0.0/SKILL.md"
            self.assertFalse(target.exists())
            agent.policy.decide(waiting.approval_id, "approved")
            agent.tool_factory.request_install(proposal["id"], proposal["version"])
            self.assertIn("name: qa_summary", target.read_text("utf-8"))
            target.write_text("Unreviewed disk edit should never be activated", encoding="utf-8")
            self.assertIn("verified decisions", agent.tool_factory.system_prompt("BASE"))
            self.assertNotIn("Unreviewed disk edit", agent.tool_factory.system_prompt("BASE"))
            execute.assert_not_called()

    def test_changed_proposal_digest_is_rejected_after_old_approval(self):
        with isolated_agent([]) as agent:
            proposal = agent.tool_factory.create_proposal(**_tool_payload())
            expected = _proposal_digest(proposal)
            with agent.memory.database.connect() as connection:
                connection.execute("UPDATE extension_proposals SET source_code=? WHERE id=?", ('def run(arguments):\n    return "changed"\n', proposal["id"]))
            result = agent.tools.invoke("install_extension", {"proposal_id": proposal["id"], "version": proposal["version"], "content_sha256": expected})
            self.assertFalse(result.ok)
            self.assertIn("SHA-256", result.text())
            self.assertFalse(agent.tools.has("friendly_greeting"))


if __name__ == "__main__":
    unittest.main()
