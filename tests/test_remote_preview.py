from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts import remote_preview


def completed(payload: object = None) -> subprocess.CompletedProcess[str]:
    stdout = json.dumps(payload if payload is not None else {})
    return subprocess.CompletedProcess(["tailscale"], 0, stdout=stdout, stderr="")


class RemotePreviewTests(unittest.TestCase):
    def test_missing_tailscale_keeps_remote_mode_unavailable(self) -> None:
        preview = remote_preview.TailscalePreview(command="")
        state = preview.status()
        self.assertFalse(state.installed)
        self.assertFalse(state.active)

    def test_status_detects_only_the_avelin_target(self) -> None:
        preview = remote_preview.TailscalePreview(command="tailscale.exe")
        with patch.object(
            preview,
            "_run",
            side_effect=[
                completed({"BackendState": "Running", "Self": {"Online": True, "DNSName": "avelin.tail.ts.net."}}),
                completed({"Web": {"avelin.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5173"}}}}}),
            ],
        ):
            state = preview.status()
        self.assertTrue(state.active)
        self.assertEqual(state.url, "https://avelin.tail.ts.net")

    def test_start_refuses_to_overwrite_unrelated_serve_configuration(self) -> None:
        preview = remote_preview.TailscalePreview(command="tailscale.exe")
        with patch.object(
            preview,
            "_run",
            side_effect=[
                completed({"BackendState": "Running", "Self": {"Online": True, "DNSName": "avelin.tail.ts.net."}}),
                completed({"Web": {"other.tail.ts.net:443": {"Handlers": {"/docs": {"Proxy": "http://127.0.0.1:9000"}}}}}),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "не будет её перезаписывать"):
                preview.start(timeout=0)

    def test_start_surfaces_the_one_time_serve_enable_url(self) -> None:
        preview = remote_preview.TailscalePreview(command="tailscale.exe")
        prompt = subprocess.CompletedProcess(
            ["tailscale"],
            0,
            stdout="Serve is not enabled on your tailnet.\nhttps://login.tailscale.com/f/serve?node=test-node\n",
            stderr="",
        )
        with patch.object(
            preview,
            "_run",
            side_effect=[
                completed({"BackendState": "Running", "Self": {"Online": True, "DNSName": "avelin.tail.ts.net."}}),
                completed({}),
                prompt,
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "login.tailscale.com/f/serve"):
                preview.start(timeout=0)

    def test_start_and_stop_use_scoped_serve_commands_and_require_api_auth(self) -> None:
        preview = remote_preview.TailscalePreview(command="tailscale.exe")
        calls: list[tuple[str, ...]] = []

        def fake_run(*args: str, **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args == ("status", "--json"):
                return completed({"BackendState": "Running", "Self": {"Online": True, "DNSName": "avelin.tail.ts.net."}})
            if args == ("serve", "status", "--json"):
                active = any(item[:2] == ("serve", "--bg") for item in calls)
                return completed({"Proxy": remote_preview.TARGET} if active else {})
            return completed()

        with TemporaryDirectory() as temp_dir, patch.object(remote_preview, "STATE_FILE", Path(temp_dir) / "state.json"), patch.object(
            preview, "_run", side_effect=fake_run
        ), patch.object(remote_preview, "fetch_status", side_effect=[200, 401]):
            state = preview.start(timeout=1)
            self.assertTrue(state.active)
            stopped = preview.stop()

        self.assertIn(("serve", "--bg", "--yes", "--https=443", remote_preview.TARGET), calls)
        self.assertIn(("serve", "--yes", "--https=443", "off"), calls)
        self.assertFalse(stopped.active)


if __name__ == "__main__":
    unittest.main()
