from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
STATE_FILE = LOCAL / "remote-preview.json"
TARGET = "http://127.0.0.1:5173"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(slots=True)
class PreviewState:
    installed: bool
    online: bool = False
    active: bool = False
    url: str = ""
    message: str = ""

    def payload(self) -> dict[str, object]:
        return asdict(self)


def fetch_status(url: str, timeout: float = 4.0) -> int | None:
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status
    except error.HTTPError as exc:
        return exc.code
    except (error.URLError, OSError, TimeoutError):
        return None


def _contains_target(value: object, target: str = TARGET) -> bool:
    if isinstance(value, str):
        return target in value or "127.0.0.1:5173" in value
    if isinstance(value, dict):
        return any(_contains_target(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_target(item, target) for item in value)
    return False


def _has_any_serve_config(value: object) -> bool:
    if value in ({}, [], None, ""):
        return False
    if isinstance(value, dict):
        return any(_has_any_serve_config(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_any_serve_config(item) for item in value)
    return bool(value)


def find_tailscale() -> str:
    discovered = shutil.which("tailscale")
    if discovered:
        return discovered
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if not root:
            continue
        candidate = Path(root) / "Tailscale" / "tailscale.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


class TailscalePreview:
    def __init__(self, command: str | None = None) -> None:
        self.command = find_tailscale() if command is None else command
        self.started_here = False

    def _run(self, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
        if not self.command:
            raise RuntimeError("Tailscale не установлен. Установи его и войди в свой tailnet.")
        completed = subprocess.run(
            [self.command, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise RuntimeError(f"Tailscale command failed: {detail[:500]}")
        return completed

    def _json(self, *args: str) -> object:
        output = self._run(*args).stdout.strip() or "{}"
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Tailscale вернул неожиданный ответ. Обнови клиент и повтори проверку.") from exc

    def _identity(self) -> tuple[bool, str]:
        payload = self._json("status", "--json")
        if not isinstance(payload, dict):
            return False, ""
        self_state = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
        online = payload.get("BackendState") == "Running" and bool(self_state.get("Online", True))
        dns_name = str(self_state.get("DNSName") or "").rstrip(".")
        return online, dns_name

    def status(self) -> PreviewState:
        if not self.command:
            return PreviewState(installed=False, message="Tailscale не установлен")
        try:
            online, dns_name = self._identity()
            serve_payload = self._json("serve", "status", "--json") if online else {}
            active = online and _contains_target(serve_payload)
            url = f"https://{dns_name}" if active and dns_name else ""
            return PreviewState(
                installed=True,
                online=online,
                active=active,
                url=url,
                message="Private preview активен" if active else "Tailscale готов" if online else "Tailscale не подключён",
            )
        except RuntimeError as exc:
            return PreviewState(installed=True, message=str(exc))

    def start(self, timeout: float = 30.0) -> PreviewState:
        if not self.command:
            raise RuntimeError("Tailscale не установлен. Установи его и войди в свой tailnet.")
        online, dns_name = self._identity()
        if not online or not dns_name:
            raise RuntimeError("Tailscale не подключён или MagicDNS недоступен.")
        current = self._json("serve", "status", "--json")
        if _has_any_serve_config(current) and not _contains_target(current):
            raise RuntimeError("Tailscale Serve уже используется другой конфигурацией. Launcher не будет её перезаписывать.")
        if not _contains_target(current):
            serve_result = self._run("serve", "--bg", "--yes", "--https=443", TARGET, timeout=45)
            if "Serve is not enabled" in serve_result.stdout:
                enable_url = re.search(r"https://login\.tailscale\.com/\S+", serve_result.stdout)
                suffix = f" Открой: {enable_url.group(0)}" if enable_url else ""
                raise RuntimeError(f"Tailscale Serve ещё не разрешён для этого tailnet.{suffix}")
            self.started_here = True
        url = f"https://{dns_name}"
        deadline = time.monotonic() + timeout
        root_status = api_status = None
        while time.monotonic() < deadline:
            root_status = fetch_status(url, timeout=3)
            api_status = fetch_status(f"{url}/api/bootstrap", timeout=3)
            if root_status == 200 and api_status in {401, 403}:
                break
            time.sleep(.5)
        else:
            if self.started_here:
                self.stop()
            raise RuntimeError(f"Remote smoke check failed: root={root_status}, unauth_api={api_status}")
        LOCAL.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"mode": "tailscale-serve", "url": url, "target": TARGET}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return PreviewState(True, True, True, url, "Private preview активен до нажатия Stop")

    def smoke(self) -> dict[str, object]:
        state = self.status()
        root_status = fetch_status(state.url) if state.url else None
        api_status = fetch_status(f"{state.url}/api/bootstrap") if state.url else None
        return {
            **state.payload(),
            "root_status": root_status,
            "unauthorized_api_status": api_status,
            "secure": bool(state.url.startswith("https://")),
            "ok": bool(state.active and root_status == 200 and api_status in {401, 403}),
        }

    def stop(self) -> PreviewState:
        if not self.command:
            return PreviewState(False, message="Tailscale не установлен")
        current = self._json("serve", "status", "--json")
        if _contains_target(current):
            self._run("serve", "--yes", "--https=443", "off")
        self.started_here = False
        STATE_FILE.unlink(missing_ok=True)
        state = self.status()
        state.active = False
        state.url = ""
        state.message = "Private preview остановлен; локальный Avelin продолжает работать"
        return state
