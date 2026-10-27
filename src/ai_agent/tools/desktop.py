from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import re

from ai_agent.tools.base import ToolResult, failure_result


SENSITIVE_TITLE_PATTERN = re.compile(
    r"(?i)(password|passcode|credential|sign[ -]?in|login|bank|wallet|парол|вход|банк)"
)


@dataclass(frozen=True, slots=True)
class DesktopFailure(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class DesktopRunner:
    """Approval-gated Windows UIA runner with an optional PyAutoGUI fallback."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        allowed_window_titles: tuple[str, ...],
        emergency_stop: Callable[[], bool],
        timeout_seconds: int = 15,
        allow_pyautogui: bool = False,
    ) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.allowed_window_titles = tuple(title.strip() for title in allowed_window_titles if title.strip())
        self.emergency_stop = emergency_stop
        self.timeout_seconds = max(1, min(int(timeout_seconds), 60))
        self.allow_pyautogui = allow_pyautogui

    def availability(self) -> tuple[bool, str | None]:
        try:
            import pywinauto  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return False, "Для desktop runner нужны pywinauto и Pillow."
        if self.allow_pyautogui:
            try:
                import pyautogui  # noqa: F401
            except ImportError:
                return False, "DESKTOP_ALLOW_PYAUTOGUI требует пакет pyautogui."
        if not self.allowed_window_titles:
            return False, "DESKTOP_ALLOWED_WINDOW_TITLES пуст; доступ к окнам запрещён."
        return True, None

    def snapshot(self, *, window_title: str, screenshot_path: str | None = None) -> dict:
        self._guard(window_title)
        window = self._window(window_title)
        image = window.capture_as_image()
        target = self._screenshot_path(screenshot_path)
        image.save(target, format="PNG")
        controls = self._controls(window)
        return {
            "window_title": window_title,
            "backend": "uia",
            "screen_hash": _image_hash(image),
            "controls": controls,
            "screenshot": self._artifact(target),
        }

    def interact(
        self,
        *,
        window_title: str,
        actions: list[dict],
        expected_screen_hash: str | None = None,
        screenshot_path: str | None = None,
    ) -> dict:
        if not actions or len(actions) > 8:
            raise DesktopFailure("invalid_actions", "Разрешено от 1 до 8 desktop-действий.")
        self._guard(window_title)
        window = self._window(window_title)
        before = window.capture_as_image()
        actual_hash = _image_hash(before)
        if expected_screen_hash and expected_screen_hash != actual_hash:
            raise DesktopFailure(
                "screen_changed", "Окно изменилось после планирования; действие не выполнено."
            )
        performed: list[dict] = []
        for index, action in enumerate(actions):
            self._guard(window_title)
            kind = str(action.get("type", "")).strip().lower()
            if kind != "click":
                raise DesktopFailure("unsupported_action", f"Desktop action не поддерживается: {kind}.")
            performed.append(self._click(window, action, index))
        return {
            **self.snapshot(window_title=window_title, screenshot_path=screenshot_path),
            "actions": performed,
        }

    def _window(self, title: str):
        from pywinauto import Desktop

        try:
            window = Desktop(backend="uia").window(title=title)
            window.wait("visible", timeout=self.timeout_seconds)
            return window
        except Exception as exc:
            raise DesktopFailure("window_not_found", "Разрешённое окно не найдено.") from exc

    def _controls(self, window) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        try:
            wrappers = window.descendants()
        except Exception:
            wrappers = []
        for index, control in enumerate(wrappers[:100]):
            info = control.element_info
            result.append(
                {
                    "ref": f"u{index + 1}",
                    "title": str(info.name or "")[:300],
                    "control_type": str(info.control_type or "")[:80],
                    "automation_id": str(info.automation_id or "")[:120],
                    "enabled": bool(control.is_enabled()),
                }
            )
        return result

    def _click(self, window, action: dict, index: int) -> dict:
        ref = str(action.get("ref", "")).strip()
        automation_id = str(action.get("automation_id", "")).strip()
        title = str(action.get("title", "")).strip()
        control_type = str(action.get("control_type", "")).strip()
        try:
            if ref:
                match = re.fullmatch(r"u([1-9][0-9]*)", ref)
                descendants = window.descendants()
                if not match or int(match.group(1)) > len(descendants):
                    raise LookupError("invalid UIA ref")
                wrapper = descendants[int(match.group(1)) - 1]
                if not wrapper.is_enabled():
                    raise LookupError("disabled UIA target")
                wrapper.click_input()
                return {"index": index, "type": "click", "backend": "uia", "status": "completed"}
            if automation_id:
                target = window.child_window(auto_id=automation_id)
            elif title:
                criteria: dict[str, str] = {"title": title}
                if control_type:
                    criteria["control_type"] = control_type
                target = window.child_window(**criteria)
            else:
                raise LookupError("no UIA target")
            target.wait("enabled", timeout=self.timeout_seconds)
            target.wrapper_object().click_input()
            return {"index": index, "type": "click", "backend": "uia", "status": "completed"}
        except Exception as exc:
            if not self.allow_pyautogui or "x" not in action or "y" not in action:
                raise DesktopFailure(
                    "target_not_found", "UIA-элемент не найден; coordinate fallback отключён."
                ) from exc
            import pyautogui

            pyautogui.click(int(action["x"]), int(action["y"]))
            return {
                "index": index,
                "type": "click",
                "backend": "pyautogui",
                "status": "completed",
            }

    def _guard(self, title: str) -> None:
        if self.emergency_stop():
            raise DesktopFailure("emergency_stop", "Emergency stop включён.")
        if title not in self.allowed_window_titles:
            raise DesktopFailure("window_not_allowed", "Окно отсутствует в desktop allowlist.")
        if SENSITIVE_TITLE_PATTERN.search(title):
            raise DesktopFailure("sensitive_window", "Доступ к чувствительному окну запрещён.")

    def _screenshot_path(self, relative_path: str | None) -> Path:
        clean = (relative_path or f"desktop/screenshots/{uuid4().hex}.png").strip().replace("\\", "/")
        if not clean.casefold().endswith(".png"):
            raise DesktopFailure("invalid_screenshot_path", "Desktop screenshot должен быть PNG.")
        target = (self.workspace_dir / clean).resolve()
        if target == self.workspace_dir or self.workspace_dir not in target.parents:
            raise DesktopFailure("path_outside_workspace", "Screenshot path выходит за workspace.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _artifact(self, target: Path) -> dict:
        return {
            "path": target.relative_to(self.workspace_dir).as_posix(),
            "name": target.name,
            "sha256": sha256(target.read_bytes()).hexdigest(),
            "size_bytes": target.stat().st_size,
        }


def desktop_snapshot_result(runner: DesktopRunner, arguments: dict) -> ToolResult:
    return _desktop_result(
        lambda: runner.snapshot(
            window_title=str(arguments.get("window_title", "")),
            screenshot_path=str(arguments.get("screenshot_path", "")).strip() or None,
        )
    )


def desktop_interact_result(runner: DesktopRunner, arguments: dict) -> ToolResult:
    actions = arguments.get("actions", [])
    return _desktop_result(
        lambda: runner.interact(
            window_title=str(arguments.get("window_title", "")),
            actions=actions if isinstance(actions, list) else [],
            expected_screen_hash=str(arguments.get("expected_screen_hash", "")).strip() or None,
            screenshot_path=str(arguments.get("screenshot_path", "")).strip() or None,
        )
    )


def _desktop_result(operation: Callable[[], dict]) -> ToolResult:
    try:
        return ToolResult(status="completed", output=operation())
    except DesktopFailure as exc:
        return failure_result(exc.code, exc.message)


def _image_hash(image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return sha256(buffer.getvalue()).hexdigest()
