"""Configuration helpers for the OMIS project."""
from __future__ import annotations

from pathlib import Path
import os

# Base directory of the project package.
BASE_DIR = Path(__file__).resolve().parent

# SQLite database file path. Can be overridden via the OMIS_DB_FILE environment variable.
DB_FILE = Path(os.getenv("OMIS_DB_FILE", BASE_DIR / "omis.sqlite3"))

__all__ = ["DB_FILE", "BASE_DIR"]