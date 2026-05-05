from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
import re
import zipfile


PRESENTATION_CX = 12192000
PRESENTATION_CY = 6858000
MIN_SLIDES_FOR_AGENDA = 4
MAX_BULLETS_PER_SLIDE = 7
MAX_BULLET_CHARS = 150
MAX_BODY_CHARS = 900
SUPPORTED_LAYOUTS = {
    "title",
    "section",
    "agenda",
    "content",
    "image",
    "closing",
    "quote",
    "comparison",
    "metrics",
}


@dataclass(slots=True)
class PresentationTheme:
    name: str = "business"
    background: str = "FFFFFF"
    foreground: str = "111111"
    accent: str = "2F6BFF"
    surface: str = "F5F7FB"
    muted: str = "6B7280"
    font_major: str = "Aptos Display"
    font_minor: str = "Aptos"


@dataclass(slots=True)
class SlideContent:
    layout: str
    title: str
    subtitle: str
    bullets: list[str]
    body: str
    caption: str
    source: str
    image_path: str
    image_file: Path | None


class PresentationSandbox:
    def __init__(self, workspace_dir: Path, max_text_chars: int = 30000, max_slides: int = 30) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.max_text_chars = max_text_chars
        self.max_slides = max_slides
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def create_presentation(
        self,
        relative_path: str,
        title: str = "",
        slides: list[dict[str, Any]] | None = None,
        theme: str = "business",
        topic: str = "",
        audience: str = "",
        purpose: str = "",
        slide_count: int = 0,
        style: str = "",
        overwrite: bool = False,
    ) -> str:
        target = self._resolve_safe_path(relative_path)
        if target.suffix.lower() != ".pptx":
            raise ValueError("Презентация должна иметь расширение .pptx.")
        if target.exists() and not target.is_file():
            raise ValueError(f"Путь существует, но не является файлом: {relative_path}")
        if target.exists() and not overwrite:
            raise ValueError(
                "Презентация уже существует. Передай overwrite=true, если ее действительно нужно заменить."
            )

        normalized_title = _normalize_text(title) or _normalize_text(topic) or _title_from_path(target)
        normalized_topic = _normalize_text(topic) or normalized_title
        normalized_audience = _normalize_text(audience)
        normalized_purpose = _normalize_text(purpose)
        normalized_style = _normalize_text(style)
        requested_slide_count = _normalize_slide_count(slide_count)
        normalized_slides = self._normalize_slides(slides or [], fallback_title=normalized_title)
        if not normalized_slides:
            normalized_slides = self._generated_slides(
                title=normalized_title,
                topic=normalized_topic,
                audience=normalized_audience,
                purpose=normalized_purpose,
                slide_count=requested_slide_count,
                style=normalized_style,
            )
        else:
            normalized_slides = _polish_slide_sequence(
                normalized_slides,
                fallback_title=normalized_title,
                audience=normalized_audience,
                purpose=normalized_purpose,
            )

        if len(normalized_slides) > self.max_slides:
            raise ValueError(f"Слишком много слайдов: {len(normalized_slides)}. Лимит: {self.max_slides}.")

        total_text = normalized_title + "\n".join(_slide_text(slide) for slide in normalized_slides)
        if not total_text.strip():
            raise ValueError("Презентация должна содержать хотя бы один текстовый элемент.")
        if len(total_text) > self.max_text_chars:
            raise ValueError(
                f"Текст презентации слишком большой: {len(total_text)} символов. "
                f"Лимит: {self.max_text_chars}."
            )
        _validate_slide_quality(normalized_slides)

        target.parent.mkdir(parents=True, exist_ok=True)
        existed_before = target.exists()
        selected_theme = _theme_from_name(_select_theme_name(theme, normalized_style))
        _write_minimal_pptx(
            target=target,
            title=normalized_title,
            slides=normalized_slides,
            theme=selected_theme,
        )
        _validate_pptx_package(target, expected_slides=len(normalized_slides))
        action = "перезаписана" if existed_before else "создана"
        return (
            f"PPTX презентация {action}: {self._relative_display_path(target)}\n"
            f"Слайдов: {len(normalized_slides)}\n"
            f"Тема: {selected_theme.name}\n"
            "Проверка: валидный PPTX-пакет"
        )

    def _resolve_safe_path(self, relative_path: str) -> Path:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            raise ValueError("Путь к презентации не должен быть пустым.")

        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Разрешены только относительные пути внутри рабочей папки инструментов.")

        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Презентация находится вне безопасной рабочей папки инструментов.")
        return resolved

    def _resolve_optional_image(self, relative_path: str) -> Path | None:
        cleaned = relative_path.strip().replace("\\", "/")
        if not cleaned:
            return None

        candidate = Path(cleaned)
        if candidate.is_absolute():
            raise ValueError("Путь к изображению должен быть относительным внутри рабочей папки инструментов.")

        resolved = (self.workspace_dir / candidate).resolve()
        if resolved != self.workspace_dir and self.workspace_dir not in resolved.parents:
            raise ValueError("Изображение находится вне безопасной рабочей папки инструментов.")
        if not resolved.exists():
            raise ValueError(f"Изображение не найдено в рабочей папке: {relative_path}")
        if not resolved.is_file():
            raise ValueError(f"Путь к изображению не является файлом: {relative_path}")
        if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif"}:
            raise ValueError("Поддерживаются только изображения .png, .jpg, .jpeg и .gif.")
        if resolved.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Изображение слишком большое. Лимит: 10 MB.")
        return resolved

    def _relative_display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return path.name

    def _normalize_slides(self, raw_slides: list[dict[str, Any]], fallback_title: str) -> list[SlideContent]:
        slides: list[SlideContent] = []
        for index, raw_slide in enumerate(raw_slides, start=1):
            if not isinstance(raw_slide, dict):
                continue

            layout = _normalize_layout(str(raw_slide.get("layout", "")))
            title = _limit_text(_normalize_text(raw_slide.get("title", "")), 120)
            subtitle = _limit_text(_normalize_text(raw_slide.get("subtitle", "")), 220)
            body = _limit_text(_normalize_text(raw_slide.get("body", "")), MAX_BODY_CHARS)
            caption = _limit_text(_normalize_text(raw_slide.get("caption", "")), 260)
            source = _limit_text(_normalize_text(raw_slide.get("source", "")), 260)
            image_path = _normalize_text(raw_slide.get("image_path", ""))
            image_file = self._resolve_optional_image(image_path)
            bullets = [_limit_text(item, MAX_BULLET_CHARS) for item in _normalize_text_items(raw_slide.get("bullets", []))]

            if not any([title, subtitle, body, caption, source, image_path, bullets]):
                continue
            if index == 1 and not title:
                title = fallback_title
            if image_file is not None and not source:
                source = f"Локальный файл: {image_path}"
            slides.append(
                SlideContent(
                    layout=layout,
                    title=title,
                    subtitle=subtitle,
                    bullets=bullets,
                    body=body,
                    caption=caption,
                    source=source,
                    image_path=image_path,
                    image_file=image_file,
                )
            )
        return slides

    def _generated_slides(
        self,
        title: str,
        topic: str,
        audience: str,
        purpose: str,
        slide_count: int,
        style: str,
    ) -> list[SlideContent]:
        target_count = slide_count or 6
        target_count = max(4, min(target_count, self.max_slides))
        agenda = _generated_agenda(topic, target_count)
        subtitle_parts = [part for part in [audience and f"Для аудитории: {audience}", purpose] if part]
        subtitle = " | ".join(subtitle_parts) or "Красивая презентация Avelin"

        raw_slides: list[dict[str, Any]] = [
            {"layout": "title", "title": title, "subtitle": subtitle},
            {"layout": "agenda", "title": "Структура", "bullets": agenda},
        ]
        content_slots = max(1, target_count - 3)
        for index, agenda_item in enumerate(agenda[:content_slots], start=1):
            raw_slides.append(
                {
                    "layout": "content",
                    "title": agenda_item,
                    "subtitle": _generated_slide_subtitle(topic, index, style),
                    "bullets": _generated_bullets(topic, agenda_item, audience, purpose, index),
                }
            )

        raw_slides.append(
            {
                "layout": "closing",
                "title": "Итоги",
                "bullets": _generated_closing_bullets(topic, purpose),
            }
        )
        return self._normalize_slides(raw_slides, fallback_title=title)


