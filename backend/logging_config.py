from __future__ import annotations

from logging.handlers import RotatingFileHandler
from pathlib import Path
import logging


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(root_dir: Path) -> None:
    log_dir = root_dir / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if any(getattr(handler, "_avelin_handler", False) for handler in root_logger.handlers):
        return

    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler._avelin_handler = True  # type: ignore[attr-defined]

    file_handler = RotatingFileHandler(
        log_dir / "backend.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._avelin_handler = True  # type: ignore[attr-defined]

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
