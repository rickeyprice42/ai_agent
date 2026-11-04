"""Bounded attachment validation and text extraction; never executes uploads."""
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile
import json
import re
from itertools import islice
from src.ai_agent.image_processing import IMAGE_FORMATS, decode_image

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 24000
OFFICE = {'.docx': 'word/document.xml', '.docm': 'word/document.xml', '.dotm': 'word/document.xml',
          '.xlsx': 'xl/workbook.xml', '.xlsm': 'xl/workbook.xml', '.xltm': 'xl/workbook.xml',
          '.pptx': 'ppt/presentation.xml', '.pptm': 'ppt/presentation.xml', '.potm': 'ppt/presentation.xml'}
IMAGES = set(IMAGE_FORMATS)
TEXT = {'.txt', '.md', '.csv', '.json', '.bas', '.cls', '.frm', '.vba'}
ALLOWED = TEXT | set(OFFICE) | IMAGES | {'.pdf'}

def validate_attachment(name: str, content: bytes) -> str:
    if (not name or len(name)>180 or re.search(r'[<>:"/\\|?*]',name) or name!=name.strip().rstrip('.')
        or any(ord(c) < 32 for c in name) or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',name)):
        raise ValueError('Недопустимое имя вложения.')
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError('Этот формат вложения пока не поддерживается.')
    if not content or len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError('Вложение пустое или превышает 10 МБ.')
    if suffix in IMAGES:
        decode_image(content, suffix)
    elif suffix in OFFICE:
        try:
            with ZipFile(BytesIO(content)) as package:
                if sum(i.file_size for i in package.infolist()) > 40 * 1024 * 1024 or len(package.infolist()) > 5000:
                    raise ValueError('Слишком большой распакованный документ.')
                required = OFFICE[suffix]
                if required not in package.namelist():
                    raise ValueError('Неверная структура документа.')
        except BadZipFile as exc:
            raise ValueError('Документ повреждён.') from exc
    elif suffix == '.pdf' and not content.startswith(b'%PDF-'):
        raise ValueError('Неверный PDF.')
    return suffix

def _bounded(text: str) -> str:
    return text if len(text)<=MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS]+'\n[Вложение сокращено; показан только начальный фрагмент.]'

def extract_text(path: Path) -> str:
    try:
        return _extract_text(path)
    except (ValueError, OSError, BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError('Не удалось прочитать вложение: документ повреждён, зашифрован или имеет неподдерживаемую структуру.') from exc
    except Exception as exc:
        # Third-party parsers can raise format-specific exceptions; never expose a traceback.
        raise ValueError('Не удалось прочитать вложение. Проверь формат и целостность файла.') from exc

def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGES:
        return 'Изображение прикреплено пользователем для текущего запроса. Используй analyze_image с точным путём вложения для анализа.'
    if suffix in TEXT:
        try:
            return _bounded(path.read_text(encoding='utf-8-sig'))
        except UnicodeDecodeError as exc:
            raise ValueError('Текстовое вложение должно быть в UTF-8.') from exc
    if suffix == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            parts=[]
            for page in islice(reader.pages,30):
                stream=page.get_contents()
                if stream and len(stream.get_data()) > 8*1024*1024:
                    raise ValueError('Страница PDF слишком большая для извлечения текста.')
                parts.append(page.extract_text() or '')
                if sum(len(p) for p in parts)>MAX_TEXT_CHARS:break
            text='\n'.join(parts)
            if len(reader.pages)>30:text+='\n[Показаны только первые 30 страниц PDF.]'
            return _bounded(text) or 'PDF не содержит извлекаемого текста. OCR пока недоступен.'
        except ImportError:
            return 'PDF загружен, но извлечение текста недоступно: установи зависимости проекта (pypdf). Не делай выводов о содержимом.'
    text = []
    with ZipFile(path) as package:
        if suffix in {'.docx', '.docm', '.dotm'}:
            entries = ['word/document.xml']
        elif suffix in {'.pptx', '.pptm', '.potm'}:
            entries = sorted((n for n in package.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),key=lambda n:int(re.search(r'(\d+)\.xml',n).group(1)))[:100]
        else:
            entries = ['xl/sharedStrings.xml'] if 'xl/sharedStrings.xml' in package.namelist() else []
            entries += sorted((n for n in package.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)),key=lambda n:int(re.search(r'(\d+)\.xml',n).group(1)))[:30]
        for name in entries:
            tree = ElementTree.fromstring(package.read(name))
            if suffix in {'.xlsx', '.xlsm', '.xltm'}:
                # Preserve row/cell coordinates and formulas rather than pretending recalculated values.
                text.append(name + '\n' + ElementTree.tostring(tree,encoding='unicode'))
            else:
                text.extend(node.text for node in tree.iter() if node.tag.endswith('}t') and node.text)
            if sum(len(t) for t in text) > MAX_TEXT_CHARS:
                break
    return _bounded('\n'.join(text))
