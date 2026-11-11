"""Bounded extraction from a text PDF without invoking a viewer or OCR."""
from pathlib import Path


def read_pdf_text(path: Path, start_page: int, end_page: int, max_chars: int, start_character: int = 1) -> dict:
    if path.stat().st_size > 40 * 1024 * 1024:
        raise ValueError('PDF превышает 40 МБ.')
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError('Для чтения PDF требуется установленная зависимость pypdf.') from exc

    try:
        with path.open('rb') as handle:
            reader = PdfReader(handle)
            count = len(reader.pages)
            parts = []
            used = 0
            last = min(start_page - 1, count)
            truncated = False
            next_page, next_character = None, None
            for number in range(start_page, min(end_page, count, start_page + 29) + 1):
                page = reader.pages[number - 1]
                stream = page.get_contents()
                if stream and len(stream.get_data()) > 8 * 1024 * 1024:
                    raise ValueError('Страница PDF слишком большая для извлечения текста.')
                text = page.extract_text() or ''
                header = f'[Страница {number}]\n'
                offset = start_character - 1 if number == start_page else 0
                page_text = (text if text.strip() else 'Текстовый слой отсутствует. Требуется OCR.')[offset:]
                remaining = max_chars - used - len(header)
                if parts and len(page_text) > remaining:
                    break
                take = max(1, remaining)
                part = header + page_text[:take]
                parts.append(part)
                used += len(part) + 2
                last = number
                truncated = len(page_text) > take
                if truncated:
                    next_page, next_character = number, offset + take + 1
                    break
            if next_page is None and last < count:
                next_page, next_character = last + 1, 1
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не удалось прочитать PDF: проверь целостность файла и наличие шифрования.') from exc

    return {
        'start_page': start_page,
        'end_page': last,
        'total_pages': count,
        'text': '\n\n'.join(parts),
        'last_page_truncated': truncated,
        'truncated': next_page is not None,
        'next_start_page': next_page,
        'next_start_character': next_character,
    }
