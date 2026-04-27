from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
import re
import zipfile


class DocumentSandbox:
    def __init__(self, workspace_dir: Path, max_text_chars: int = 20000) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.max_text_chars = max_text_chars
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_docx(
        self,
        relative_path: str,
        title: str = "",
        paragraphs: list[str] | None = None,
        bullets: list[str] | None = None,
        overwrite: bool = False,
    ) -> str:
        target = self._resolve_safe_path(relative_path)
        if target.suffix.lower() != ".docx":
            raise ValueError("Документ должен иметь расширение .docx.")
        if target.exists() and not target.is_file():
            raise ValueError(f"Путь существует, но не является файлом: {relative_path}")
        if target.exists() and not overwrite:
            raise ValueError(
                "Документ уже существует. Передай overwrite=true, если его действительно нужно заменить."
            )
        existed_before = target.exists()

        normalized_title = title.strip()
        normalized_paragraphs = _normalize_text_items(paragraphs or [])
        normalized_bullets = _normalize_text_items(bullets or [])
        total_text = normalized_title + "\n".join(normalized_paragraphs + normalized_bullets)
        if not total_text.strip():
            raise ValueError("Документ должен содержать заголовок, абзац или список.")
        if len(total_text) > self.max_text_chars:
            raise ValueError(
                f"Текст документа слишком большой: {len(total_text)} символов. "
                f"Лимит: {self.max_text_chars}."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        _write_minimal_docx(
            target,
            title=normalized_title,
            paragraphs=normalized_paragraphs,
            bullets=normalized_bullets,
        )
        action = "перезаписан" if existed_before else "создан"
        return (
            f"DOCX документ {action}: {self._relative_display_path(target)}\n"
            f"Абзацев: {len(normalized_paragraphs)}\n"
            f"Пунктов списка: {len(normalized_bullets)}"
        )

    def append_docx(
        self,
        relative_path: str,
        paragraphs: list[str] | None = None,
        bullets: list[str] | None = None,
    ) -> str:
        target = self._resolve_safe_path(relative_path)
        if target.suffix.lower() != ".docx":
            raise ValueError("Документ должен иметь расширение .docx.")
        if not target.exists():
            raise ValueError(f"Документ не найден в рабочей папке: {relative_path}")
        if not target.is_file():
            raise ValueError(f"Путь не является файлом: {relative_path}")

        normalized_paragraphs = _normalize_text_items(paragraphs or [])
        normalized_bullets = _normalize_text_items(bullets or [])
        total_text = "\n".join(normalized_paragraphs + normalized_bullets)
        if not total_text.strip():
            raise ValueError("Нужно передать хотя бы один абзац или пункт списка.")
        if len(total_text) > self.max_text_chars:
            raise ValueError(
                f"Текст для добавления слишком большой: {len(total_text)} символов. "
                f"Лимит: {self.max_text_chars}."
            )

        _append_to_docx(
            target,
            paragraphs=normalized_paragraphs,
            bullets=normalized_bullets,
        )
        return (
            f"DOCX документ дополнен: {self._relative_display_path(target)}\n"
            f"Добавлено абзацев: {len(normalized_paragraphs)}\n"
            f"Добавлено пунктов списка: {len(normalized_bullets)}"
        )

    def _resolve_safe_path(self, relative_path: str) -> Path:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Путь к документу не должен быть пустым.")

        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Разрешены только относительные пути внутри рабочей папки инструментов.")

        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Документ находится вне безопасной рабочей папки инструментов.")
        return resolved

    def _relative_display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return path.name


def _normalize_text_items(items: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in items:
        text = " ".join(str(item).split()).strip()
        if text:
            normalized.append(text)
    return normalized


def _write_minimal_docx(
    target: Path,
    title: str,
    paragraphs: list[str],
    bullets: list[str],
) -> None:
    document_xml = _document_xml(title=title, paragraphs=paragraphs, bullets=bullets)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("word/_rels/document.xml.rels", _document_rels_xml())
        archive.writestr("word/document.xml", document_xml)


def _append_to_docx(target: Path, paragraphs: list[str], bullets: list[str]) -> None:
    with zipfile.ZipFile(target, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}

    try:
        document_xml = entries["word/document.xml"].decode("utf-8")
    except KeyError as exc:
        raise ValueError("DOCX не содержит word/document.xml.") from exc

    addition = "".join(_paragraph_xml(paragraph) for paragraph in paragraphs)
    addition += "".join(_paragraph_xml(f"- {bullet}") for bullet in bullets)
    updated_xml = _insert_before_section_properties(document_xml, addition)
    entries["word/document.xml"] = updated_xml.encode("utf-8")

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def _insert_before_section_properties(document_xml: str, addition_xml: str) -> str:
    marker = "<w:sectPr>"
    marker_index = document_xml.rfind(marker)
    if marker_index >= 0:
        return document_xml[:marker_index] + addition_xml + document_xml[marker_index:]

    body_end = document_xml.rfind("</w:body>")
    if body_end < 0:
        raise ValueError("DOCX имеет неподдерживаемую структуру документа.")
    return document_xml[:body_end] + addition_xml + document_xml[body_end:]


def _document_xml(title: str, paragraphs: list[str], bullets: list[str]) -> str:
    body_parts: list[str] = []
    if title:
        body_parts.append(_paragraph_xml(title, bold=True, font_size=32, spacing_after=220))
    body_parts.extend(_paragraph_xml(paragraph) for paragraph in paragraphs)
    body_parts.extend(_paragraph_xml(f"- {bullet}") for bullet in bullets)
    body_parts.append(
        "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{''.join(body_parts)}</w:body>"
        "</w:document>"
    )


def _paragraph_xml(text: str, bold: bool = False, font_size: int | None = None, spacing_after: int = 120) -> str:
    runs = "".join(
        _run_xml(part, bold=bold or part_bold, italic=part_italic, font_size=font_size)
        for part, part_bold, part_italic in _markup_runs(text)
    )
    return f"<w:p><w:pPr><w:spacing w:after=\"{spacing_after}\"/></w:pPr>{runs}</w:p>"


def _markup_runs(text: str) -> list[tuple[str, bool, bool]]:
    runs: list[tuple[str, bool, bool]] = []
    pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            runs.append((text[position : match.start()], False, False))
        token = match.group(0)
        if token.startswith("**"):
            runs.append((token[2:-2], True, False))
        else:
            runs.append((token[1:-1], False, True))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], False, False))
    return runs or [(text, False, False)]


def _run_xml(text: str, bold: bool = False, italic: bool = False, font_size: int | None = None) -> str:
    props: list[str] = []
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if font_size is not None:
        props.append(f"<w:sz w:val=\"{font_size}\"/>")
    run_props = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{run_props}<w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r>"


def _content_types_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
        "</Relationships>"
    )


def _document_rels_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>"
    )
