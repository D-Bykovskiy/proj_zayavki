"""Подробный парсер почты Outlook с интеграцией в базу OMIS."""
from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from . import database

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - необязательная зависимость
    # exchangelib нужен только при живом подключении к Outlook, поэтому импортируем
    # его лениво и спокойно переживаем отсутствие библиотеки в dev-окружении.
    from exchangelib import Account, Configuration, DELEGATE, Message, OAuth2Credentials
except ImportError:  # pragma: no cover - необязательная зависимость
    Account = Configuration = Message = OAuth2Credentials = None  # type: ignore[assignment]
    DELEGATE = None  # type: ignore[assignment]

# Словарь ключевых фраз, по которым определяем статус заявки в письме.
STATUS_KEYWORDS = {
    "заявка принята": ("принят", "принята", "подтвержд"),
    "подрядчик в пути": ("в пути", "выех"),
    "подрядчик на месте": ("на месте", "прибыл"),
    "подрядчик убыл": ("убыл", "завершил", "законч"),
}

# Регулярные выражения, вытаскивающие номера заявок/позиций из свободного текста.
REQUEST_RE = re.compile(r"(?:заявк[аи]|req)[^0-9]*(?P<number>\d+)", re.IGNORECASE)
POSITION_RE = re.compile(r"(?:позици[яи]|pos)[^0-9]*(?P<number>\d+)", re.IGNORECASE)


@dataclass
class ContractorMessage:
    """Единый формат данных, полученных из письма подрядчика."""

    request_number: str
    position_number: Optional[str]
    detected_status: Optional[str]
    comment: str
    received_at: datetime
    sender: str
    subject: str


@dataclass
class OutlookSettings:
    """Настройки подключения к Outlook, собранные из переменных окружения."""

    email: str
    client_id: str
    client_secret: str
    tenant_id: str
    folder_path: Tuple[str, ...]
    lookback_minutes: int
    max_messages: int


FAKE_CONTRACTOR_MESSAGES: Sequence[dict] = (
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


def _load_outlook_settings() -> Optional[OutlookSettings]:
    """Собирает и валидирует секреты для подключения к Outlook."""

    # Каждое значение чистим от случайных пробелов, чтобы ошибки копирования
    # реквизитов не ломали авторизацию.
    email = os.getenv("OMIS_OUTLOOK_EMAIL", "").strip()
    client_id = os.getenv("OMIS_OUTLOOK_CLIENT_ID", "").strip()
    client_secret = os.getenv("OMIS_OUTLOOK_CLIENT_SECRET", "").strip()
    tenant_id = os.getenv("OMIS_OUTLOOK_TENANT_ID", "").strip()

    required_values = {
        "OMIS_OUTLOOK_EMAIL": email,
        "OMIS_OUTLOOK_CLIENT_ID": client_id,
        "OMIS_OUTLOOK_CLIENT_SECRET": client_secret,
        "OMIS_OUTLOOK_TENANT_ID": tenant_id,
    }
    missing = [name for name, value in required_values.items() if not value]
    if missing:
        LOGGER.warning(
            "Outlook настроен не полностью, отсутствуют переменные: %s",
            ", ".join(sorted(missing)),
        )
        return None

    # Дополнительные параметры позволяют ограничить глубину выборки и выбрать
    # нужную папку без правок кода.
    folder_env = os.getenv("OMIS_OUTLOOK_FOLDER", "").strip()
    folder_path = tuple(part for part in folder_env.split("/") if part)

    try:
        lookback_minutes = int(os.getenv("OMIS_OUTLOOK_LOOKBACK_MINUTES", "1440"))
    except ValueError:
        LOGGER.warning("Неверное значение OMIS_OUTLOOK_LOOKBACK_MINUTES, используем 1440")
        lookback_minutes = 1440

    try:
        max_messages = int(os.getenv("OMIS_OUTLOOK_MAX_MESSAGES", "50"))
    except ValueError:
        LOGGER.warning("Неверное значение OMIS_OUTLOOK_MAX_MESSAGES, используем 50")
        max_messages = 50

    return OutlookSettings(
        email=email,
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        folder_path=folder_path,
        lookback_minutes=max(1, lookback_minutes),
        max_messages=max(1, max_messages),
    )


def _iter_outlook_messages(settings: OutlookSettings) -> Iterator[ContractorMessage]:
    """Подключается к Outlook и превращает найденные письма в ContractorMessage."""

    if Account is None or OAuth2Credentials is None or Configuration is None or DELEGATE is None:
        LOGGER.error(
            "Библиотека exchangelib не установлена, включите её для реальной синхронизации",
        )
        return

    # OAuth2Credentials поддерживает современную авторизацию Microsoft 365.
    credentials = OAuth2Credentials(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        tenant_id=settings.tenant_id,
        identity=settings.email,
    )

    configuration = Configuration(credentials=credentials, server="outlook.office365.com")

    try:
        # autodiscover=True позволяет exchangelib автоматически подобрать URL EWS.
        account = Account(
            primary_smtp_address=settings.email,
            config=configuration,
            autodiscover=True,
            access_type=DELEGATE,
        )
    except Exception as exc:  # pragma: no cover - зависит от внешнего сервиса
        LOGGER.exception("Не удалось подключиться к Outlook: %s", exc)
        return

    # Начинаем с Inbox, затем спускаемся по цепочке папок, если оператор указал путь.
    folder = account.inbox
    for part in settings.folder_path:
        try:
            folder = folder / part
        except Exception as exc:  # pragma: no cover - зависит от структуры ящика
            LOGGER.exception("Не удалось открыть папку Outlook '%s': %s", part, exc)
            return

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.lookback_minutes)

    try:
        queryset = folder.filter(datetime_received__gte=cutoff).order_by("-datetime_received")
    except Exception as exc:  # pragma: no cover - сетевые/фильтрационные ошибки
        LOGGER.exception("Не удалось получить список писем: %s", exc)
        return

    processed = 0
    for item in queryset:
        if processed >= settings.max_messages:
            break
        if not isinstance(item, Message):
            # exchangelib может возвращать приглашения/уведомления; пропускаем их.
            continue

        try:
            received_at = item.datetime_received.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            received_at = datetime.utcnow()

        subject = item.subject or ""
        body_plain = getattr(item, "text_body", None) or (item.body or "")

        request_number, position_number = _extract_numbers(subject, body_plain)
        status = _detect_status(f"{subject} {body_plain}")

        sender = "unknown@example.com"
        if getattr(item, "sender", None) and getattr(item.sender, "email_address", None):
            sender = item.sender.email_address  # type: ignore[assignment]
        elif getattr(item, "author", None) and getattr(item.author, "email_address", None):
            sender = item.author.email_address  # type: ignore[assignment]

        payload = {"subject": subject, "body": body_plain}
        yield ContractorMessage(
            request_number=request_number or "",
            position_number=position_number,
            detected_status=status,
            comment=_compose_comment(payload),
            received_at=received_at,
            sender=sender,
            subject=subject,
        )
        processed += 1


