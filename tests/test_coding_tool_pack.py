from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
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
from ai_agent.tools.code_tools import BoundedProcessRunner
from ai_agent.tools.files import FileSandbox


def isolated_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="mock-local",
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class CodingToolPackTests(unittest.TestCase):
    def test_unicode_line_read_search_metadata_exact_edit_move_and_recoverable_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = FileSandbox(root, max_read_chars=400, max_write_chars=5000)
            source = root / "данные" / "пример.txt"
            source.parent.mkdir(parents=True)
            source.write_text("первая строка\nИркутск и Avelin\nтретья строка\n", encoding="utf-8")

            metadata = files.metadata("данные\\пример.txt")
            self.assertRegex(metadata["sha256"], r"^[0-9a-f]{64}$")
            lines = files.read_lines("данные/пример.txt", 2, 3)
            self.assertIn("2: Иркутск и Avelin", lines["content"])
            self.assertEqual(lines["sha256"], metadata["sha256"])
            found = files.search("иркутск", "данные")
            self.assertEqual(found["matches"][0]["line"], 2)

            edited = files.exact_edit(
                "данные/пример.txt",
                metadata["sha256"],
                "Иркутск и Avelin",
                "Иркутск любит Avelin",
            )
            self.assertNotEqual(edited["before_sha256"], edited["after_sha256"])
            with self.assertRaisesRegex(ValueError, "stale_file"):
                files.exact_edit("данные/пример.txt", metadata["sha256"], "третья", "3")
            self.assertIn("Иркутск любит Avelin", source.read_text(encoding="utf-8"))

            moved = files.move_file(
                "данные/пример.txt", "готово/пример.txt", edited["after_sha256"]
            )
            self.assertEqual(moved["destination"], "готово/пример.txt")
            trashed = files.trash_file("готово/пример.txt", edited["after_sha256"])
            self.assertTrue(trashed["recoverable"])
            self.assertTrue((root / trashed["recovery_path"]).is_file())
            restored = files.restore_file(
                trashed["recovery_path"], "восстановлено/пример.txt", edited["after_sha256"]
            )
            self.assertTrue(restored["restored"])
            self.assertTrue((root / "восстановлено" / "пример.txt").is_file())

    def test_large_file_is_bounded_and_stale_precondition_preserves_user_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = FileSandbox(root, max_read_chars=120, max_write_chars=10000)
            target = root / "large.txt"
            target.write_text("начало\n" + ("x" * 5000), encoding="utf-8")
            read = files.read_lines("large.txt", 1, 500)
            self.assertTrue(read["truncated"])
            original_hash = read["sha256"]
            target.write_text("изменено пользователем", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale_file"):
                files.exact_edit("large.txt", original_hash, "начало", "start")
            self.assertEqual(target.read_text(encoding="utf-8"), "изменено пользователем")

    def test_process_timeout_preserves_partial_output_and_bounds_normal_output(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = BoundedProcessRunner(Path(directory), max_output_chars=1000)
            timeout = subprocess.TimeoutExpired(["demo"], 1, output="partial stdout", stderr="partial stderr")
            with patch("ai_agent.tools.code_tools.subprocess.run", side_effect=timeout):
                result = runner.run(["demo"], timeout_seconds=1)
            self.assertTrue(result["timed_out"])
            self.assertIn("partial stdout", result["stdout"])
            completed = subprocess.CompletedProcess(["demo"], 0, stdout="x" * 1200, stderr="")
            with patch("ai_agent.tools.code_tools.subprocess.run", return_value=completed):
                bounded = runner.run(["demo"])
            self.assertTrue(bounded["truncated"])
            self.assertLessEqual(len(bounded["stdout"]), 1000)

    @unittest.skipUnless(shutil.which("git"), "Git executable is required")
    def test_end_to_end_inspect_edit_test_diff_and_git_write_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = isolated_settings(root)
            workspace = settings.tool_workspace_dir
            (workspace / "tests").mkdir(parents=True)
            (workspace / "sample.py").write_text(
                "def value():\n    return 1\n", encoding="utf-8"
            )
            (workspace / "tests" / "test_sample.py").write_text(
                "import unittest\nfrom sample import value\n\n"
                "class SampleTest(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(value(), 2)\n",
                encoding="utf-8",
            )
            self._git(workspace, "init")
            self._git(workspace, "config", "user.email", "tests@example.com")
            self._git(workspace, "config", "user.name", "Avelin Tests")
            self._git(workspace, "add", ".")
            self._git(workspace, "commit", "-m", "initial")

            agent = Agent(settings, user_id="alice")
            expected_tools = {
                "read_file_lines", "file_metadata", "search_workspace", "edit_file_exact",
                "move_file", "delete_file", "restore_file", "inspect_project", "run_code_check", "process_status",
                "git_status", "git_diff", "git_log", "git_branches", "git_inspect_ref",
                "git_stage", "git_commit", "git_create_branch",
            }
            self.assertTrue(expected_tools.issubset({item["name"] for item in agent.tools.list_manifests()}))
            self.assertFalse(agent.tools.has("git_push"))
            self.assertEqual(
                agent.policy.authorize(
                    agent.tools.get("delete_file"),
                    {"path": "sample.py", "expected_sha256": "0" * 64},
                ).outcome,
                "approval_required",
            )
            self.assertEqual(
                agent.policy.authorize(
                    agent.tools.get("move_file"),
                    {
                        "source": "sample.py", "destination": "other.py",
                        "expected_sha256": "0" * 64, "overwrite": True,
                    },
                ).outcome,
                "approval_required",
            )

            inspection = agent.tools.invoke("inspect_project", {})
            self.assertTrue(inspection.ok)
            self.assertEqual(inspection.output["languages"]["Python"], 2)
            metadata = agent.tools.invoke("file_metadata", {"path": "sample.py"}).output
            edit_args = {
                "path": "sample.py",
                "expected_sha256": metadata["sha256"],
                "old_text": "return 1",
                "new_text": "return 2",
            }
            self.assertEqual(agent.policy.authorize(agent.tools.get("edit_file_exact"), edit_args).outcome, "allow")
            self.assertTrue(agent.tools.invoke("edit_file_exact", edit_args).ok)

            check_args = {"runner": "python_unittest", "timeout_seconds": 30}
            approval = agent.policy.authorize(agent.tools.get("run_code_check"), check_args)
            self.assertEqual(approval.outcome, "approval_required")
            agent.policy.decide(approval.approval_id, "approved")
            self.assertEqual(agent.policy.authorize(agent.tools.get("run_code_check"), check_args).outcome, "allow")
            check = agent.tools.invoke("run_code_check", check_args)
            self.assertTrue(check.ok, check.text())
            self.assertIn("OK", check.output["stderr"])
            process_status = agent.tools.invoke("process_status", {})
            self.assertTrue(process_status.ok, process_status.text())
            self.assertEqual(process_status.output["recent"][0]["return_code"], 0)

            diff = agent.tools.invoke("git_diff", {})
            self.assertTrue(diff.ok)
            self.assertIn("return 2", diff.output["stdout"])
            stage_args = {"paths": ["sample.py"]}
            self.assertEqual(
                agent.policy.authorize(agent.tools.get("git_stage"), stage_args).outcome,
                "approval_required",
            )
            blocked = agent.tools.invoke("git_stage", {"paths": ["../outside.txt"]})
            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.error.code, "invalid_operation")

    @staticmethod
    def _git(workspace: Path, *arguments: str) -> None:
        completed = subprocess.run(
            ["git", *arguments], cwd=workspace, capture_output=True, text=True, encoding="utf-8"
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


if __name__ == "__main__":
    unittest.main()
