"""Authenticated image previews and normalized per-account avatars."""
from datetime import datetime, timezone
from io import BytesIO
import os
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from PIL import Image, ImageOps

from backend.routes import current_user
from backend import routes
from backend.attachments import MAX_ATTACHMENT_BYTES
from ai_agent.image_processing import decode_image, jpeg_bytes, preview_bytes

router = APIRouter(prefix='/api')


async def _body(request: Request) -> bytes:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, 'Изображение превышает 10 МБ.')
        content.extend(chunk)
    return bytes(content)


def _decode(content, name):
    from pathlib import Path
    try:
        return decode_image(content, Path(unquote(name)).suffix)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/workspace/preview')
def preview(path: str = Query(..., max_length=500), size: int = Query(1024, ge=128, le=2048), user=Depends(current_user)):
    try:
        target = routes.agent_service.workspace_file_path(user['id'], path)
        data = preview_bytes(target, size)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


def _avatar_path(user_id):
    return routes.agent_service.user_workspace(user_id) / '.profile' / 'avatar.png'


@router.get('/profile/avatar')
def avatar(user=Depends(current_user)):
    path = _avatar_path(user['id'])
    if not path.is_file() or path.resolve() != path:
        raise HTTPException(404, 'Аватар пока не установлен.')
    return Response(path.read_bytes(), media_type='image/png', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/profile/avatar/preview')
async def avatar_preview(request: Request, x_filename: str = Header(default=''), user=Depends(current_user)):
    image = _decode(await _body(request), x_filename)
    return Response(jpeg_bytes(image, size=2048), media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})


@router.put('/profile/avatar')
async def save_avatar(request: Request, x_filename: str = Header(default=''), user=Depends(current_user)):
    image = _decode(await _body(request), x_filename)
    image = ImageOps.fit(image.convert('RGBA'), (256, 256), method=Image.Resampling.LANCZOS)
    image.info.clear()
    output = BytesIO()
    image.save(output, 'PNG')
    path = _avatar_path(user['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise HTTPException(400, 'Недопустимая папка профиля.')
    temporary = path.with_name(uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(output.getvalue())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'updated_at': datetime.now(timezone.utc).isoformat(), 'mime_type': 'image/png', 'width': 256, 'height': 256}


@router.delete('/profile/avatar')
def delete_avatar(user=Depends(current_user)):
    path = _avatar_path(user['id'])
    if path.resolve() != path:
        raise HTTPException(400, 'Недопустимый путь профиля.')
    path.unlink(missing_ok=True)
    return {'deleted': True}