def _normalize_layout(layout: str) -> str:
    normalized = layout.strip().lower().replace("_", "-")
    if normalized in SUPPORTED_LAYOUTS:
        return normalized
    return "content"


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).split()).strip()


def _limit_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _normalize_text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _normalize_text(item))]


def _slide_text(slide: SlideContent) -> str:
    return "\n".join([slide.title, slide.subtitle, slide.body, slide.caption, slide.source, *slide.bullets])


def _title_from_path(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    return title.capitalize() or "Презентация"


def _theme_from_name(name: str) -> PresentationTheme:
    normalized = name.strip().lower().replace("_", "-")
    themes = {
        "business": PresentationTheme("business", "FFFFFF", "111111", "2F6BFF", "F5F7FB", "667085"),
        "strict": PresentationTheme("strict", "FFFFFF", "101010", "111111", "F4F4F5", "71717A"),
        "dark": PresentationTheme("dark", "111111", "F7F7F7", "7DD3FC", "1F2937", "A1A1AA"),
        "education": PresentationTheme("education", "FBFBF6", "1F2937", "16A34A", "EEF7ED", "64748B"),
        "pitch": PresentationTheme("pitch", "FAFAFA", "18181B", "E11D48", "FFF1F2", "71717A"),
        "product": PresentationTheme("product", "F8FAFC", "0F172A", "0D9488", "ECFEFF", "64748B"),
        "report": PresentationTheme("report", "F8FAFC", "0F172A", "2563EB", "EFF6FF", "64748B"),
    }
    return themes.get(normalized, themes["business"])


def _select_theme_name(theme: str, style: str) -> str:
    normalized_theme = theme.strip().lower().replace("_", "-")
    normalized_style = style.strip().lower().replace("_", "-")
    if normalized_theme and normalized_theme != "business":
        return normalized_theme
    if any(word in normalized_style for word in ("строг", "strict")):
        return "strict"
    if any(word in normalized_style for word in ("учеб", "education", "обуч")):
        return "education"
    if "pitch" in normalized_style or "питч" in normalized_style:
        return "pitch"
    if any(word in normalized_style for word in ("product", "продукт")):
        return "product"
    if any(word in normalized_style for word in ("report", "отчет", "исслед")):
        return "report"
    if any(word in normalized_style for word in ("dark", "темн")):
        return "dark"
    return normalized_theme or "business"


def _normalize_slide_count(value: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(count, 30))


def _polish_slide_sequence(
    slides: list[SlideContent],
    fallback_title: str,
    audience: str,
    purpose: str,
) -> list[SlideContent]:
    polished = list(slides)
    if not polished:
        return polished

    if polished[0].layout != "title":
        polished.insert(
            0,
            SlideContent(
                layout="title",
                title=fallback_title,
                subtitle=purpose or audience or "Презентация Avelin",
                bullets=[],
                body="",
                caption="",
                source="",
                image_path="",
                image_file=None,
            ),
        )

    has_agenda = any(slide.layout == "agenda" for slide in polished)
    if len(polished) >= MIN_SLIDES_FOR_AGENDA and not has_agenda:
        agenda_items = [slide.title for slide in polished[1:] if slide.title][:5]
        if agenda_items:
            polished.insert(
                1,
                SlideContent(
                    layout="agenda",
                    title="Структура",
                    subtitle="",
                    bullets=agenda_items,
                    body="",
                    caption="",
                    source="",
                    image_path="",
                    image_file=None,
                ),
            )

    if polished[-1].layout != "closing":
        polished.append(
            SlideContent(
                layout="closing",
                title="Итоги",
                subtitle="",
                bullets=["Ключевые идеи собраны", "Материал готов к обсуждению"],
                body="",
                caption="",
                source="",
                image_path="",
                image_file=None,
            )
        )
    return polished


def _generated_agenda(topic: str, slide_count: int) -> list[str]:
    base = [
        f"Контекст: {topic}",
        "Ключевые идеи",
        "Практическая ценность",
        "Риски и ограничения",
        "Следующие шаги",
    ]
    if slide_count <= 5:
        return base[:3]
    if slide_count >= 8:
        return [base[0], "Почему это важно", base[1], "Примеры применения", base[2], base[3], base[4]]
    return base


def _generated_slide_subtitle(topic: str, index: int, style: str) -> str:
    if "pitch" in style.lower():
        return "Фокус на ценности и решении"
    subtitles = [
        "Коротко и по делу",
        "Что важно понять",
        "Как это работает на практике",
        "Где нужна осторожность",
    ]
    return subtitles[(index - 1) % len(subtitles)] if topic else ""


def _generated_bullets(topic: str, agenda_item: str, audience: str, purpose: str, index: int) -> list[str]:
    audience_text = audience or "целевой аудитории"
    purpose_text = purpose or "принять понятное решение"
    templates = [
        [
            f"Определяем, какую задачу закрывает тема «{topic}»",
            f"Показываем, почему это важно для {audience_text}",
            "Выделяем 2-3 главных вывода без перегруза деталями",
        ],
        [
            f"Раскрываем блок «{agenda_item}» через простые тезисы",
            "Добавляем пример, который можно сразу обсудить",
            f"Связываем выводы с целью: {purpose_text}",
        ],
        [
            "Фиксируем практические сценарии использования",
            "Отделяем доказанные факты от предположений",
            "Оставляем место для визуального материала или данных",
        ],
        [
            "Показываем ограничения и открытые вопросы",
            "Предлагаем безопасный следующий шаг",
            "Формулируем критерий успешного результата",
        ],
    ]
    return templates[(index - 1) % len(templates)]


def _generated_closing_bullets(topic: str, purpose: str) -> list[str]:
    return [
        f"Тема «{topic}» сведена к понятной структуре",
        purpose or "Главные решения можно обсуждать на основе слайдов",
        "Следующий шаг: уточнить факты, цифры и визуальные материалы",
    ]


def _validate_slide_quality(slides: list[SlideContent]) -> None:
    for index, slide in enumerate(slides, start=1):
        if not _slide_text(slide).strip() and slide.image_file is None:
            raise ValueError(f"Слайд {index} пустой.")
        if len(slide.title) > 120:
            raise ValueError(f"Заголовок слайда {index} слишком длинный. Лимит: 120 символов.")
        if len(slide.body) > MAX_BODY_CHARS:
            raise ValueError(f"Текст слайда {index} слишком длинный. Лимит: {MAX_BODY_CHARS} символов.")
        if len(slide.bullets) > MAX_BULLETS_PER_SLIDE:
            raise ValueError(f"На слайде {index} слишком много пунктов. Лимит: {MAX_BULLETS_PER_SLIDE}.")
        long_bullets = [bullet for bullet in slide.bullets if len(bullet) > MAX_BULLET_CHARS]
        if long_bullets:
            raise ValueError(f"На слайде {index} есть слишком длинный пункт списка. Лимит: {MAX_BULLET_CHARS} символов.")


def _validate_pptx_package(path: Path, expected_slides: int) -> None:
    required_parts = {
        "[Content_Types].xml",
        "_rels/.rels",
        "docProps/core.xml",
        "docProps/app.xml",
        "ppt/presentation.xml",
        "ppt/_rels/presentation.xml.rels",
        "ppt/theme/theme1.xml",
        "ppt/slideMasters/slideMaster1.xml",
        "ppt/slideLayouts/slideLayout1.xml",
    }
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = sorted(required_parts - names)
        if missing:
            raise ValueError(f"PPTX создан неполным: отсутствуют части {', '.join(missing)}.")
        for index in range(1, expected_slides + 1):
            slide_part = f"ppt/slides/slide{index}.xml"
            rels_part = f"ppt/slides/_rels/slide{index}.xml.rels"
            if slide_part not in names or rels_part not in names:
                raise ValueError(f"PPTX создан неполным: отсутствует слайд {index}.")


def _write_minimal_pptx(
    target: Path,
    title: str,
    slides: list[SlideContent],
    theme: PresentationTheme,
) -> None:
    image_parts = _presentation_images(slides)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(slides), image_parts))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("docProps/core.xml", _core_props_xml(title))
        archive.writestr("docProps/app.xml", _app_props_xml(len(slides)))
        archive.writestr("ppt/presentation.xml", _presentation_xml(len(slides)))
        archive.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels_xml(len(slides)))
        archive.writestr("ppt/presProps.xml", _pres_props_xml())
        archive.writestr("ppt/viewProps.xml", _view_props_xml())
        archive.writestr("ppt/tableStyles.xml", _table_styles_xml())
        archive.writestr("ppt/theme/theme1.xml", _theme_xml(theme))
        archive.writestr("ppt/slideMasters/slideMaster1.xml", _slide_master_xml(theme))
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _slide_master_rels_xml())
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", _slide_layout_xml())
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _slide_layout_rels_xml())

        for index, slide in enumerate(slides, start=1):
            image_part = image_parts.get(index)
            archive.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(slide, theme, index, image_part))
            archive.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", _slide_rels_xml(image_part))
            if image_part is not None and slide.image_file is not None:
                archive.writestr(f"ppt/media/{image_part.media_name}", slide.image_file.read_bytes())


