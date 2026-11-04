"""Local image decoding shared by uploads, previews, vision and image tools."""
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, ImageOps

IMAGE_FORMATS = {'.jpg': {'JPEG'}, '.jpeg': {'JPEG'}, '.png': {'PNG'},
                 '.webp': {'WEBP'}, '.gif': {'GIF'}, '.bmp': {'BMP'},
                 '.tif': {'TIFF'}, '.tiff': {'TIFF'}, '.avif': {'AVIF'},
                 '.heic': {'HEIF'}, '.heif': {'HEIF'}}
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 10 * 1024 * 1024

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # A missing optional codec produces a useful error at decode time.


def decode_image(content: bytes, suffix: str = '') -> Image.Image:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('Изображение пустое или превышает 10 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if suffix and source.format not in IMAGE_FORMATS.get(suffix.lower(), set()):
                    raise ValueError('Формат изображения не соответствует расширению.')
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError('Изображение превышает 40 мегапикселей.')
                # Animated and multi-page files are explicitly treated as their first frame.
                source.seek(0)
                image = ImageOps.exif_transpose(source).copy()
                image.info['source_frames'] = getattr(source, 'n_frames', 1)
                image.load()
                return image
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError('Изображение слишком большое для безопасной обработки.') from exc
    except ValueError:
        raise
    except Exception as exc:
        hint = ' Установи pillow-heif из requirements.txt.' if suffix.lower() in {'.heic', '.heif'} else ''
        raise ValueError('Изображение повреждено или кодек недоступен.' + hint) from exc


def jpeg_bytes(image: Image.Image, *, size: int = 2048, quality: int = 90) -> bytes:
    result = image.copy()
    result.thumbnail((size, size), Image.Resampling.LANCZOS)
    background = Image.new('RGB', result.size, 'white')
    if result.mode in {'RGBA', 'LA'} or 'transparency' in result.info:
        rgba = result.convert('RGBA')
        background.paste(rgba, mask=rgba.getchannel('A'))
    else:
        background.paste(result.convert('RGB'))
    output = BytesIO()
    background.save(output, 'JPEG', quality=quality, optimize=True)
    return output.getvalue()


def preview_bytes(path: Path, size: int = 1024) -> bytes:
    if path.suffix.lower() not in IMAGE_FORMATS:
        raise ValueError('Предпросмотр доступен только для изображений.')
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('Изображение превышает 10 МБ.')
    return jpeg_bytes(decode_image(path.read_bytes(), path.suffix), size=max(128, min(size, 2048)))
