"""Store a closed-test registration code as a hash after hidden interactive input."""
from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import tempfile


def set_access_code(env_file: Path) -> None:
    access_code = getpass("Closed-test access code: ").strip()
    confirmation = getpass("Repeat access code: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", access_code):
        raise ValueError("Access code must contain 8-64 ASCII letters or digits.")
    if access_code != confirmation:
        raise ValueError("Access codes do not match.")

    from hashlib import sha256

    digest = sha256(access_code.encode("utf-8")).hexdigest()
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    replacement = f"REGISTRATION_ACCESS_CODE_HASH={digest}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("REGISTRATION_ACCESS_CODE_HASH="):
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        if updated and updated[-1]:
            updated.append("")
        updated.append(replacement)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{env_file.name}.", dir=env_file.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(updated) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, env_file)
        os.chmod(env_file, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/etc/avelin/avelin.env"))
    args = parser.parse_args()
    try:
        set_access_code(args.env_file.resolve())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Registration access code setup failed: {exc}")
    print("Registration access code hash stored. Restart Avelin to apply it.")
