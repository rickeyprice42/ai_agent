from __future__ import annotations

from pathlib import Path
import re

from ai_agent.tools.code_tools import BoundedProcessRunner


class GitSandbox:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.runner = BoundedProcessRunner(self.workspace_dir, timeout_seconds=45)

    def status(self) -> dict:
        return self._git(["status", "--short", "--branch"])

    def diff(self, *, staged: bool = False, path: str = "", context_lines: int = 3) -> dict:
        arguments = ["diff", f"--unified={max(0, min(int(context_lines), 20))}"]
        if staged:
            arguments.append("--cached")
        if path:
            arguments.extend(["--", self._safe_relative(path)])
        return self._git(arguments)

    def log(self, limit: int = 20) -> dict:
        safe_limit = max(1, min(int(limit), 100))
        return self._git(["log", f"-{safe_limit}", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%an%x09%s"])

    def inspect_ref(self, ref: str = "HEAD") -> dict:
        safe_ref = _safe_ref(ref)
        return self._git(["show", "--no-patch", "--date=iso-strict", "--format=%H%n%ad%n%an%n%s", safe_ref])

    def branches(self) -> dict:
        return self._git(["branch", "--all", "--verbose", "--no-abbrev"])

    def stage(self, paths: list[str]) -> dict:
        if not paths:
            raise ValueError("Нужно указать хотя бы один путь для stage.")
        safe_paths = [self._safe_relative(path, allow_dot=True) for path in paths]
        return self._git(["add", "--", *safe_paths])

    def commit(self, message: str) -> dict:
        normalized = message.strip()
        if not normalized or len(normalized) > 200 or "\n" in normalized or "\r" in normalized:
            raise ValueError("Commit message должен быть одной строкой длиной до 200 символов.")
        return self._git(["commit", "-m", normalized], timeout_seconds=90)

    def create_branch(self, name: str, start_point: str = "HEAD") -> dict:
        branch = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", branch) or ".." in branch or branch.endswith("/"):
            raise ValueError("Недопустимое имя Git-ветки.")
        return self._git(["branch", branch, _safe_ref(start_point)])

    def _git(self, arguments: list[str], timeout_seconds: int | None = None) -> dict:
        self._require_repository()
        result = self.runner.run(["git", *arguments], timeout_seconds=timeout_seconds)
        result["operation"] = arguments[0]
        return result

    def _require_repository(self) -> None:
        marker = self.workspace_dir / ".git"
        if not marker.exists():
            raise ValueError("Workspace не является Git-репозиторием.")

    def _safe_relative(self, value: str, *, allow_dot: bool = False) -> str:
        cleaned = value.strip().replace("\\", "/")
        if allow_dot and cleaned == ".":
            return cleaned
        if not cleaned or cleaned.startswith("-"):
            raise ValueError("Git path не должен быть пустым или похожим на option.")
        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Git path должен быть относительным.")
        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Git path выходит за пределы workspace.")
        relative = resolved.relative_to(self.workspace_dir).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            raise ValueError("Прямые операции с .git запрещены.")
        return relative


def _safe_ref(value: str) -> str:
    ref = value.strip()
    if not ref or ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._/@{}~^:+-]{1,160}", ref):
        raise ValueError("Недопустимый Git ref.")
    return ref
