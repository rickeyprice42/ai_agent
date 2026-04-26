from __future__ import annotations

from pathlib import Path


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

    def _relative_display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return path.name
