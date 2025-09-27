"""Configuration helpers for the OMIS project."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Optional

# Base directory of the project package.
BASE_DIR: Final[Path] = Path(__file__).resolve().parent

# SQLite database file path. Can be overridden via the OMIS_DB_FILE environment variable.
DB_FILE: Final[Path] = Path(os.getenv("OMIS_DB_FILE", BASE_DIR / "omis.sqlite3"))

# Telegram credentials (optional).
TELEGRAM_TOKEN: Optional[str] = os.getenv("OMIS_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID: Optional[str] = os.getenv("OMIS_TELEGRAM_CHAT_ID")

__all__ = [
    "BASE_DIR",
    "DB_FILE",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_TOKEN",
]