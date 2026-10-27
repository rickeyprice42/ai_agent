from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from urllib import parse
from uuid import uuid4
import ipaddress
import json
import socket

from ai_agent.tools.base import ToolResult, failure_result


EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
SAFE_SCHEMES = {"http", "https"}
INTERNAL_SCHEMES = {"about", "blob", "data"}


@dataclass(frozen=True, slots=True)
class BrowserFailure(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class PlaywrightBrowserRunner:
    """Ephemeral, DOM-first browser runner backed by the installed Edge channel."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        timeout_seconds: int = 20,
        allowed_hosts: tuple[str, ...] = (),
        allow_private_networks: bool = False,
        max_text_chars: int = 20000,
        max_elements: int = 100,
    ) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ms = max(1, min(int(timeout_seconds), 120)) * 1000
        self.allowed_hosts = tuple(host.strip().casefold() for host in allowed_hosts if host.strip())
        self.allow_private_networks = allow_private_networks
        self.max_text_chars = max(1000, min(int(max_text_chars), 100000))
        self.max_elements = max(10, min(int(max_elements), 500))

    def availability(self) -> tuple[bool, str | None]:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "Python package playwright не установлен."
        if not any(path.is_file() for path in EDGE_PATHS):
            return False, "Microsoft Edge не найден; browser runner не может стартовать."
        return True, None

    def read(
        self,
        *,
        url: str,
        screenshot: bool = True,
        screenshot_path: str | None = None,
    ) -> dict:
        return self._run(url=url, actions=[], screenshot=screenshot, screenshot_path=screenshot_path)

    def interact(
        self,
        *,
        url: str,
        actions: list[dict],
        expected_dom_hash: str | None = None,
        screenshot: bool = True,
        screenshot_path: str | None = None,
    ) -> dict:
        if not actions:
            raise BrowserFailure("invalid_actions", "Нужно передать хотя бы одно browser-действие.")
        if len(actions) > 12:
            raise BrowserFailure("too_many_actions", "За один browser-вызов разрешено не более 12 действий.")
        return self._run(
            url=url,
            actions=actions,
            expected_dom_hash=expected_dom_hash,
            screenshot=screenshot,
            screenshot_path=screenshot_path,
        )

    def _run(
        self,
        *,
        url: str,
        actions: list[dict],
        expected_dom_hash: str | None = None,
        screenshot: bool,
        screenshot_path: str | None,
    ) -> dict:
        available, reason = self.availability()
        if not available:
            raise BrowserFailure("browser_unavailable", reason or "Browser runner недоступен.")
        initial = self._validate_url(url)
        initial_host = (initial.hostname or "").casefold()
        if self.allowed_hosts and not _host_allowed(initial_host, self.allowed_hosts):
            raise BrowserFailure(
                "host_not_allowed",
                f"Хост {initial_host} отсутствует в BROWSER_ALLOWED_HOSTS.",
                details={"host": initial_host},
            )
        operation_hosts = self.allowed_hosts or (initial_host,)
        artifact_target = self._screenshot_path(screenshot_path) if screenshot else None
        blocked_urls: list[str] = []
        validated_hosts: set[str] = set()
        performed: list[dict] = []

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="msedge",
                    headless=True,
                    args=["--disable-background-networking", "--disable-sync"],
                )
                try:
                    context = browser.new_context(
                        accept_downloads=False,
                        service_workers="block",
                        viewport={"width": 1365, "height": 768},
                        locale="ru-RU",
                    )
                    context.set_default_timeout(self.timeout_ms)
                    context.set_default_navigation_timeout(self.timeout_ms)

                    def route_request(route) -> None:
                        request_url = route.request.url
                        parsed = parse.urlparse(request_url)
                        if parsed.scheme in INTERNAL_SCHEMES:
                            route.continue_()
                            return
                        try:
                            checked = self._validate_url(request_url, validated_hosts)
                            request_host = (checked.hostname or "").casefold()
                            if not _host_allowed(request_host, operation_hosts):
                                raise BrowserFailure(
                                    "host_not_allowed",
                                    f"Browser request к {request_host} не входит в allowlist запуска.",
                                )
                        except BrowserFailure:
                            blocked_urls.append(request_url)
                            route.abort("blockedbyclient")
                            return
                        route.continue_()

                    context.route("**/*", route_request)
                    page = context.new_page()
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    page.on("download", lambda download: download.cancel())
                    response = page.goto(initial.geturl(), wait_until="domcontentloaded")
                    if blocked_urls and page.url in blocked_urls:
                        raise BrowserFailure(
                            "blocked_navigation",
                            "Навигация или redirect заблокированы browser allowlist/SSRF policy.",
                            details={"blocked_urls": blocked_urls[:10]},
                        )
                    initial_snapshot = self._snapshot(page)
                    if expected_dom_hash and initial_snapshot["dom_hash"] != expected_dom_hash:
                        raise BrowserFailure(
                            "dom_changed",
                            "DOM изменился после планирования; действие не выполнено.",
                            details={
                                "expected_dom_hash": expected_dom_hash,
                                "actual_dom_hash": initial_snapshot["dom_hash"],
                            },
                        )
                    for index, action in enumerate(actions):
                        performed.append(self._perform(page, action, index))
                        for extra_page in context.pages[1:]:
                            extra_page.close()
                    final_snapshot = self._snapshot(page)
                    artifact = None
                    if artifact_target is not None:
                        page.screenshot(path=str(artifact_target), full_page=True)
                        artifact = self._artifact(artifact_target)
                    return {
                        "url": page.url,
                        "title": page.title()[:500],
                        "status": response.status if response is not None else None,
                        "dom_hash": final_snapshot["dom_hash"],
                        "main_text": final_snapshot["main_text"],
                        "elements": final_snapshot["elements"],
                        "actions": performed,
                        "screenshot": artifact,
                        "blocked_requests": blocked_urls[:10],
                        "profile": "ephemeral",
                    }
                finally:
                    browser.close()
        except BrowserFailure:
            raise
        except PlaywrightTimeoutError as exc:
            raise BrowserFailure("timeout", "Browser action превысил таймаут.", True) from exc
        except Exception as exc:
            raise BrowserFailure(
                "browser_error",
                f"Browser runner завершился ошибкой: {type(exc).__name__}.",
            ) from exc

    def _perform(self, page, action: dict, index: int) -> dict:
        kind = str(action.get("type", "")).strip().lower()
        if kind == "scroll":
            delta_y = max(-5000, min(int(action.get("delta_y", 600)), 5000))
            page.mouse.wheel(0, delta_y)
            page.wait_for_timeout(50)
            return {"index": index, "type": kind, "delta_y": delta_y, "status": "completed"}
        if kind == "snapshot":
            return {"index": index, "type": kind, "status": "completed"}
        if kind != "click":
            raise BrowserFailure("unsupported_action", f"Browser action не поддерживается: {kind}.")
        locator = _locator_for_action(page, action)
        count = locator.count()
        if count == 0:
            raise BrowserFailure("target_not_found", "Элемент для клика не найден; DOM мог измениться.")
        if count > 1:
            raise BrowserFailure("ambiguous_target", "Найдено несколько элементов; уточните selector/role/name.")
        locator.click(timeout=self.timeout_ms)
        page.wait_for_timeout(100)
        return {"index": index, "type": kind, "status": "completed"}

    def _snapshot(self, page) -> dict:
        raw = page.evaluate(
            r"""({maxElements, maxText}) => {
              const candidates = Array.from(document.querySelectorAll(
                'a,button,input,textarea,select,summary,[role],[tabindex]'
              ));
              const visible = candidates.filter((node) => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
              }).slice(0, maxElements);
              const elements = visible.map((node, index) => {
                const ref = `e${index + 1}`;
                node.setAttribute('data-avelin-ref', ref);
                const tag = node.tagName.toLowerCase();
                const role = node.getAttribute('role') || ({a:'link',button:'button',input:'textbox',textarea:'textbox',select:'combobox',summary:'button'}[tag] || tag);
                const name = (node.getAttribute('aria-label') || node.innerText || node.value || node.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ').slice(0, 300);
                return {ref, role, name, tag, disabled: Boolean(node.disabled)};
              });
              const main = document.querySelector('main,article,[role="main"]') || document.body;
              const mainText = ((main && main.innerText) || '').trim().replace(/\n{3,}/g, '\n\n').slice(0, maxText);
              return {mainText, elements};
            }""",
            {"maxElements": self.max_elements, "maxText": self.max_text_chars},
        )
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "main_text": str(raw.get("mainText", "")),
            "elements": raw.get("elements", []),
            "dom_hash": sha256(canonical.encode("utf-8")).hexdigest(),
        }

    def _validate_url(self, value: str, validated_hosts: set[str] | None = None):
        parsed = parse.urlparse(value.strip())
        if parsed.scheme not in SAFE_SCHEMES or not parsed.hostname:
            raise BrowserFailure("invalid_url", "Browser URL должен быть публичным http/https URL.")
        if parsed.username or parsed.password:
            raise BrowserFailure("credentials_forbidden", "Credentials в browser URL запрещены.")
        host = parsed.hostname.casefold()
        if validated_hosts is not None and host in validated_hosts:
            return parsed
        try:
            default_port = 443 if parsed.scheme == "https" else 80
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or default_port)}
        except socket.gaierror as exc:
            raise BrowserFailure("dns_failed", "Не удалось разрешить browser host.", True) from exc
        if not self.allow_private_networks:
            for address in addresses:
                ip = ipaddress.ip_address(address)
                if not ip.is_global:
                    raise BrowserFailure("private_network_blocked", "Browser доступ к private/local network запрещён.")
        if validated_hosts is not None:
            validated_hosts.add(host)
        return parsed

    def _screenshot_path(self, relative_path: str | None) -> Path:
        clean = (relative_path or f"browser/screenshots/{uuid4().hex}.png").strip().replace("\\", "/")
        if not clean.lower().endswith(".png"):
            raise BrowserFailure("invalid_screenshot_path", "Screenshot должен иметь расширение .png.")
        target = (self.workspace_dir / clean).resolve()
        if target == self.workspace_dir or self.workspace_dir not in target.parents:
            raise BrowserFailure("path_outside_workspace", "Screenshot path выходит за пределы workspace.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _artifact(self, target: Path) -> dict:
        return {
            "path": target.relative_to(self.workspace_dir).as_posix(),
            "name": target.name,
            "sha256": sha256(target.read_bytes()).hexdigest(),
            "size_bytes": target.stat().st_size,
        }


def browser_read_result(runner: PlaywrightBrowserRunner, arguments: dict) -> ToolResult:
    return _browser_result(
        lambda: runner.read(
            url=str(arguments.get("url", "")),
            screenshot=bool(arguments.get("screenshot", True)),
            screenshot_path=str(arguments.get("screenshot_path", "")).strip() or None,
        )
    )


def browser_interact_result(runner: PlaywrightBrowserRunner, arguments: dict) -> ToolResult:
    actions = arguments.get("actions", [])
    return _browser_result(
        lambda: runner.interact(
            url=str(arguments.get("url", "")),
            actions=[item for item in actions if isinstance(item, dict)] if isinstance(actions, list) else [],
            expected_dom_hash=str(arguments.get("expected_dom_hash", "")).strip() or None,
            screenshot=bool(arguments.get("screenshot", True)),
            screenshot_path=str(arguments.get("screenshot_path", "")).strip() or None,
        )
    )


def _browser_result(operation: Callable[[], dict]) -> ToolResult:
    try:
        output = operation()
    except BrowserFailure as exc:
        return failure_result(
            exc.code,
            exc.message,
            retryable=exc.retryable,
            details=exc.details or {},
        )
    return ToolResult(status="completed", output=output)


def _host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    normalized = host.casefold().rstrip(".")
    return any(
        normalized == allowed.rstrip(".") or normalized.endswith("." + allowed.rstrip("."))
        for allowed in allowlist
    )


def _locator_for_action(page, action: dict):
    ref = str(action.get("ref", "")).strip()
    selector = str(action.get("selector", "")).strip()
    role = str(action.get("role", "")).strip()
    name = str(action.get("name", "")).strip()
    if ref:
        return page.locator(f'[data-avelin-ref="{ref}"]')
    if selector:
        return page.locator(selector)
    if role and name:
        return page.get_by_role(role, name=name, exact=True)
    raise BrowserFailure("invalid_target", "Для click укажите ref, selector или точные role и name.")
