"""Real-provider image conversion with synthetic HEIC and an isolated database."""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from PIL import Image
from ai_agent.agent import Agent
from ai_agent.config import Settings
import ai_agent.image_processing  # registers HEIC


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['openai', 'ollama_cloud'], required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    output = ROOT / '.local/product-2026-11-04/media-live' / args.provider / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    workspace = output / 'workspace'
    workspace.mkdir(parents=True)
    source = workspace / 'fixture.heic'
    Image.new('RGB', (800, 400), '#b14bb7').save(source, 'HEIF')
    before = sha256(source.read_bytes()).hexdigest()
    settings = replace(Settings.load(ROOT), model_provider=args.provider, model_name=args.model,
        openai_model=args.model if args.provider == 'openai' else Settings.load(ROOT).openai_model,
        reasoning_fallback_enabled=False, llm_timeout_seconds=75, llm_max_retries=0,
        database_file=output / 'qa.sqlite3', memory_file=output / 'memory.json', tool_workspace_dir=workspace)
    agent = Agent(settings, user_id='media-qa', thread_id='media-qa')
    original = agent.tools.list_for_model
    agent.tools.list_for_model = lambda: [tool for tool in original() if tool['name'] in {'inspect_image', 'transform_image', 'list_workspace_files'}]
    start = perf_counter()
    events = []
    failure = None
    try:
        events = list(agent.respond_stream('Преобразуй фото fixture.heic в PNG. Ширина 256 пикселей, пропорции сохрани. Верни готовый файл outputs/converted.png. Оригинал сохрани.'))
        target = workspace / 'outputs/converted.png'
        with Image.open(target) as image:
            assert image.format == 'PNG' and image.size == (256, 128), 'Incorrect actual output pixels/format'
        assert sha256(source.read_bytes()).hexdigest() == before, 'Original changed'
        assert any(a.get('path') == 'outputs/converted.png' for message in agent.memory.database.list_messages(agent.memory.thread_id) for a in message.get('artifacts', [])), 'Result is missing from chat artifacts'
    except Exception as exc:
        failure = f'{type(exc).__name__}: {exc}'
    report = {'status': 'FAIL' if failure else 'PASS', 'provider': args.provider, 'model': args.model,
        'elapsed_seconds': round(perf_counter() - start, 3), 'failure': failure, 'events': events,
        'actions': agent.memory.database.list_action_logs(agent.memory.user_id, limit=30)}
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({key: report[key] for key in ['status', 'provider', 'model', 'elapsed_seconds', 'failure']}, ensure_ascii=False))
    return int(bool(failure))


if __name__ == '__main__':
    raise SystemExit(main())
