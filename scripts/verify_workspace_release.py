"""Verify the loaded mobile-interaction release without sending model requests.

A short-lived diagnostic session is revoked in finally. No private message text,
attachment names, API keys, session tokens or push endpoints are printed.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from urllib.request import build_opener, ProxyHandler, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from backend.auth import issue_session_token
from backend.runtime_version import source_signature
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/workspace-interactions/runtime-proof.json")
    args = parser.parse_args()
    database = AvelinDatabase(Settings.load(ROOT).database_file)
    with database.connect() as connection:
        user = connection.execute("SELECT id FROM users WHERE username='iphonetest'").fetchone()
        if user is None:
            raise RuntimeError("Diagnostic test account unavailable; no account was created.")
        threads = database.list_chat_threads(user["id"])
    if not threads:
        raise RuntimeError("No accessible existing diagnostic thread.")
    session = issue_session_token()
    database.create_session(user["id"], session.token_hash, session.expires_at)
    opener = build_opener(ProxyHandler({}))
    checks = []

    def get(path):
        request = Request(args.base.rstrip("/") + path, headers={"Authorization": "Bearer " + session.raw})
        with opener.open(request, timeout=20) as response:
            return json.load(response)

    try:
        health = get("/api/health")
        assert not health["restart_required"] and health["runtime"]["source_signature"] == source_signature()
        checks.append("loaded source matches disk")
        # Vite's /openapi.json belongs to its own SPA, so use the actual backend.
        with opener.open("http://127.0.0.1:8000/openapi.json", timeout=15) as response:
            paths = json.load(response)["paths"]
        required = {"/api/chats/{thread_id}/model-settings", "/api/chats/{thread_id}/questions/{question_id}/runs",
                    "/api/chats/{thread_id}/media", "/api/chats/{thread_id}/media/file",
                    "/api/chats/{thread_id}/media/preview", "/api/home/suggestions"}
        assert required.issubset(paths)
        checks.append("model, question and media API contracts loaded")
        thread_id = threads[0]["id"]
        selected = get(f"/api/chats/{thread_id}/model-settings")
        assert selected["provider"] and selected["model_name"] and isinstance(selected["override"], bool)
        checks.append("existing chat model settings readable")
        media = get(f"/api/chats/{thread_id}/media")
        assert media["thread_id"] == thread_id and all(isinstance(media[key], list) for key in ("images", "files", "links"))
        checks.append("existing thread-bound media readable")
        suggestions = get("/api/home/suggestions")
        assert len(suggestions["basic"]) >= 4 and len(suggestions["advanced"]) >= 4
        checks.append("basic and advanced suggestions available")
        bootstrap = get(f"/api/bootstrap?thread_id={thread_id}")
        assert isinstance(bootstrap["chat_approvals"], list) and isinstance(bootstrap["chat_questions"], list)
        checks.append("authoritative pending interactions available")
    finally:
        database.delete_session(session.token_hash)
    preserved = None
    if args.backup:
        with sqlite3.connect(args.backup) as backup, database.connect() as current:
            backup.row_factory = sqlite3.Row
            before = {row["id"]: dict(row) for row in backup.execute("SELECT * FROM messages")}
            after = {row["id"]: dict(row) for row in current.execute("SELECT * FROM messages")}
            assert all(after.get(identifier) == row for identifier, row in before.items()), "An original message changed. Inspect privately."
            preserved = len(before)
        checks.append("original message rows unchanged")
    result = {"status": "PASS", "checks": checks, "session_revoked": True,
              "original_messages_unchanged": preserved, "new_physical_iphone_checks": "NOT TESTED",
              "actual_push_delivery": "confirmed by user before this release"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
