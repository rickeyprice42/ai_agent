"""Identify the code loaded by a backend, independently of a healthy TCP port."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
API_CONTRACT = 2


def source_signature(root: Path = ROOT) -> str:
    digest = sha256()
    files = list((root / 'backend').glob('*.py')) + list((root / 'src/ai_agent').rglob('*.py'))
    for candidate in [root / 'requirements.txt']:
        if candidate.is_file():
            files.append(candidate)
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode('utf8') + b'\0')
        digest.update(path.read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return digest.hexdigest()


def installation_id(root: Path = ROOT) -> str:
    return sha256(str(root.resolve()).casefold().encode('utf8')).hexdigest()[:24]


LOADED_SIGNATURE = source_signature()
INSTANCE_ID = uuid4().hex
STARTED_AT = datetime.now(timezone.utc).isoformat()
LAUNCHER_ID = os.getenv('AVELIN_LAUNCH_ID', '')


def runtime_health() -> dict:
    return {'status': 'ok', 'restart_required': LOADED_SIGNATURE != source_signature(), 'runtime': {
        'contract': API_CONTRACT, 'source_signature': LOADED_SIGNATURE,
        'installation_id': installation_id(), 'instance_id': INSTANCE_ID,
        'launcher_id': LAUNCHER_ID,
        'process_id': os.getpid(), 'started_at': STARTED_AT,
        'capabilities': ['image_preview', 'avatars', 'web_push', 'topic_titles', 'scoped_automation_outputs'],
    }}
