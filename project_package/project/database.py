"""Слой работы с базой данных для управления заявками подрядчика."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from .config import DB_FILE

LOGGER = logging.getLogger(__name__)
DEFAULT_STATUS = "заявка отправлена"
ROBOT_AUTHOR = "Робот"


def _ensure_db_dir() -> None:
    """Создаёт каталог для файла базы данных, если он отсутствует."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    """Возвращает текущий момент времени в ISO-формате без микросекунд."""
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Добавляет недостающие столбцы в таблицу заявок."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}

    if "comment_author" not in columns:
        conn.execute("ALTER TABLE requests ADD COLUMN comment_author TEXT")

    if "created_at" not in columns:
        conn.execute("ALTER TABLE requests ADD COLUMN created_at TEXT")
        now = _utc_now()
        conn.execute(
            "UPDATE requests SET created_at = COALESCE(created_at, status_updated_at, ?)",
            (now,),
        )


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Возвращает соединение SQLite с включённой фабрикой строк."""
    _ensure_db_dir()
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except sqlite3.Error as exc:  # pragma: no cover - защитное логирование
        connection.rollback()
        LOGGER.exception("Database error: %s", exc)
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Создаёт таблицу заявок, индексы и выполняет миграции схемы."""
    try:
        with _connect() as conn:
            # SQL: создаём таблицу для хранения заявок и связанных метаданных.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_number TEXT NOT NULL,
                    position_number TEXT NOT NULL,
                    comment TEXT,
                    comment_author TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status_updated_at TEXT NOT NULL,
                    UNIQUE(request_number, position_number)
                )
                """
            )
            # SQL: индекс ускоряет выборки по времени последнего обновления статуса.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_requests_status_updated_at
                ON requests(status_updated_at)
                """
            )
            _ensure_schema(conn)
    except sqlite3.Error as exc:
        LOGGER.exception("Failed to initialize database schema at %s: %s", DB_FILE, exc)
        raise


def add_request(
    request_number: str,
    position_number: str,
    comment: str,
    comment_author: str,
) -> int:
    """Добавляет новую заявку подрядчика и возвращает её идентификатор."""
    timestamp = _utc_now()
    try:
        with _connect() as conn:
            # SQL: вставляем заявку с автором и временными метками.
            cursor = conn.execute(
                """
                INSERT INTO requests (
                    request_number,
                    position_number,
                    comment,
                    comment_author,
                    status,
                    created_at,
                    status_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_number,
                    position_number,
                    comment,
                    comment_author,
                    DEFAULT_STATUS,
                    timestamp,
                    timestamp,
                ),
            )
            request_id = int(cursor.lastrowid)
            LOGGER.info(
                "Request %s/%s created with status '%s'",
                request_number,
                position_number,
                DEFAULT_STATUS,
            )
            return request_id
    except sqlite3.IntegrityError as exc:
        LOGGER.exception(
            "Request %s/%s already exists and cannot be duplicated: %s",
            request_number,
            position_number,
            exc,
        )
        raise
    except sqlite3.Error as exc:
        LOGGER.exception(
            "Failed to insert request %s/%s: %s",
            request_number,
            position_number,
            exc,
        )
        raise


def update_status(
    request_number: str,
    new_status: str,
    position_number: Optional[str] = None,
) -> bool:
    """Обновляет статус существующей заявки."""
    timestamp = _utc_now()
    parameters: List[Any] = [new_status, timestamp, request_number]
    where_clause = "request_number = ?"

    if position_number is not None:
        where_clause += " AND position_number = ?"
        parameters.append(position_number)

    sql = (
        "UPDATE requests SET status = ?, status_updated_at = ? "
        f"WHERE {where_clause}"
    )

    try:
        with _connect() as conn:
            # SQL: обновляем статус выбранной заявки.
            cursor = conn.execute(sql, parameters)
            updated = cursor.rowcount > 0

        if updated:
            LOGGER.info(
                "Updated request %s%s to status '%s'",
                request_number,
                f"/{position_number}" if position_number else "",
                new_status,
            )
        else:
            LOGGER.warning(
                "No request found for %s%s when setting status '%s'",
                request_number,
                f"/{position_number}" if position_number else "",
                new_status,
            )
        return updated
    except sqlite3.Error as exc:
        LOGGER.exception(
            "Failed to update status for %s%s: %s",
            request_number,
            f"/{position_number}" if position_number else "",
            exc,
        )
        raise


def update_comment(
    request_number: str,
    comment: str,
    position_number: Optional[str] = None,
    author: str = ROBOT_AUTHOR,
) -> bool:
    """Обновляет комментарий и автора заявки."""
    parameters: List[Any] = [comment, author, request_number]
    where_clause = "request_number = ?"

    if position_number is not None:
        where_clause += " AND position_number = ?"
        parameters.append(position_number)

    sql = (
        "UPDATE requests SET comment = ?, comment_author = ? "
        f"WHERE {where_clause}"
    )

    try:
        with _connect() as conn:
            # SQL: записываем комментарий и автора.
            cursor = conn.execute(sql, parameters)
            updated = cursor.rowcount > 0

        if updated:
            LOGGER.info(
                "Updated comment for request %s%s",
                request_number,
                f"/{position_number}" if position_number else "",
            )
        else:
            LOGGER.warning(
                "No request found for %s%s when saving comment",
                request_number,
                f"/{position_number}" if position_number else "",
            )
        return updated
    except sqlite3.Error as exc:
        LOGGER.exception(
            "Failed to update comment for %s%s: %s",
            request_number,
            f"/{position_number}" if position_number else "",
            exc,
        )
        raise


def get_requests(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает список заявок, отсортированный по времени обновления статуса."""
    query = (
        "SELECT id, request_number, position_number, comment, comment_author, "
        "status, created_at, status_updated_at "
        "FROM requests ORDER BY datetime(status_updated_at) DESC"
    )
    parameters: List[Any] = []

    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)

    try:
        with _connect() as conn:
            # SQL: загружаем заявки, наиболее актуальные по времени обновления.
            cursor = conn.execute(query, parameters)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        LOGGER.exception("Failed to fetch requests: %s", exc)
        raise


__all__ = [
    "DEFAULT_STATUS",
    "ROBOT_AUTHOR",
    "add_request",
    "get_requests",
    "init_db",
    "update_comment",
    "update_status",
]