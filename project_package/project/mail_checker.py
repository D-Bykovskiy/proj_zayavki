"""Обработчик писем подрядчика из Outlook."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from . import database

LOGGER = logging.getLogger(__name__)

STATUS_KEYWORDS = {
    "заявка принята": ("принят", "принята", "подтвержд"),
    "подрядчик в пути": ("в пути", "выех"),
    "подрядчик на месте": ("на месте", "прибыл"),
    "подрядчик убыл": ("убыл", "завершил", "законч"),
}

REQUEST_RE = re.compile(r"(?:заявк[аи]|req)[^0-9]*(?P<number>\d+)", re.IGNORECASE)
POSITION_RE = re.compile(r"(?:позици[яи]|pos)[^0-9]*(?P<number>\d+)", re.IGNORECASE)


@dataclass
class ContractorMessage:
    """Данные, извлечённые из письма подрядчика."""

    request_number: str
    position_number: Optional[str]
    detected_status: Optional[str]
    comment: str
    received_at: datetime
    sender: str
    subject: str


FAKE_CONTRACTOR_MESSAGES: Sequence[dict] = (
    # <<< Тестовые письма: измените содержимое для проверки логики >>>
    {
        "subject": "Заявка 101: подрядчик выехал",
        "body": "Заявка №101. Позиция 12. Подрядчик в пути, ожидаем прибытие через 30 минут.",
        "sender": "contractor@example.com",
        "received": datetime(2025, 9, 27, 10, 15),
    },
    {
        "subject": "REQ-101 подрядчик на месте",
        "body": "Подрядчик прибыл на позицию 12. Проверка оборудования.",
        "sender": "contractor@example.com",
        "received": datetime(2025, 9, 27, 11, 5),
    },
    {
        "subject": "REQ-102 завершено",
        "body": "Позиция 8. Работы выполнены, подрядчик убыл.",
        "sender": "contractor@example.com",
        "received": datetime(2025, 9, 27, 12, 40),
    },
)


def _detect_status(text: str) -> Optional[str]:
    lowered = text.lower()
    for status, keywords in STATUS_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return status
    return None


def _extract_numbers(subject: str, body: str) -> tuple[Optional[str], Optional[str]]:
    request_number = None
    position_number = None

    for text in (subject, body):
        if request_number is None:
            match = REQUEST_RE.search(text)
            if match:
                request_number = match.group("number")
        if position_number is None:
            match = POSITION_RE.search(text)
            if match:
                position_number = match.group("number")
    return request_number, position_number


def _compose_comment(message: dict) -> str:
    subject = message.get("subject", "").strip()
    body = message.get("body", "").strip()
    snippet = body.splitlines()[0] if body else ""
    parts = [value for value in (subject, snippet) if value]
    return " - ".join(parts) if parts else "Сообщение подрядчика"


def fetch_contractor_messages(use_fake: bool = True) -> Iterable[ContractorMessage]:
    """Загружает письма подрядчика. Пока доступен только тестовый режим."""
    if use_fake:
        for fake in FAKE_CONTRACTOR_MESSAGES:
            subject = fake["subject"]
            body = fake.get("body", "")
            request_number, position_number = _extract_numbers(subject, body)
            yield ContractorMessage(
                request_number=request_number or "",
                position_number=position_number,
                detected_status=_detect_status(subject + " " + body),
                comment=_compose_comment(fake),
                received_at=fake.get("received", datetime.utcnow()),
                sender=fake.get("sender", "unknown@example.com"),
                subject=subject,
            )
        return

    # TODO: заменить на интеграцию с Outlook через exchangelib.
    LOGGER.warning("Режим обращения к реальной почте ещё не реализован")
    return []


def process_mailbox(use_fake: bool = True) -> List[str]:
    """Сканирует почтовый ящик и обновляет заявки."""
    results: List[str] = []

    for message in fetch_contractor_messages(use_fake=use_fake):
        if not message.request_number:
            LOGGER.warning(
                "Не удалось определить номер заявки (subject=%s)",
                message.subject,
            )
            results.append(
                f"Пропуск письма от {message.sender}: не найден номер заявки"
            )
            continue

        status_applied = False
        comment_saved = False

        if message.detected_status:
            status_applied = database.update_status(
                request_number=message.request_number,
                new_status=message.detected_status,
                position_number=message.position_number,
            )

        if message.comment:
            comment_saved = database.update_comment(
                request_number=message.request_number,
                comment=message.comment,
                position_number=message.position_number,
            )

        summary_parts = [
            f"Заявка {message.request_number}",
            f"позиция {message.position_number}" if message.position_number else "",
            f"статус -> {message.detected_status}" if status_applied and message.detected_status else "",
            "комментарий обновлён" if comment_saved else "",
        ]
        summary = "; ".join(part for part in summary_parts if part)
        if not summary:
            summary = (
                f"Данные из письма не применены (subject={message.subject}, "
                f"request={message.request_number})"
            )
        results.append(summary)

    return results


__all__ = [
    "ContractorMessage",
    "process_mailbox",
    "fetch_contractor_messages",
]