@dataclass(slots=True)
class ImagePart:
    relationship_id: str
    media_name: str
    extension: str


def _presentation_images(slides: list[SlideContent]) -> dict[int, ImagePart]:
    image_parts: dict[int, ImagePart] = {}
    image_number = 1
    for slide_index, slide in enumerate(slides, start=1):
        if slide.image_file is None:
            continue
        extension = slide.image_file.suffix.lower().lstrip(".")
        if extension == "jpg":
            extension = "jpeg"
        image_parts[slide_index] = ImagePart(
            relationship_id="rIdImage1",
            media_name=f"image{image_number}.{extension}",
            extension=extension,
        )
        image_number += 1
    return image_parts


def _content_types_xml(slide_count: int, image_parts: dict[int, ImagePart]) -> str:
    slide_overrides = "".join(
        f"<Override PartName=\"/ppt/slides/slide{index}.xml\" "
        "ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slide+xml\"/>"
        for index in range(1, slide_count + 1)
    )
    image_defaults = "".join(
        f"<Default Extension=\"{extension}\" ContentType=\"{_image_content_type(extension)}\"/>"
        for extension in sorted({part.extension for part in image_parts.values()})
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        f"{image_defaults}"
        "<Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/>"
        "<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/>"
        "<Override PartName=\"/ppt/presentation.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml\"/>"
        "<Override PartName=\"/ppt/presProps.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presProps+xml\"/>"
        "<Override PartName=\"/ppt/viewProps.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml\"/>"
        "<Override PartName=\"/ppt/tableStyles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml\"/>"
        "<Override PartName=\"/ppt/theme/theme1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.theme+xml\"/>"
        "<Override PartName=\"/ppt/slideMasters/slideMaster1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml\"/>"
        "<Override PartName=\"/ppt/slideLayouts/slideLayout1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml\"/>"
        f"{slide_overrides}"
        "</Types>"
    )