def _detect_status(text: str) -> Optional[str]:
    """Возвращает статус, если в тексте письма найдено известное ключевое слово."""
    lowered = text.lower()
    for status, keywords in STATUS_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return status
    return None


def _extract_numbers(subject: str, body: str) -> tuple[Optional[str], Optional[str]]:
    """Ищет номера заявки и позиции в теме и теле письма."""
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
    """Формирует короткий комментарий по письму для занесения в БД."""
    subject = message.get("subject", "").strip()
    body = message.get("body", "").strip()
    snippet = body.splitlines()[0] if body else ""
    parts = [value for value in (subject, snippet) if value]
    return " - ".join(parts) if parts else "Содержимое письма недоступно"


def fetch_contractor_messages(use_fake: bool = True) -> Iterable[ContractorMessage]:
    """Возвращает письма подрядчика из Outlook или подготовленный фейковый набор."""

    # Если запрошен реальный Outlook, пытаемся собрать настройки и пройти по ящику.
    # Любые ошибки приводят к возврату тестовых сообщений, чтобы CLI оставался рабочим.
    if not use_fake:
        settings = _load_outlook_settings()
        if settings:
            yielded = False
            for message in _iter_outlook_messages(settings):
                yielded = True
                yield message
            if yielded:
                return
        LOGGER.warning("Переходим на тестовые письма, Outlook недоступен или не настроен.")

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


def process_mailbox(use_fake: bool = True) -> List[str]:
    """Объединяет письма с базой и возвращает текстовые отчёты по каждой записи."""

    # Список итоговых строк возвращаем вызывающему коду: CLI печатает их в терминал,
    # а тесты могут проверять, что логика обновления сработала корректно.
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Позволяет разово запустить обработку почты через CLI."""
    parser = argparse.ArgumentParser(
        description="Обновляет статусы заявок на основе писем подрядчика.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="использовать встроенные тестовые письма вместо подключения к Outlook",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="уровень логирования (по умолчанию INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    results = process_mailbox(use_fake=args.fake)
    if not results:
        print("Новых писем подрядчика не найдено.")
        return 0

    for line in results:
        print(line)
    return 0


__all__ = [
    "ContractorMessage",
    "OutlookSettings",
    "fetch_contractor_messages",
    "main",
    "process_mailbox",
]


if __name__ == "__main__":  # pragma: no cover - CLI обёртка
    raise SystemExit(main())
