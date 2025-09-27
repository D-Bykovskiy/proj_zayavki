"""Слой доступа к базе данных для управления заявками подрядчиков."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from .config import DB_FILE

LOGGER = logging.getLogger(__name__)
DEFAULT_STATUS = "\u0437\u0430\u044f\u0432\u043a\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0430"


def _ensure_db_dir() -> None:
    """Убедиться, что каталог для файла базы данных существует."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    """Вернуть текущий момент времени в формате ISO без микросекунд."""
    return datetime.utcnow().replace(microsecond=0).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Возвращать соединение SQLite с включённой фабрикой строк."""
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
    """Создать таблицу заявок и необходимые индексы, если их нет."""
    try:
        with _connect() as conn:
            # SQL: создаём таблицу для хранения заявок и данных о статусах.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_number TEXT NOT NULL,
                    position_number TEXT NOT NULL,
                    comment TEXT,
                    status TEXT NOT NULL,
                    status_updated_at TEXT NOT NULL,
                    UNIQUE(request_number, position_number)
                )
                """
            )
            # SQL: индекс ускоряет выборки по времени обновления статуса.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_requests_status_updated_at
                ON requests(status_updated_at)
                """
            )
    except sqlite3.Error as exc:
        LOGGER.exception("Failed to initialize database schema at %s: %s", DB_FILE, exc)
        raise


def add_request(request_number: str, position_number: str, comment: str) -> int:
    """Добавить новую заявку подрядчика в базу данных.

    Args:
        request_number: Идентификатор из внутренней системы.
        position_number: Привязанный номер позиции или оборудования.
        comment: Дополнительные сведения от инженера.

    Returns:
        Первичный ключ созданной записи.
    """
    timestamp = _utc_now()
    try:
        with _connect() as conn:
            # SQL: записываем новую заявку с базовым статусом.
            cursor = conn.execute(
                """
                INSERT INTO requests (
                    request_number,
                    position_number,
                    comment,
                    status,
                    status_updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (request_number, position_number, comment, DEFAULT_STATUS, timestamp),
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
    """Обновить статус существующей заявки.

    Args:
        request_number: Идентификатор, связывающий письмо и заявку.
        new_status: Статус, полученный из коммуникаций подрядчика.
        position_number: Необязательный дополнительный идентификатор для уточнения.

    Returns:
        True, если обновлена хотя бы одна запись, иначе False.
    """
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


def get_requests(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Получить список заявок, упорядоченный по времени обновления статуса."""
    query = (
        "SELECT id, request_number, position_number, comment, status, status_updated_at "
        "FROM requests ORDER BY datetime(status_updated_at) DESC"
    )
    parameters: List[Any] = []

    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)

    try:
        with _connect() as conn:
            # SQL: выбираем последние обновлённые заявки для отображения.
            cursor = conn.execute(query, parameters)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        LOGGER.exception("Failed to fetch requests: %s", exc)
        raise


__all__ = [
    "DEFAULT_STATUS",
    "add_request",
    "get_requests",
    "init_db",
    "update_status",
]