def _image_content_type(extension: str) -> str:
    if extension == "png":
        return "image/png"
    if extension == "gif":
        return "image/gif"
    return "image/jpeg"


def _root_rels_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"ppt/presentation.xml\"/>"
        "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\" Target=\"docProps/core.xml\"/>"
        "<Relationship Id=\"rId3\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties\" Target=\"docProps/app.xml\"/>"
        "</Relationships>"
    )


def _presentation_xml(slide_count: int) -> str:
    slide_ids = "".join(
        f"<p:sldId id=\"{255 + index}\" r:id=\"rId{index}\"/>" for index in range(1, slide_count + 1)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:presentation xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">"
        "<p:sldMasterIdLst><p:sldMasterId id=\"2147483648\" r:id=\"rIdMaster\"/></p:sldMasterIdLst>"
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
        f"<p:sldSz cx=\"{PRESENTATION_CX}\" cy=\"{PRESENTATION_CY}\" type=\"screen16x9\"/>"
        "<p:notesSz cx=\"6858000\" cy=\"9144000\"/>"
        "</p:presentation>"
    )


def _presentation_rels_xml(slide_count: int) -> str:
    slide_rels = "".join(
        f"<Relationship Id=\"rId{index}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide\" Target=\"slides/slide{index}.xml\"/>"
        for index in range(1, slide_count + 1)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        f"{slide_rels}"
        "<Relationship Id=\"rIdMaster\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster\" Target=\"slideMasters/slideMaster1.xml\"/>"
        "<Relationship Id=\"rIdPresProps\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps\" Target=\"presProps.xml\"/>"
        "<Relationship Id=\"rIdViewProps\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps\" Target=\"viewProps.xml\"/>"
        "<Relationship Id=\"rIdTableStyles\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles\" Target=\"tableStyles.xml\"/>"
        "<Relationship Id=\"rIdTheme\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme\" Target=\"theme/theme1.xml\"/>"
        "</Relationships>"
    )


