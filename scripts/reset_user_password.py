"""Interactively replace one user's password without printing it or deleting chats."""
from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for import_path in (ROOT, SRC):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from backend.auth import hash_password
from ai_agent.database import AvelinDatabase


def reset_password(database_path: Path, login: str) -> tuple[str, int]:
    database = AvelinDatabase(database_path)
    existing = database.get_user_for_login(login)
    if existing is None:
        raise ValueError("User not found.")

    password = getpass("New password: ")
    confirmation = getpass("Repeat new password: ")
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    if password != confirmation:
        raise ValueError("Passwords do not match.")

    user = database.reset_user_password(login, hash_password(password))
    if user is None:
        raise ValueError("User disappeared during password reset.")
    with database.connect() as connection:
        chat_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM chat_threads WHERE user_id = ?",
                (user["id"],),
            ).fetchone()[0]
        )
    return user["username"] or user["email"], chat_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--login", required=True, help="Existing username or email")
    args = parser.parse_args()
    try:
        account, chats = reset_password(args.database.resolve(), args.login)
    except (OSError, ValueError) as exc:
        print(f"Password reset failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Password updated for {account}. Existing sessions revoked. Chats preserved: {chats}.")
