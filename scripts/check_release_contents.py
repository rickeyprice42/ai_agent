"""Check staged release paths/content without ever printing a credential value."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ai_agent.config import Settings


def main():
    files = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], cwd=ROOT).decode('utf8').split('\0')
    settings = Settings.load(ROOT)
    secrets = [key.encode() for key in [settings.ollama_api_key, settings.openai_api_key] if len(key) >= 20]
    problems = []
    count = 0
    for name in filter(None, files):
        count += 1
        if (name in {'.env', 'API_KEY.txt', 'docs/api_ollama.txt'}
                or name.startswith(('data/', '.local/', '.logs/', '.venv/', 'frontend/node_modules/', 'frontend/dist/'))):
            problems.append((name, 'private/runtime path'))
            continue
        content = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        if any(secret in content for secret in secrets):
            problems.append((name, 'configured credential'))
        if re.search(rb'-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----', content):
            problems.append((name, 'private key'))
        if re.search(rb'sk-(?:proj-|svcacct-)[A-Za-z0-9_-]{35,}', content):
            problems.append((name, 'credential-shaped content'))
    print({'staged_files': count, 'status': 'FAIL' if problems else 'PASS', 'problems': problems})
    return int(bool(problems))


if __name__ == '__main__':
    raise SystemExit(main())