def _slide_xml(slide: SlideContent, theme: PresentationTheme, index: int, image_part: ImagePart | None) -> str:
    shapes = [
        _background_shape(theme),
        *_layout_shapes(slide, theme, index, image_part),
    ]
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:sld xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">"
        "<p:cSld><p:spTree>"
        "<p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
        "<a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr>"
        f"{''.join(shapes)}"
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _layout_shapes(slide: SlideContent, theme: PresentationTheme, index: int, image_part: ImagePart | None) -> list[str]:
    if slide.layout == "title":
        return [
            _rect_shape(13, "Title accent", 720000, 1280000, 1800000, 85000, theme.accent, ""),
            _text_shape(10, "Title", 720000, 1450000, 10750000, 1100000, [slide.title], theme, 4200, True),
            _text_shape(11, "Subtitle", 900000, 2680000, 10300000, 800000, [slide.subtitle], theme, 2200, False),
            _footer_shape(index, theme),
        ]

    if slide.layout == "section":
        return [
            _accent_bar(theme),
            _text_shape(10, "Section", 950000, 1850000, 10000000, 1000000, [slide.title], theme, 3600, True),
            _text_shape(11, "Body", 950000, 3100000, 9800000, 1200000, [slide.body or slide.subtitle], theme, 2000, False),
            _footer_shape(index, theme),
        ]

    if slide.layout == "agenda":
        agenda_items = slide.bullets or ([slide.body] if slide.body else [])
        numbered_items = [f"{item_index}. {item}" for item_index, item in enumerate(agenda_items, start=1)]
        return [
            _text_shape(10, "Agenda title", 650000, 500000, 10900000, 800000, [slide.title or "Agenda"], theme, 3200, True),
            _rect_shape(12, "Agenda panel", 900000, 1450000, 10200000, 4100000, theme.surface, ""),
            _text_shape(11, "Agenda items", 1200000, 1650000, 9600000, 3700000, numbered_items, theme, 2100, False),
            _footer_shape(index, theme),
        ]

    if slide.layout == "closing":
        return [
            _rect_shape(12, "Closing accent", 900000, 1760000, 1800000, 85000, theme.accent, ""),
            _text_shape(10, "Closing", 900000, 1900000, 10400000, 1100000, [slide.title], theme, 3800, True),
            _text_shape(11, "Summary", 1100000, 3180000, 9800000, 1300000, slide.bullets or [slide.body, slide.subtitle], theme, 2100, False),
            _footer_shape(index, theme),
        ]

    if slide.layout == "image":
        visual_shape = (
            _picture_shape(11, "Image", 650000, 1450000, 10900000, 3500000, image_part)
            if image_part is not None
            else _placeholder_shape(11, theme)
        )
        return [
            _text_shape(10, "Title", 650000, 500000, 10900000, 700000, [slide.title], theme, 3000, True),
            visual_shape,
            _text_shape(12, "Caption", 650000, 5200000, 10900000, 700000, [slide.caption or slide.body], theme, 1500, False),
            _source_shape(slide.source, theme),
            _footer_shape(index, theme),
        ]

    if slide.layout == "quote":
        quote_lines = [slide.body or slide.subtitle or (slide.bullets[0] if slide.bullets else "")]
        caption = slide.caption or slide.source
        return [
            _rect_shape(12, "Quote panel", 900000, 1350000, 10400000, 3500000, theme.surface, ""),
            _text_shape(10, "Quote", 1300000, 1900000, 9500000, 1400000, quote_lines, theme, 2900, True),
            _text_shape(11, "Quote caption", 1300000, 3600000, 9500000, 500000, [caption], theme, 1500, False),
            _footer_shape(index, theme),
        ]

    if slide.layout == "comparison":
        items = slide.bullets or _split_comparison_items(slide.body)
        left_items = items[: max(1, len(items) // 2)]
        right_items = items[max(1, len(items) // 2) :]
        return [
            _text_shape(10, "Title", 650000, 450000, 10900000, 700000, [slide.title], theme, 3000, True),
            _rect_shape(12, "Left comparison", 800000, 1500000, 5000000, 3600000, theme.surface, ""),
            _rect_shape(13, "Right comparison", 6400000, 1500000, 5000000, 3600000, theme.surface, ""),
            _text_shape(14, "Left items", 1100000, 1800000, 4400000, 3000000, left_items, theme, 1750, False, bullets=True),
            _text_shape(15, "Right items", 6700000, 1800000, 4400000, 3000000, right_items, theme, 1750, False, bullets=True),
            _footer_shape(index, theme),
        ]

    if slide.layout == "metrics":
        items = (slide.bullets or _split_comparison_items(slide.body))[:3]
        while len(items) < 3:
            items.append("Показатель")
        return [
            _text_shape(10, "Title", 650000, 450000, 10900000, 700000, [slide.title], theme, 3000, True),
            *_metric_cards(items, theme),
            _text_shape(20, "Caption", 900000, 5050000, 10300000, 500000, [slide.caption or slide.subtitle], theme, 1350, False),
            _footer_shape(index, theme),
        ]

    body_lines = slide.bullets or ([slide.body] if slide.body else [])
    if slide.subtitle:
        body_lines = [slide.subtitle, *body_lines]
    return [
        _text_shape(10, "Title", 650000, 450000, 10900000, 700000, [slide.title], theme, 3000, True),
        _rect_shape(12, "Content panel", 760000, 1420000, 10650000, 4200000, theme.surface, ""),
        _text_shape(11, "Content", 900000, 1550000, 10100000, 3900000, body_lines, theme, 1900, False, bullets=bool(slide.bullets)),
        _source_shape(slide.source, theme),
        _footer_shape(index, theme),
    ]


def _background_shape(theme: PresentationTheme) -> str:
    return _rect_shape(2, "Background", 0, 0, PRESENTATION_CX, PRESENTATION_CY, theme.background, "")


def _accent_bar(theme: PresentationTheme) -> str:
    return _rect_shape(12, "Accent", 0, 0, 170000, PRESENTATION_CY, theme.accent, "")


def _placeholder_shape(shape_id: int, theme: PresentationTheme) -> str:
    return _rect_shape(
        shape_id,
        "Image placeholder",
        650000,
        1450000,
        10900000,
        3500000,
        theme.surface,
        "Изображение будет добавлено на следующем этапе",
        theme,
    )


def _picture_shape(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    image_part: ImagePart,
) -> str:
    return (
        f"<p:pic><p:nvPicPr><p:cNvPr id=\"{shape_id}\" name=\"{escape(name)}\"/>"
        "<p:cNvPicPr><a:picLocks noChangeAspect=\"1\"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>"
        f"<p:blipFill><a:blip r:embed=\"{image_part.relationship_id}\"/>"
        "<a:stretch><a:fillRect/></a:stretch></p:blipFill>"
        f"<p:spPr><a:xfrm><a:off x=\"{x}\" y=\"{y}\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm>"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic>"
    )


def _source_shape(source: str, theme: PresentationTheme) -> str:
    if not source:
        return ""
    return _text_shape(90, "Source", 650000, 5850000, 10900000, 330000, [f"Источник: {source}"], theme, 950, False)


def _footer_shape(index: int, theme: PresentationTheme) -> str:
    return _text_shape(99, "Slide number", 11100000, 6250000, 600000, 250000, [str(index)], theme, 900, False)


def _metric_cards(items: list[str], theme: PresentationTheme) -> list[str]:
    shapes: list[str] = []
    positions = [900000, 4300000, 7700000]
    for offset, item in enumerate(items):
        shape_id = 12 + offset * 2
        value, label = _split_metric_text(item)
        x = positions[offset]
        shapes.append(_rect_shape(shape_id, "Metric card", x, 1700000, 3000000, 2700000, theme.surface, ""))
        shapes.append(_text_shape(shape_id + 1, "Metric text", x + 250000, 2050000, 2500000, 1800000, [value, label], theme, 2400, True))
    return shapes


def _split_metric_text(text: str) -> tuple[str, str]:
    cleaned = _normalize_text(text)
    match = re.match(r"^([^:|—-]{1,32})\s*[:|—-]\s*(.+)$", cleaned)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    parts = cleaned.split(maxsplit=1)
    if len(parts) == 2 and any(char.isdigit() for char in parts[0]):
        return parts[0], parts[1]
    return "Факт", cleaned


def _split_comparison_items(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?:\r?\n|;\s*|\s+\|\s+)", text) if part.strip()]


def _rect_shape(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    color: str,
    text: str,
    theme: PresentationTheme | None = None,
) -> str:
    text_body = ""
    if text and theme is not None:
        text_body = _text_body([text], theme, 1700, False, False)
    else:
        text_body = "<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody>"
    return (
        f"<p:sp><p:nvSpPr><p:cNvPr id=\"{shape_id}\" name=\"{escape(name)}\"/>"
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f"<p:spPr><a:xfrm><a:off x=\"{x}\" y=\"{y}\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm>"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom>"
        f"<a:solidFill><a:srgbClr val=\"{color}\"/></a:solidFill>"
        "<a:ln><a:noFill/></a:ln></p:spPr>"
        f"{text_body}</p:sp>"
    )


def _text_shape(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    lines: list[str],
    theme: PresentationTheme,
    font_size: int,
    bold: bool,
    bullets: bool = False,
) -> str:
    visible_lines = [line for line in lines if line]
    if not visible_lines:
        visible_lines = [""]
    fitted_font_size = _fit_font_size(visible_lines, font_size)
    return (
        f"<p:sp><p:nvSpPr><p:cNvPr id=\"{shape_id}\" name=\"{escape(name)}\"/>"
        "<p:cNvSpPr txBox=\"1\"/><p:nvPr/></p:nvSpPr>"
        f"<p:spPr><a:xfrm><a:off x=\"{x}\" y=\"{y}\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm>"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>"
        f"{_text_body(visible_lines, theme, fitted_font_size, bold, bullets)}</p:sp>"
    )


def _text_body(lines: list[str], theme: PresentationTheme, font_size: int, bold: bool, bullets: bool) -> str:
    paragraphs = "".join(
        _text_paragraph(line, theme, font_size, bold, bullets=bullets and index > 0)
        for index, line in enumerate(lines)
    )
    return f"<p:txBody><a:bodyPr wrap=\"square\"/><a:lstStyle/>{paragraphs}</p:txBody>"


def _fit_font_size(lines: list[str], font_size: int) -> int:
    longest = max((len(line) for line in lines), default=0)
    line_count = len(lines)
    if longest > 120 or line_count > 8:
        return max(1050, int(font_size * 0.72))
    if longest > 85 or line_count > 6:
        return max(1150, int(font_size * 0.82))
    if longest > 55 or line_count > 4:
        return max(1250, int(font_size * 0.9))
    return font_size


def _text_paragraph(text: str, theme: PresentationTheme, font_size: int, bold: bool, bullets: bool) -> str:
    bullet_xml = "<a:buChar char=\"•\"/>" if bullets else "<a:buNone/>"
    run_props = (
        f"<a:rPr lang=\"ru-RU\" sz=\"{font_size}\" b=\"{1 if bold else 0}\">"
        f"<a:solidFill><a:srgbClr val=\"{theme.foreground}\"/></a:solidFill>"
        f"<a:latin typeface=\"{escape(theme.font_minor)}\"/></a:rPr>"
    )
    return (
        f"<a:p><a:pPr marL=\"{280000 if bullets else 0}\" indent=\"{ -180000 if bullets else 0}\">{bullet_xml}</a:pPr>"
        f"<a:r>{run_props}<a:t>{escape(text)}</a:t></a:r><a:endParaRPr lang=\"ru-RU\"/></a:p>"
    )


def _slide_rels_xml(image_part: ImagePart | None = None) -> str:
    image_relationship = ""
    if image_part is not None:
        image_relationship = (
            f"<Relationship Id=\"{image_part.relationship_id}\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image\" "
            f"Target=\"../media/{image_part.media_name}\"/>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout\" Target=\"../slideLayouts/slideLayout1.xml\"/>"
        f"{image_relationship}"
        "</Relationships>"
    )


def _slide_master_xml(theme: PresentationTheme) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:sldMaster xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">"
        "<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/><a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr>"
        f"{_background_shape(theme)}</p:spTree></p:cSld>"
        "<p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/>"
        "<p:sldLayoutIdLst><p:sldLayoutId id=\"2147483649\" r:id=\"rId1\"/></p:sldLayoutIdLst>"
        "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>"
        "</p:sldMaster>"
    )


def _slide_master_rels_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout\" Target=\"../slideLayouts/slideLayout1.xml\"/>"
        "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme\" Target=\"../theme/theme1.xml\"/>"
        "</Relationships>"
    )


def _slide_layout_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:sldLayout xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" type=\"blank\" preserve=\"1\">"
        "<p:cSld name=\"Blank\"><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/><a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr>"
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )


def _slide_layout_rels_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster\" Target=\"../slideMasters/slideMaster1.xml\"/>"
        "</Relationships>"
    )


