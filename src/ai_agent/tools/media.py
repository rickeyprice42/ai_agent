"""Deterministic local image tools: no model or cloud upload is needed."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from ai_agent.image_processing import decode_image, jpeg_bytes, MAX_IMAGE_PIXELS
from ai_agent.tools.base import Tool, ToolPermission
from ai_agent.tools.files import FileSandbox


class ImageSandbox:
    def __init__(self, files: FileSandbox):
        self.files = files

    def inspect(self, path: str) -> dict:
        target = self.files.resolve_download_path(path)
        if target.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('Изображение превышает 10 МБ.')
        content = target.read_bytes()
        image = decode_image(content, target.suffix)
        return {'path': path, 'width': image.width, 'height': image.height,
                'mode': image.mode, 'frames': image.info.get('source_frames', 1),
                'sha256': sha256(content).hexdigest()}

    def transform(self, path: str, output_path: str = '', width: int | None = None,
                  height: int | None = None, format: str = '', fit: str = 'contain',
                  quality: int = 90) -> dict:
        source = self.files.resolve_download_path(path)
        if source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('Изображение превышает 10 МБ.')
        image = decode_image(source.read_bytes(), source.suffix)
        frames = image.info.get('source_frames', 1)
        if fit not in {'contain', 'cover', 'stretch'}:
            raise ValueError('fit должен быть contain, cover или stretch.')
        if width is not None and (type(width) is not int or not 1 <= width <= 16000):
            raise ValueError('Ширина должна быть от 1 до 16000 пикселей.')
        if height is not None and (type(height) is not int or not 1 <= height <= 16000):
            raise ValueError('Высота должна быть от 1 до 16000 пикселей.')
        if width is not None or height is not None:
            w = width or max(1, round(image.width * height / image.height))
            h = height or max(1, round(image.height * width / image.width))
            if w * h > MAX_IMAGE_PIXELS:
                raise ValueError('Результат превышает 40 мегапикселей.')
            if fit == 'cover':
                image = ImageOps.fit(image, (w, h), method=Image.Resampling.LANCZOS)
            elif fit == 'stretch':
                image = image.resize((w, h), Image.Resampling.LANCZOS)
            else:
                image = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
        formats = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP',
                   'avif': 'AVIF', 'bmp': 'BMP', 'tif': 'TIFF', 'tiff': 'TIFF',
                   'gif': 'GIF', 'heic': 'HEIF', 'heif': 'HEIF'}
        extension = (format or Path(output_path).suffix.lstrip('.') or 'png').lower()
        if extension not in formats:
            raise ValueError('Неподдерживаемый формат результата.')
        relative = output_path or f'outputs/{source.stem}-{uuid4().hex[:8]}.{extension}'
        if Path(relative).suffix.lower() != '.' + extension:
            raise ValueError('Расширение output_path должно соответствовать format.')
        target = self.files._resolve_safe_path(relative)
        if not target.is_relative_to(self.files.workspace_dir / 'outputs') or target == source:
            raise ValueError('Результат должен быть новым файлом внутри outputs/.')
        if target.exists():
            raise ValueError('Файл результата уже существует; выбери новое имя.')
        if not 1 <= quality <= 100:
            raise ValueError('quality должен быть от 1 до 100.')
        buffer = BytesIO()
        output_format = formats[extension]
        if image.mode == 'P' and 'transparency' in image.info:
            image = image.convert('RGBA')
        if output_format == 'JPEG':
            data = jpeg_bytes(image, size=max(image.size), quality=quality)
        else:
            if output_format in {'BMP', 'HEIF'}:
                image = image.convert('RGB')
            # Strip EXIF/GPS and other source metadata from public outputs.
            image.info.clear()
            options = {'quality': quality} if output_format in {'WEBP', 'AVIF', 'HEIF'} else {}
            image.save(buffer, output_format, **options)
            data = buffer.getvalue()
        if len(data) > 40 * 1024 * 1024:
            raise ValueError('Результат превышает 40 МБ.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(data)
        return {'path': target.relative_to(self.files.workspace_dir).as_posix(),
                'width': image.width, 'height': image.height, 'format': output_format,
                'size_bytes': len(data), 'source_preserved': True,
                'note': 'Обработан первый кадр.' if frames > 1 else ''}


def register_image_tools(registry, files):
    images = ImageSandbox(files)
    registry.register(Tool(name='inspect_image', description='Читает размеры, формат и SHA-256 изображения локально, без отправки модели.',
        schema={'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']},
        output_schema={'type': 'object'}, permissions=(ToolPermission('workspace.read', 'read'),), capabilities=('images.inspect',),
        handler=lambda args: images.inspect(args['path'])))
    registry.register(Tool(name='transform_image', description='Локально изменяет размер/формат фото, включая HEIC. Создаёт новый файл в outputs/, сохраняет оригинал. contain сохраняет пропорции; cover кадрирует; stretch растягивает. Для GIF/TIFF обрабатывает первый кадр.',
        schema={'type': 'object', 'properties': {'path': {'type': 'string'}, 'output_path': {'type': 'string'},
            'width': {'type': 'integer', 'minimum': 1, 'maximum': 16000}, 'height': {'type': 'integer', 'minimum': 1, 'maximum': 16000},
            'format': {'type': 'string', 'enum': ['png', 'jpg', 'jpeg', 'webp', 'avif', 'heic', 'heif', 'gif', 'bmp', 'tif', 'tiff']},
            'fit': {'type': 'string', 'enum': ['contain', 'cover', 'stretch']}, 'quality': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'required': ['path']},
        output_schema={'type': 'object'}, permissions=(ToolPermission('workspace.write', 'write'),), capabilities=('images.transform',),
        handler=lambda args: images.transform(**args)))
