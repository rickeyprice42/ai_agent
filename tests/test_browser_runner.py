from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings
from ai_agent.executor import ExecutionDecision, decide_step_action
from ai_agent.tasks import TaskStep
from ai_agent.tools.browser import BrowserFailure, PlaywrightBrowserRunner, browser_read_result


class BrowserFixtureHandler(BaseHTTPRequestHandler):
    version = 1

    def do_GET(self):
        if self.path == "/slow":
            sleep(2)
        marker = f"Version {self.version}"
        body = (
            "<!doctype html><html><head><title>Avelin Browser Fixture</title></head>"
            "<body><main id='content'>" + marker + "</main>"
            "<button id='go' onclick=\"document.querySelector('#content').textContent='Clicked safely'\">Continue</button>"
            "<div style='height:1800px'>Scrollable fixture</div>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *_):
        return


class BrowserPlanner:
    def __init__(self, url: str) -> None:
        self.url = url

    def decide(self, task, step, checkpoints) -> ExecutionDecision:
        return ExecutionDecision(
            "browser_interact",
            {"url": self.url, "actions": [{"type": "click", "selector": "#go"}]},
            "browser interaction test",
        )


def isolated_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="mock-local",
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class BrowserRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), BrowserFixtureHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        BrowserFixtureHandler.version = 1

    def test_dom_first_read_click_scroll_screenshot_and_dom_change(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            runner = PlaywrightBrowserRunner(
                workspace,
                timeout_seconds=5,
                allowed_hosts=("127.0.0.1",),
                allow_private_networks=True,
            )
            snapshot = runner.read(
                url=self.base_url,
                screenshot=True,
                screenshot_path="browser/initial.png",
            )
            self.assertEqual(snapshot["profile"], "ephemeral")
            self.assertIn("Version 1", snapshot["main_text"])
            self.assertTrue(any(item["role"] == "button" for item in snapshot["elements"]))
            self.assertTrue((workspace / snapshot["screenshot"]["path"]).is_file())
            self.assertEqual(len(snapshot["screenshot"]["sha256"]), 64)

            interacted = runner.interact(
                url=self.base_url,
                expected_dom_hash=snapshot["dom_hash"],
                actions=[
                    {"type": "click", "role": "button", "name": "Continue"},
                    {"type": "scroll", "delta_y": 700},
                    {"type": "snapshot"},
                ],
                screenshot_path="browser/final.png",
            )
            self.assertIn("Clicked safely", interacted["main_text"])
            self.assertEqual([item["status"] for item in interacted["actions"]], ["completed"] * 3)

            BrowserFixtureHandler.version = 2
            result = browser_read_result(runner, {"url": self.base_url, "screenshot": False})
            self.assertTrue(result.ok)
            self.assertNotEqual(result.output["dom_hash"], snapshot["dom_hash"])
            with self.assertRaisesRegex(BrowserFailure, "DOM изменился"):
                runner.interact(
                    url=self.base_url,
                    expected_dom_hash=snapshot["dom_hash"],
                    actions=[{"type": "click", "selector": "#go"}],
                )

    def test_timeout_ssrf_allowlist_and_denied_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_blocked = PlaywrightBrowserRunner(
                root / "blocked", timeout_seconds=1, allowed_hosts=("127.0.0.1",)
            )
            blocked = browser_read_result(private_blocked, {"url": self.base_url})
            self.assertFalse(blocked.ok)
            self.assertEqual(blocked.error.code, "private_network_blocked")

            wrong_host = PlaywrightBrowserRunner(
                root / "allowlist",
                timeout_seconds=1,
                allowed_hosts=("example.com",),
                allow_private_networks=True,
            )
            denied_host = browser_read_result(wrong_host, {"url": self.base_url})
            self.assertEqual(denied_host.error.code, "host_not_allowed")

            local = PlaywrightBrowserRunner(
                root / "timeout",
                timeout_seconds=1,
                allowed_hosts=("127.0.0.1",),
                allow_private_networks=True,
            )
            timed_out = browser_read_result(local, {"url": self.base_url + "/slow"})
            self.assertFalse(timed_out.ok)
            self.assertEqual(timed_out.error.code, "timeout")

            settings = isolated_settings(root / "agent")
            agent = Agent(settings, user_id="alice")
            agent.browser = local
            browser_tool = agent.tools.get("browser_interact")
            task = agent.tasks.create_task("browser approval", steps=["click fixture"])
            step = task.steps[0]
            arguments = {"url": self.base_url, "actions": [{"type": "click", "selector": "#go"}]}
            decision = agent.policy.authorize(
                browser_tool, arguments, task_id=task.id, step_id=step.id
            )
            self.assertEqual(decision.outcome, "approval_required")
            denied = agent.policy.decide(decision.approval_id, "denied")
            self.assertEqual(denied["status"], "denied")
            self.assertFalse((settings.tool_workspace_dir / "browser").exists())

    def test_browser_manifest_registry_and_direct_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = isolated_settings(Path(directory))
            agent = Agent(settings, user_id="alice")
            read_tool = agent.tools.get("browser_read")
            interact_tool = agent.tools.get("browser_interact")
            self.assertEqual(read_tool.version, "1.0.0")
            self.assertEqual(read_tool.permissions[0].risk, "read")
            self.assertEqual(interact_tool.permissions[0].risk, "external")

            local = PlaywrightBrowserRunner(
                settings.tool_workspace_dir,
                timeout_seconds=5,
                allowed_hosts=("127.0.0.1",),
                allow_private_networks=True,
            )
            read_tool.handler = lambda args: browser_read_result(local, args)
            invocation = agent.tools.invoke(
                "browser_read", {"url": self.base_url, "screenshot": False}
            )
            self.assertEqual(invocation.status, "completed")
            self.assertEqual(invocation.output["profile"], "ephemeral")

            decision = decide_step_action(
                TaskStep(
                    id="browser-plan",
                    task_id="task",
                    description="Открой в браузере https://example.com и покажи страницу",
                )
            )
            self.assertEqual(decision.tool_name, "browser_read")
            self.assertEqual(decision.arguments["url"], "https://example.com")
            self.assertTrue(decision.arguments["screenshot"])


if __name__ == "__main__":
    unittest.main()
