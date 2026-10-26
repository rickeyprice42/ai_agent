from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
from uuid import uuid4

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

    def read_lines(
        self,
        relative_path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict:
        target = self._require_file(relative_path)
        start = max(1, int(start_line))
        requested_end = int(end_line) if end_line is not None else start + 199
        end = max(start, min(requested_end, start + 499))
        raw = target.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        selected = lines[start - 1 : end]
        content = "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start=start))
        truncated = len(content) > self.max_read_chars
        if truncated:
            content = content[: self.max_read_chars]
        return {
            "path": self._relative_display_path(target),
            "sha256": _file_sha256(target),
            "start_line": start,
            "end_line": min(end, len(lines)),
            "total_lines": len(lines),
            "content": content,
            "truncated": truncated or end < len(lines),
        }

    def metadata(self, relative_path: str) -> dict:
        target = self._require_file(relative_path)
        stat = target.stat()
        return {
            "path": self._relative_display_path(target),
            "name": target.name,
            "extension": target.suffix.lower(),
            "size_bytes": int(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "sha256": _file_sha256(target),
        }

    def search(
        self,
        query: str,
        relative_path: str = "",
        *,
        include_content: bool = True,
        limit: int = 50,
    ) -> dict:
        needle = query.strip().casefold()
        if not needle:
            raise ValueError("Поисковый запрос не должен быть пустым.")
        root = self._resolve_safe_folder(relative_path)
        safe_limit = max(1, min(int(limit), 100))
        matches: list[dict] = []
        scanned = 0
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file() or _is_hidden_or_internal(path, self.workspace_dir):
                continue
            scanned += 1
            display = self._relative_display_path(path)
            if needle in display.casefold():
                matches.append({"path": display, "line": None, "preview": "filename match"})
            if include_content and path.stat().st_size <= 2 * 1024 * 1024:
                try:
                    for number, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
                    ):
                        if needle in line.casefold():
                            matches.append(
                                {"path": display, "line": number, "preview": _single_line(line, 240)}
                            )
                            if len(matches) >= safe_limit:
                                break
                except OSError:
                    pass
            if len(matches) >= safe_limit:
                break
        return {
            "query": query,
            "root": self._relative_display_path(root),
            "scanned_files": scanned,
            "matches": matches[:safe_limit],
            "truncated": len(matches) >= safe_limit,
        }

    def exact_edit(
        self,
        relative_path: str,
        expected_sha256: str,
        old_text: str,
        new_text: str,
    ) -> dict:
        target = self._require_file(relative_path)
        before_hash = _file_sha256(target)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) or before_hash != expected_sha256.lower():
            raise ValueError("stale_file: файл изменился после чтения; edit отклонён.")
        if not old_text:
            raise ValueError("old_text не должен быть пустым.")
        content = target.read_text(encoding="utf-8", errors="strict")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ValueError(
                f"exact_match_required: old_text должен встречаться ровно один раз, найдено: {occurrences}."
            )
        updated = content.replace(old_text, new_text, 1)
        if len(updated) > self.max_write_chars:
            raise ValueError(f"Результат слишком большой. Лимит: {self.max_write_chars} символов.")
        if _file_sha256(target) != before_hash:
            raise ValueError("stale_file: файл изменился во время подготовки edit.")
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(updated, encoding="utf-8", newline="")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {
            "path": self._relative_display_path(target),
            "before_sha256": before_hash,
            "after_sha256": _file_sha256(target),
            "replacements": 1,
            "size_bytes": target.stat().st_size,
        }

    def move_file(
        self,
        source: str,
        destination: str,
        expected_sha256: str,
        *,
        overwrite: bool = False,
    ) -> dict:
        source_path = self._require_file(source)
        before_hash = _file_sha256(source_path)
        if before_hash != expected_sha256.lower():
            raise ValueError("stale_file: исходный файл изменился; move отклонён.")
        destination_path = self._resolve_safe_path(destination)
        if destination_path.exists() and not overwrite:
            raise ValueError("Файл назначения уже существует; move не выполнен.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if _file_sha256(source_path) != before_hash:
            raise ValueError("stale_file: исходный файл изменился во время move.")
        if overwrite:
            os.replace(source_path, destination_path)
        else:
            source_path.rename(destination_path)
        return {
            "source": source,
            "destination": self._relative_display_path(destination_path),
            "sha256": before_hash,
            "overwritten": bool(overwrite),
        }

    def trash_file(self, relative_path: str, expected_sha256: str) -> dict:
        target = self._require_file(relative_path)
        before_hash = _file_sha256(target)
        if before_hash != expected_sha256.lower():
            raise ValueError("stale_file: файл изменился; delete отклонён.")
        trash_root = (self.workspace_dir / ".avelin-trash").resolve()
        trash_root.mkdir(parents=True, exist_ok=True)
        recovery_name = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}-{target.name}"
        recovery_path = trash_root / recovery_name
        shutil.move(str(target), str(recovery_path))
        return {
            "path": relative_path,
            "sha256": before_hash,
            "recovery_path": self._relative_display_path(recovery_path),
            "recoverable": True,
        }

    def restore_file(
        self,
        recovery_path: str,
        destination: str,
        expected_sha256: str,
    ) -> dict:
        cleaned = recovery_path.strip().replace("\\", "/")
        if not cleaned.startswith(".avelin-trash/"):
            raise ValueError("Восстановление разрешено только из recoverable trash-зоны.")
        trash_root = (self.workspace_dir / ".avelin-trash").resolve()
        source = (self.workspace_dir / Path(cleaned)).resolve()
        if trash_root not in source.parents or not source.is_file():
            raise ValueError("Recoverable файл не найден.")
        before_hash = _file_sha256(source)
        if before_hash != expected_sha256.lower():
            raise ValueError("stale_file: recoverable файл изменился; restore отклонён.")
        destination_path = self._resolve_safe_path(destination)
        if destination_path.exists():
            raise ValueError("Файл назначения уже существует; restore не выполнен.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination_path)
        return {
            "recovery_path": cleaned,
            "destination": self._relative_display_path(destination_path),
            "sha256": before_hash,
            "restored": True,
        }

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

    def describe_file(self, relative_path: str) -> WorkspaceFile:
        target = self._resolve_safe_path(relative_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"Файл не найден в рабочей папке: {relative_path}")
        if _is_hidden_or_internal(target, self.workspace_dir):
            raise ValueError("Скрытые и служебные файлы недоступны.")
        stat = target.stat()
        return WorkspaceFile(
            path=self._relative_display_path(target),
            name=target.name,
            extension=target.suffix.lower(),
            size_bytes=int(stat.st_size),
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        )

    def resolve_download_path(self, relative_path: str) -> Path:
        file = self.describe_file(relative_path)
        return self._resolve_safe_path(file.path)

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

    def _require_file(self, relative_path: str) -> Path:
        target = self._resolve_safe_path(relative_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"Файл не найден в рабочей папке: {relative_path}")
        if _is_hidden_or_internal(target, self.workspace_dir):
            raise ValueError("Скрытые и служебные файлы недоступны.")
        return target

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


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _single_line(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


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
