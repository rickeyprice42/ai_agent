from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import launch_local


class LauncherRecoveryTests(unittest.TestCase):
    def test_launcher_recovers_missing_frontend_when_backend_is_already_ready(self) -> None:
        process = Mock()
        process.poll.return_value = None

        with TemporaryDirectory() as temp_dir:
            with patch.object(launch_local, "LOGS", Path(temp_dir)), patch.object(
                launch_local.AvelinLauncher, "validate"
            ), patch.object(
                launch_local, "fetch", side_effect=[(200, '{"status":"ok"}'), None]
            ), patch.object(launch_local, "wait_for", return_value=True), patch.object(
                launch_local.subprocess, "Popen", return_value=process
            ) as popen:
                launcher = launch_local.AvelinLauncher()
                message = launcher.start()
                launcher.stop()

        self.assertIn("frontend", message)
        self.assertEqual(popen.call_count, 1)
        command = popen.call_args.args[0]
        self.assertIn("vite.js", " ".join(command))
        process.terminate.assert_called_once()

    def test_launcher_reports_foreign_backend_port_without_starting_processes(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(launch_local, "LOGS", Path(temp_dir)), patch.object(
            launch_local.AvelinLauncher, "validate"
        ), patch.object(launch_local, "fetch", side_effect=[(200, "not avelin"), None]), patch.object(
            launch_local.subprocess, "Popen"
        ) as popen:
            launcher = launch_local.AvelinLauncher()
            with self.assertRaisesRegex(RuntimeError, "8000"):
                launcher.start()

        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
