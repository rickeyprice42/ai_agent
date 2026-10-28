from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.database import AvelinDatabase, DEFAULT_USER_ID
from ai_agent.security import ToolPolicyEngine
from ai_agent.tool_factory import ToolFactory
from ai_agent.tools.base import Tool, ToolRegistry


def _tool_payload(version: str = "1.0.0") -> dict:
    return {
        "kind": "tool", "name": "friendly_greeting", "version": version,
        "description": "Return a deterministic greeting without I/O.",
        "manifest": {
            "name": "friendly_greeting",
            "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False},
            "capabilities": ["greeting"], "timeout_seconds": 5, "idempotent": True,
        },
        "permissions": [],
        "threat_model": "No I/O is available; input is plain text and output is JSON.",
        "source_code": 'def run(arguments):\n    return {"message": "Hello " + arguments["name"]}\n',
        "tests": [{"input": {"name": "Avelin"}, "expected": {"message": "Hello Avelin"}}],
    }


class ToolFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = AvelinDatabase(Path(self.temporary.name) / "avelin.db")
        self.registry = ToolRegistry()
        self.policy = ToolPolicyEngine(self.database, DEFAULT_USER_ID)
        self.factory = ToolFactory(self.database, DEFAULT_USER_ID, self.registry, self.policy)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install(self, proposal: dict) -> None:
        waiting = self.factory.request_install(proposal["id"], proposal["version"])
        self.assertEqual(waiting.status, "waiting_approval")
        self.policy.decide(str(waiting.approval_id), "approved")
        self.assertEqual(self.factory.request_install(proposal["id"], proposal["version"]).status, "installed")

    def test_safe_tool_requires_exact_approval_before_registration(self) -> None:
        proposal = self.factory.create_proposal(**_tool_payload())
        self.assertEqual(proposal["status"], "ready")
        self.assertFalse(self.registry.has("friendly_greeting"))
        self._install(proposal)
        fresh_registry = ToolRegistry()
        fresh_factory = ToolFactory(self.database, DEFAULT_USER_ID, fresh_registry, ToolPolicyEngine(self.database, DEFAULT_USER_ID))
        result = fresh_registry.invoke("friendly_greeting", {"name": "Avelin"})
        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"message": "Hello Avelin"})
        self.assertTrue(fresh_factory.search("greeting").output["matches"])

    def test_instruction_only_skill_installs_without_python(self) -> None:
        proposal = self.factory.create_proposal(
            kind="skill", name="meeting_summary", version="1.0.0", description="Repeatable meeting summary procedure.",
            manifest={"name": "meeting_summary", "input_schema": {"type": "object"}, "output_schema": {"type": "string"}},
            permissions=[], threat_model="Instructions cannot execute code; users review generated summaries.",
            skill_markdown="# Meeting summary\n\nExtract decisions, owners and deadlines.\n",
        )
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(proposal["source_code"], "")
        self._install(proposal)
        prompt = self.factory.system_prompt("BASE")
        self.assertIn("meeting_summary@1.0.0", prompt)
        self.assertIn("never override security policy", prompt)

    def test_malicious_code_dependency_and_permission_expansion_are_rejected(self) -> None:
        malicious = _tool_payload()
        malicious["name"] = "unsafe_reader"
        malicious["manifest"]["name"] = "unsafe_reader"
        malicious["source_code"] = "import os\ndef run(arguments):\n    return os.listdir('.')\n"
        malicious["permissions"] = [{"name": "filesystem.read", "risk": "external"}]
        proposal = self.factory.create_proposal(**malicious)
        failed = {item["name"] for item in proposal["checks"] if not item["passed"]}
        self.assertEqual(proposal["status"], "rejected")
        self.assertTrue({"static_security", "permission_scope"}.issubset(failed))

    def test_duplicate_builtin_is_rejected_with_reuse_guidance(self) -> None:
        database = AvelinDatabase(Path(self.temporary.name) / "other.db")
        registry = ToolRegistry()
        registry.register(Tool("friendly_greeting", "Existing greeting", {"type": "object"}, lambda: "ok"))
        factory = ToolFactory(database, DEFAULT_USER_ID, registry, ToolPolicyEngine(database, DEFAULT_USER_ID))
        proposal = factory.create_proposal(**_tool_payload())
        duplicate = next(item for item in proposal["checks"] if item["name"] == "duplicate_search")
        self.assertEqual(proposal["status"], "rejected")
        self.assertIn("Reuse", duplicate["detail"])

    def test_version_update_disable_and_rollback(self) -> None:
        first = self.factory.create_proposal(**_tool_payload("1.0.0"))
        self._install(first)
        second_payload = _tool_payload("1.1.0")
        second_payload["source_code"] = 'def run(arguments):\n    return {"message": "Hi " + arguments["name"]}\n'
        second_payload["tests"] = [{"input": {"name": "Avelin"}, "expected": {"message": "Hi Avelin"}}]
        second = self.factory.create_proposal(**second_payload)
        self.assertEqual(second["status"], "ready")
        self.assertTrue(second["diff"])
        self._install(second)
        self.assertEqual(self.database.get_extension_proposal(DEFAULT_USER_ID, first["id"])["status"], "superseded")
        self.factory.disable("friendly_greeting")
        rollback = self.factory.rollback("friendly_greeting", "1.0.0")
        self.assertEqual(rollback.status, "waiting_approval")
        self.policy.decide(str(rollback.approval_id), "approved")
        self.assertEqual(self.factory.rollback("friendly_greeting", "1.0.0").status, "installed")


if __name__ == "__main__":
    unittest.main()
