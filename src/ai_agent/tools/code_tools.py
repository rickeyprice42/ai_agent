from __future__ import annotations

from collections import Counter, deque
from pathlib import Path
from time import perf_counter
from typing import Any
import os
import subprocess
import sys

from ai_agent.tools.shell import _sandbox_environment


IGNORED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript React",
    ".js": "JavaScript",
    ".jsx": "JavaScript React",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
}
MANIFESTS = {
    "pyproject.toml", "requirements.txt", "package.json", "pnpm-lock.yaml",
    "package-lock.json", "Cargo.toml", "go.mod", "pom.xml", ".gitignore",
}


class BoundedProcessRunner:
    def __init__(self, workspace_dir: Path, timeout_seconds: int = 60, max_output_chars: int = 12000) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.timeout_seconds = max(1, min(int(timeout_seconds), 300))
        self.max_output_chars = max(1000, min(int(max_output_chars), 50000))
        self._history: deque[dict[str, Any]] = deque(maxlen=20)

    def run(self, arguments: list[str], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        if not arguments or any("\x00" in value for value in arguments):
            raise ValueError("Некорректная команда процесса.")
        timeout = self.timeout_seconds if timeout_seconds is None else max(1, min(int(timeout_seconds), 300))
        started = perf_counter()
        timed_out = False
        try:
            completed = subprocess.run(
                arguments,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env=_sandbox_environment(),
            )
            return_code: int | None = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            return_code = None
            stdout = _timeout_text(exc.stdout)
            stderr = _timeout_text(exc.stderr)
        except FileNotFoundError as exc:
            raise ValueError(f"Исполняемый файл недоступен: {arguments[0]}") from exc

        stdout, stdout_truncated = _bounded(stdout, self.max_output_chars)
        stderr, stderr_truncated = _bounded(stderr, self.max_output_chars)
        result = {
            "command": arguments,
            "cwd": ".",
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "truncated": stdout_truncated or stderr_truncated,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        }
        self._history.appendleft(dict(result))
        return result

    def status(self, limit: int = 10) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 20))
        return {"active": [], "recent": list(self._history)[:safe_limit]}


class CodeSandbox:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.runner = BoundedProcessRunner(self.workspace_dir)

    def inspect_project(self, limit: int = 2000) -> dict[str, Any]:
        safe_limit = max(10, min(int(limit), 5000))
        languages: Counter[str] = Counter()
        manifests: list[str] = []
        file_count = 0
        total_bytes = 0
        truncated = False
        for root, directories, files in os.walk(self.workspace_dir):
            directories[:] = [item for item in directories if item not in IGNORED_DIRECTORIES]
            for name in sorted(files):
                path = Path(root) / name
                file_count += 1
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
                language = LANGUAGES.get(path.suffix.casefold())
                if language:
                    languages[language] += 1
                if name in MANIFESTS:
                    manifests.append(path.relative_to(self.workspace_dir).as_posix())
                if file_count >= safe_limit:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "root": ".",
            "file_count": file_count,
            "total_bytes": total_bytes,
            "languages": dict(languages.most_common()),
            "manifests": sorted(manifests),
            "git_repository": (self.workspace_dir / ".git").exists(),
            "truncated": truncated,
        }

    def run_check(self, runner: str, target: str = "", timeout_seconds: int = 60) -> dict[str, Any]:
        target_path = self._safe_target(target) if target else ""
        commands = {
            "python_unittest": [sys.executable, "-m", "unittest", "discover", "-s", target_path or "tests"],
            "python_pytest": [sys.executable, "-m", "pytest"] + ([target_path] if target_path else []),
            "python_compile": [sys.executable, "-m", "compileall", target_path or "."],
            "npm_test": ["npm", "test", "--", "--runInBand"],
            "npm_lint": ["npm", "run", "lint", "--if-present"],
            "npm_typecheck": ["npm", "run", "typecheck", "--if-present"],
            "npm_build": ["npm", "run", "build"],
        }
        if runner not in commands:
            raise ValueError("Неизвестный runner. Разрешены только предопределённые проверки.")
        result = self.runner.run(commands[runner], timeout_seconds=timeout_seconds)
        result["check"] = runner
        result["target"] = target_path
        return result

    def process_status(self, limit: int = 10) -> dict[str, Any]:
        return self.runner.status(limit)

    def _safe_target(self, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        if not cleaned:
            return ""
        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Target должен быть относительным путём внутри workspace.")
        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Target выходит за пределы workspace.")
        return resolved.relative_to(self.workspace_dir).as_posix()


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[: limit - 3] + "...", True
