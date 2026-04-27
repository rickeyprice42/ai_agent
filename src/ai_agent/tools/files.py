from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import platform
import subprocess

from ai_agent.types import WorkspaceFile


class FileSandbox:
    def __init__(
        self,
        workspace_dir: Path,
        max_read_chars: int = 20000,
        max_write_chars: int = 20000,
    ) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.max_read_chars = max_read_chars
        self.max_write_chars = max_write_chars
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def read_file(self, relative_path: str) -> str:
        target = self._resolve_safe_path(relative_path)
        if not target.exists():
            raise ValueError(f"Файл не найден в рабочей папке: {relative_path}")
        if not target.is_file():
            raise ValueError(f"Путь не является файлом: {relative_path}")

        content = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > self.max_read_chars
        if truncated:
            content = content[: self.max_read_chars]

        header = [
            f"Файл: {self._relative_display_path(target)}",
            f"Размер текста: {len(content)} символов" + (" (обрезано)" if truncated else ""),
            "Содержимое:",
        ]
        return "\n".join(header + [content])

    def write_file(self, relative_path: str, content: str, overwrite: bool = False) -> str:
        target = self._resolve_safe_path(relative_path)
        existed_before = target.exists()
        if target.exists() and not target.is_file():
            raise ValueError(f"Путь существует, но не является файлом: {relative_path}")
        if target.exists() and not overwrite:
            raise ValueError(
                "Файл уже существует. Передай overwrite=true, если его действительно нужно заменить."
            )
        if len(content) > self.max_write_chars:
            raise ValueError(
                f"Содержимое слишком большое: {len(content)} символов. "
                f"Лимит: {self.max_write_chars}."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        action = "перезаписан" if existed_before else "создан"
        return (
            f"Файл {action}: {self._relative_display_path(target)}\n"
            f"Записано символов: {len(content)}"
        )

    def list_files(self, limit: int = 30) -> list[WorkspaceFile]:
        safe_limit = max(1, min(int(limit), 100))
        items = [
            path
            for path in self.workspace_dir.rglob("*")
            if path.is_file() and not _is_hidden_or_internal(path, self.workspace_dir)
        ]
        items.sort(key=lambda path: path.stat().st_mtime, reverse=True)

        files: list[WorkspaceFile] = []
        for path in items[:safe_limit]:
            stat = path.stat()
            files.append(
                WorkspaceFile(
                    path=self._relative_display_path(path),
                    name=path.name,
                    extension=path.suffix.lower(),
                    size_bytes=int(stat.st_size),
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            )
        return files

    def format_file_list(self, limit: int = 30) -> str:
        files = self.list_files(limit=limit)
        if not files:
            return f"Рабочая папка пуста: {self.workspace_dir}"

        lines = ["Файлы в рабочей папке инструментов:"]
        for file in files:
            lines.append(
                f"- {file.path} ({_format_size(file.size_bytes)}, изменен: {file.modified_at})"
            )
        return "\n".join(lines)

    def open_folder(self, relative_path: str = "") -> str:
        target = self._resolve_safe_folder(relative_path)
        target.mkdir(parents=True, exist_ok=True)
        _open_folder_in_os(target)
        return f"Открыта рабочая папка: {self._relative_display_path(target)}"

    def _resolve_safe_path(self, relative_path: str) -> Path:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Путь к файлу не должен быть пустым.")

        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Разрешены только относительные пути внутри рабочей папки инструментов.")

        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Файл находится вне безопасной рабочей папки инструментов.")
        return resolved

    def _resolve_safe_folder(self, relative_path: str) -> Path:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            return self.workspace_dir

        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Разрешены только относительные пути внутри рабочей папки инструментов.")

        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Папка находится вне безопасной рабочей папки инструментов.")
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"Путь существует, но не является папкой: {relative_path}")
        return resolved

    def _relative_display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return path.name


def _is_hidden_or_internal(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part.startswith(".") or part == "__pycache__" for part in relative_parts)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _open_folder_in_os(path: Path) -> None:
    system = platform.system().lower()
    try:
        if system == "windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if system == "darwin":
            subprocess.Popen(["open", str(path)])
            return
        subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise ValueError(f"Не удалось открыть папку: {exc}") from exc