def _theme_xml(theme: PresentationTheme) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<a:theme xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" name=\"Avelin\">"
        "<a:themeElements><a:clrScheme name=\"Avelin\">"
        f"<a:dk1><a:srgbClr val=\"{theme.foreground}\"/></a:dk1>"
        f"<a:lt1><a:srgbClr val=\"{theme.background}\"/></a:lt1>"
        "<a:dk2><a:srgbClr val=\"1F2937\"/></a:dk2><a:lt2><a:srgbClr val=\"F8FAFC\"/></a:lt2>"
        f"<a:accent1><a:srgbClr val=\"{theme.accent}\"/></a:accent1>"
        "<a:accent2><a:srgbClr val=\"16A34A\"/></a:accent2><a:accent3><a:srgbClr val=\"EAB308\"/></a:accent3>"
        "<a:accent4><a:srgbClr val=\"E11D48\"/></a:accent4><a:accent5><a:srgbClr val=\"7C3AED\"/></a:accent5>"
        "<a:accent6><a:srgbClr val=\"0EA5E9\"/></a:accent6><a:hlink><a:srgbClr val=\"2563EB\"/></a:hlink>"
        "<a:folHlink><a:srgbClr val=\"7C3AED\"/></a:folHlink></a:clrScheme>"
        f"<a:fontScheme name=\"Avelin\"><a:majorFont><a:latin typeface=\"{escape(theme.font_major)}\"/></a:majorFont>"
        f"<a:minorFont><a:latin typeface=\"{escape(theme.font_minor)}\"/></a:minorFont></a:fontScheme>"
        "<a:fmtScheme name=\"Avelin\"><a:fillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:fillStyleLst>"
        "<a:lnStyleLst><a:ln w=\"6350\"><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln></a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        "<a:bgFillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>"
        "</a:themeElements></a:theme>"
    )


def _core_props_xml(title: str) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<cp:coreProperties xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\" "
        "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" "
        "xmlns:dcterms=\"http://purl.org/dc/terms/\" "
        "xmlns:dcmitype=\"http://purl.org/dc/dcmitype/\" "
        "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">"
        f"<dc:title>{escape(title)}</dc:title><dc:creator>Avelin</dc:creator>"
        "</cp:coreProperties>"
    )


def _app_props_xml(slide_count: int) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\" "
        "xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\">"
        "<Application>Avelin</Application>"
        f"<Slides>{slide_count}</Slides>"
        "</Properties>"
    )


def _pres_props_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:presentationPr xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>"
    )


def _view_props_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<p:viewPr xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>"
    )


def _table_styles_xml() -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<a:tblStyleLst xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" def=\"{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}\"/>"
    )
