"""Small presentation/intent contracts; never grant file access or tool permission."""
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
import re


_LOCAL_FILE_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_FILE_SUFFIX = re.compile(r"\.(?:docx?|docm|xlsx?|xlsm|pptx?|pdf|png|jpe?g|webp|zip|csv|txt|bas|json|md)$", re.I)


def wants_docx_creation(text: str) -> bool:
    if re.search(r"без\s+(?:word|ворд|docx|файл)|(?:просто|только)\s+текстом|(?:не|don't|do not)\s+созда", text, re.I):
        return False
    if re.search(r"(?:word|docx).{0,40}(?:в|как|to)\s+pdf", text, re.I):
        return False
    return bool(re.search(r"(?:созда\w*|напиш\w*|запиш\w*|сохран\w*|сдела\w*|оформ\w*|выпиш\w*|create|write|save|make|export)[^.!?\n]{0,300}(?:\bword\b|ворд|\.docx\b)", text, re.I))


def wants_resend(text: str) -> bool:
    if re.search(r"без\s+(?:word|ворд|docx|файл)|(?:просто|только)\s+текстом", text, re.I):
        return False
    if wants_docx_creation(text):
        return False
    return bool(re.search(r"(?<!\w)(?:скинь|пришли|перешли|отправь|прикрепи|верни|send|attach)\b[^!?\n]{0,100}(?:файл|документ|\bword\b|ворд|\bfile\b|\bdocument\b|\.(?:docx|xlsx|pdf|zip|png|jpe?g)\b)|не\s+могу\s+скача|(?:скинь|пришли|отправь|дай)[^!?\n]{0,40}ссылк[^!?\n]{0,35}скач", text, re.I))


def needs_precise_image(text: str) -> bool:
    """Exact identification/OCR needs a precise pass; ordinary descriptions do not."""
    return bool(re.search(r"(?:определ\w*|идентифиц\w*|распозна\w*|прочита\w*|расшифр\w*|найд\w*|найти|find|identify|identification|read).{0,95}(?:модель|маркиров|артикул|референс|серийн|текст|надпис|номер|\bid\b|скин|персонаж|model|serial|marking|text|skin|character)|(?:какой|какая|какое|what|which).{0,65}(?:\bid\b|скин|модель|персонаж|model|skin|character)", text, re.I))


def local_file_target(value: str) -> str | None:
    href = value.strip().strip('<>')
    parsed = urlsplit(href)
    if parsed.scheme and parsed.scheme != 'sandbox' or parsed.netloc or href.startswith('/api/'):
        return None
    path = unquote(parsed.path).replace('\\', '/')
    if parsed.scheme == 'sandbox':
        path = path.removeprefix('/mnt/data/').lstrip('/')
    elif path.startswith('/mnt/data/'):
        path = path.removeprefix('/mnt/data/')
    return path if _FILE_SUFFIX.search(path) else None


def local_file_targets(text: str) -> list[str]:
    return [target for match in _LOCAL_FILE_LINK.finditer(text) if (target := local_file_target(match.group(2)))]


def requested_file_names(text: str) -> set[str]:
    return {name.casefold() for name in re.findall(r"(?<!\w)([\w.-]+\.(?:docx?|docm|xlsx?|xlsm|pptx?|pdf|png|jpe?g|webp|zip|csv|txt|bas|json|md))\b", text, re.I)}


def normalize_artifact_links(text: str, artifacts: list[dict]) -> str:
    """Artifact cards own downloads; remove provider-invented local URL schemes."""
    paths = {str(item.get('path', '')).replace('\\', '/') for item in artifacts}
    names = {str(item.get('name', PurePosixPath(str(item.get('path', ''))).name)).casefold() for item in artifacts}
    def replace(match):
        target = local_file_target(match.group(2))
        if target is None:
            return match.group(0)
        label = match.group(1) or PurePosixPath(target).name
        return f"**{label}**" if target in paths or PurePosixPath(target).name.casefold() in names else f"{label} (ссылка на файл не подтверждена)"
    return _LOCAL_FILE_LINK.sub(replace, text)
