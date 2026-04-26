from __future__ import annotations

from pathlib import Path
import shlex
import subprocess


ALLOWED_PREFIXES = (
    ("python", "-m", "compileall"),
    ("python", "-m", "pytest"),
    ("npm", "test"),
    ("npm", "run", "build"),
    ("git", "status", "--short"),
)

PSEUDO_LIST_COMMANDS = {"dir", "ls", "get-childitem"}
BLOCKED_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "`"}


class ShellSandbox:
    def __init__(self, workspace_dir: Path, timeout_seconds: int = 30) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.timeout_seconds = timeout_seconds
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def run(self, command: str) -> str:
        args = _split_command(command)
        self._validate_command(args)

        if args[0].lower() in PSEUDO_LIST_COMMANDS:
            return self._list_workspace()

        try:
            completed = subprocess.run(
                args,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise ValueError(f"Команда недоступна в системе: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return _format_shell_result(
                args=args,
                return_code=-1,
                stdout=stdout,
                stderr=f"Команда остановлена по таймауту {self.timeout_seconds} сек.\n{stderr}".strip(),
            )

        return _format_shell_result(
            args=args,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _validate_command(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Команда не должна быть пустой.")

        normalized = tuple(arg.lower() for arg in args)
        if any(token in args for token in BLOCKED_TOKENS):
            raise ValueError("Команда содержит запрещенный оператор shell.")

        if normalized[0] in PSEUDO_LIST_COMMANDS:
            if len(normalized) > 1:
                raise ValueError("Команда просмотра папки не принимает аргументы.")
            return

        if not any(_starts_with(normalized, prefix) for prefix in ALLOWED_PREFIXES):
            allowed = ", ".join(" ".join(prefix) for prefix in ALLOWED_PREFIXES)
            raise ValueError(f"Команда не входит в allowlist. Разрешено: {allowed}, dir, ls.")

        if any(_looks_like_path_escape(arg) for arg in normalized):
            raise ValueError("Аргументы команды не должны выходить из рабочей папки инструментов.")

    def _list_workspace(self) -> str:
        items = sorted(self.workspace_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        if not items:
            return f"Рабочая папка пуста: {self.workspace_dir}"

        lines = [f"Рабочая папка: {self.workspace_dir}", "Содержимое:"]
        for item in items:
            kind = "dir " if item.is_dir() else "file"
            size = "" if item.is_dir() else f" {item.stat().st_size} bytes"
            lines.append(f"- {kind}: {item.name}{size}")
        return "\n".join(lines)


def _split_command(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        return []
    if "\n" in stripped or "\r" in stripped:
        raise ValueError("Многострочные shell-команды запрещены.")
    try:
        return shlex.split(stripped, posix=False)
    except ValueError as exc:
        raise ValueError(f"Не удалось разобрать команду: {exc}") from exc


def _starts_with(value: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(value) >= len(prefix) and value[: len(prefix)] == prefix


def _looks_like_path_escape(argument: str) -> bool:
    normalized = argument.replace("\\", "/")
    return normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized


def _format_shell_result(args: list[str], return_code: int, stdout: str, stderr: str) -> str:
    lines = [
        f"Команда: {' '.join(args)}",
        f"Код возврата: {return_code}",
    ]
    if stdout.strip():
        lines.extend(["stdout:", _shorten(stdout.strip())])
    if stderr.strip():
        lines.extend(["stderr:", _shorten(stderr.strip())])
    if not stdout.strip() and not stderr.strip():
        lines.append("Вывод пуст.")
    return "\n".join(lines)


def _shorten(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."
