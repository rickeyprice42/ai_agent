"""Consistent local SQLite backup. Private backups stay under ignored data/."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_agent.config import Settings


def main():
    settings = Settings.load(ROOT)
    directory = ROOT / 'data/backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / 'avelin.sqlite3'
    with closing(sqlite3.connect(f'file:{settings.database_file.as_posix()}?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
        counts = {table: backup.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ['users', 'chat_threads', 'messages', 'memory_records', 'automations']}
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'file': target.name,
                'sha256': sha256(target.read_bytes()).hexdigest(), 'integrity_check': 'ok', 'counts': counts}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    print(json.dumps({'backup': str(target.relative_to(ROOT)), 'sha256': manifest['sha256'], 'integrity_check': 'ok', 'counts': counts}))


if __name__ == '__main__':
    main()
