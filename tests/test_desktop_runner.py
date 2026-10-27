from pathlib import Path
from subprocess import CREATE_NO_WINDOW, Popen
from time import sleep
import os
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PIL import Image

from ai_agent.tools.desktop import DesktopFailure, DesktopRunner


class FakeElementInfo:
    name = "Continue"
    control_type = "Button"
    automation_id = "continue_button"


class FakeControl:
    element_info = FakeElementInfo()

    def is_enabled(self):
        return True


class FakeTarget:
    def __init__(self, clicked: list[bool]):
        self.clicked = clicked

    def wait(self, *_args, **_kwargs):
        return self

    def wrapper_object(self):
        return self

    def click_input(self):
        self.clicked.append(True)


class FakeWindow:
    def __init__(self):
        self.clicked: list[bool] = []
        self.color = "white"

    def capture_as_image(self):
        return Image.new("RGB", (320, 180), self.color)

    def descendants(self):
        return [FakeControl()]

    def child_window(self, **_criteria):
        return FakeTarget(self.clicked)


class DesktopRunnerTests(unittest.TestCase):
    def test_allowlist_snapshot_click_stale_guard_and_emergency_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            stopped = [False]
            runner = DesktopRunner(
                Path(directory),
                allowed_window_titles=("Avelin Test GUI",),
                emergency_stop=lambda: stopped[0],
            )
            window = FakeWindow()
            runner._window = lambda _title: window
            snapshot = runner.snapshot(
                window_title="Avelin Test GUI", screenshot_path="desktop/before.png"
            )
            self.assertTrue((Path(directory) / "desktop/before.png").is_file())
            self.assertEqual(snapshot["controls"][0]["automation_id"], "continue_button")

            result = runner.interact(
                window_title="Avelin Test GUI",
                expected_screen_hash=snapshot["screen_hash"],
                actions=[{"type": "click", "automation_id": "continue_button"}],
                screenshot_path="desktop/after.png",
            )
            self.assertTrue(window.clicked)
            self.assertEqual(result["actions"][0]["backend"], "uia")

            window.color = "black"
            with self.assertRaisesRegex(DesktopFailure, "изменилось"):
                runner.interact(
                    window_title="Avelin Test GUI",
                    expected_screen_hash=snapshot["screen_hash"],
                    actions=[{"type": "click", "automation_id": "continue_button"}],
                )

            stopped[0] = True
            with self.assertRaisesRegex(DesktopFailure, "Emergency stop"):
                runner.snapshot(window_title="Avelin Test GUI")

    def test_private_and_unlisted_windows_are_refused(self):
        runner = DesktopRunner(
            Path(tempfile.gettempdir()) / "avelin-desktop-test",
            allowed_window_titles=("Avelin Test GUI", "Bank login"),
            emergency_stop=lambda: False,
        )
        with self.assertRaisesRegex(DesktopFailure, "allowlist"):
            runner.snapshot(window_title="Personal browser")
        with self.assertRaisesRegex(DesktopFailure, "чувствительному"):
            runner.snapshot(window_title="Bank login")

    @unittest.skipUnless(os.name == "nt", "Windows UIA integration test")
    def test_real_tkinter_window_is_controlled_and_verified_by_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = DesktopRunner(
                Path(directory),
                allowed_window_titles=("Avelin Test GUI",),
                emergency_stop=lambda: False,
                timeout_seconds=10,
            )
            available, reason = runner.availability()
            if not available:
                self.skipTest(reason or "desktop runner unavailable")
            process = Popen(
                [sys.executable, str(ROOT / "tests" / "fixtures" / "desktop_fixture.py")],
                creationflags=CREATE_NO_WINDOW,
            )
            try:
                sleep(1)
                before = runner.snapshot(
                    window_title="Avelin Test GUI", screenshot_path="desktop/real-before.png"
                )
                button_ref = next(
                    item["ref"]
                    for item in before["controls"]
                    if item["control_type"] == "Button"
                )
                result = runner.interact(
                    window_title="Avelin Test GUI",
                    expected_screen_hash=before["screen_hash"],
                    actions=[{"type": "click", "ref": button_ref}],
                    screenshot_path="desktop/real-after.png",
                )
                self.assertTrue((Path(directory) / "desktop/real-after.png").is_file())
                self.assertNotEqual(before["screen_hash"], result["screen_hash"])
            finally:
                try:
                    runner._window("Avelin Test GUI").close()
                except Exception:
                    pass
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
