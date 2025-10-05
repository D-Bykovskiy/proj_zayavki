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

BACKEND_ENV_NAME = "OMIS_OUTLOOK_BACKEND"
BACKEND_AUTO = "auto"
BACKEND_OAUTH = "oauth"
BACKEND_COM = "com"
BACKEND_FAKE = "fake"
VALID_BACKENDS = {BACKEND_AUTO, BACKEND_OAUTH, BACKEND_COM, BACKEND_FAKE}

DEFAULT_LOOKBACK_MINUTES = 1440
DEFAULT_MAX_MESSAGES = 50
COM_INBOX_ID = 6


@dataclass
class MailboxQuerySettings:
    """Фильтры выборки писем: папка, глубина ретроспективы, лимит количества."""

    folder_path: Tuple[str, ...]
    lookback_minutes: int
    max_messages: int


@dataclass
class OutlookSettings(MailboxQuerySettings):
    """Настройки OAuth2 для доступа к Outlook через exchangelib."""

    email: str
    client_id: str
    client_secret: str
    tenant_id: str


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


def _safe_int_env(name: str, default: int) -> int:
    """Читает целое значение из переменной окружения, подставляя default при ошибке."""

    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Неверное значение %s='%s', используется %s", name, raw, default)
        return default
    return value


def _load_common_mail_settings() -> MailboxQuerySettings:
    """Собирает настройки фильтрации писем, независимые от способа подключения."""

    folder_env = os.getenv("OMIS_OUTLOOK_FOLDER", "").strip()
    folder_path = tuple(part for part in folder_env.split("/") if part)
    lookback_minutes = _safe_int_env("OMIS_OUTLOOK_LOOKBACK_MINUTES", DEFAULT_LOOKBACK_MINUTES)
    max_messages = _safe_int_env("OMIS_OUTLOOK_MAX_MESSAGES", DEFAULT_MAX_MESSAGES)
    return MailboxQuerySettings(
        folder_path=folder_path,
        lookback_minutes=max(1, lookback_minutes),
        max_messages=max(1, max_messages),
    )


def _load_outlook_settings() -> Optional[OutlookSettings]:
    """Собирает и валидирует секреты для подключения к Outlook через exchangelib."""

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

    common = _load_common_mail_settings()
    return OutlookSettings(
        email=email,
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        folder_path=common.folder_path,
        lookback_minutes=common.lookback_minutes,
        max_messages=common.max_messages,
    )


def _iter_outlook_messages(settings: OutlookSettings) -> Iterator[ContractorMessage]:
    """Подключается к Outlook и превращает найденные письма в ContractorMessage."""

    if Account is None or OAuth2Credentials is None or Configuration is None or DELEGATE is None:
        LOGGER.error("Библиотека exchangelib недоступна, пропускаем OAuth2-подключение.")
        return

    credentials = OAuth2Credentials(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        tenant_id=settings.tenant_id,
        identity=settings.email,
    )

    configuration = Configuration(credentials=credentials, server="outlook.office365.com")

    try:
        account = Account(
            primary_smtp_address=settings.email,
            config=configuration,
            autodiscover=True,
            access_type=DELEGATE,
        )
    except Exception as exc:  # pragma: no cover - сеть/авторизация
        LOGGER.exception("Не удалось авторизоваться в Outlook: %s", exc)
        return

    folder = account.inbox
    for part in settings.folder_path:
        try:
            folder = folder / part
        except Exception as exc:  # pragma: no cover - некорректная папка
            LOGGER.exception("Не удалось открыть папку Outlook '%s': %s", part, exc)
            return

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.lookback_minutes)

    try:
        queryset = folder.filter(datetime_received__gte=cutoff).order_by("-datetime_received")
    except Exception as exc:  # pragma: no cover - сбой выборки
        LOGGER.exception("Не удалось получить письма из Outlook: %s", exc)
        return

    processed = 0
    for item in queryset:
        if processed >= settings.max_messages:
            break
        if not isinstance(item, Message):
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


def _normalize_datetime(value: object) -> datetime:
    """Приводит значение времени из Outlook COM к наивному datetime в UTC."""

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.replace(tzinfo=None)

    if hasattr(value, "timestamp"):
        try:
            timestamp = value.timestamp()
        except Exception:
            timestamp = None
        if timestamp is not None:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)

    return datetime.utcnow()


def _extract_com_sender(item: object) -> str:
    """Пытается извлечь адрес отправителя из COM-объекта письма."""

    for attr in ("SenderEmailAddress", "SenderName"):
        try:
            value = getattr(item, attr)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()

    try:
        sender = getattr(item, "Sender")
    except Exception:
        sender = None

    if sender is not None:
        for attr in ("Address", "Name"):
            try:
                value = getattr(sender, attr)
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            exchange_user = sender.GetExchangeUser()
        except Exception:
            exchange_user = None
        if exchange_user is not None:
            primary = getattr(exchange_user, "PrimarySmtpAddress", None)
            if isinstance(primary, str) and primary.strip():
                return primary.strip()

    return "unknown@example.com"


def _extract_com_body(item: object) -> str:
    """Возвращает текст письма из COM-объекта (Body либо HTMLBody)."""

    for attr in ("Body", "HTMLBody"):
        try:
            value = getattr(item, attr)
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
    return ""


def _iter_outlook_com_messages(settings: MailboxQuerySettings) -> Iterator[ContractorMessage]:
    """Читает письма из установленного Outlook через COM (pywin32)."""

    try:  # pragma: no cover - зависимость только для Windows
        import pythoncom  # type: ignore[import]
        import win32com.client  # type: ignore[import]
    except ImportError:
        LOGGER.error("COM backend недоступен: установите пакет pywin32.")
        return

    com_initialized = False
    try:
        pythoncom.CoInitialize()
        com_initialized = True
    except Exception as exc:
        LOGGER.exception("Не удалось инициализировать COM: %s", exc)
        return

    try:
        try:
            namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        except Exception as exc:
            LOGGER.exception("Не удалось подключиться к Outlook через COM: %s", exc)
            return

        try:
            folder = namespace.GetDefaultFolder(COM_INBOX_ID)
        except Exception as exc:
            LOGGER.exception("Не удалось открыть папку Inbox Outlook: %s", exc)
            return

        for part in settings.folder_path:
            try:
                folder = folder.Folders(part)
            except Exception as exc:
                LOGGER.exception("COM backend: не удалось открыть подпапку '%s': %s", part, exc)
                return

        try:
            items = folder.Items
            items.Sort("[ReceivedTime]", True)
        except Exception as exc:
            LOGGER.exception("COM backend: не удалось получить список писем: %s", exc)
            return

        cutoff = datetime.utcnow() - timedelta(minutes=settings.lookback_minutes)
        processed = 0
        for item in items:
            if processed >= settings.max_messages:
                break

            try:
                received_raw = getattr(item, "ReceivedTime", None)
            except Exception:
                received_raw = None
            received_at = _normalize_datetime(received_raw)
            if settings.lookback_minutes and received_at < cutoff:
                break

            subject = getattr(item, "Subject", "") or ""
            body = _extract_com_body(item)

            request_number, position_number = _extract_numbers(subject, body)
            status = _detect_status(f"{subject} {body}")
            sender = _extract_com_sender(item)

            yield ContractorMessage(
                request_number=request_number or "",
                position_number=position_number,
                detected_status=status,
                comment=_compose_comment({"subject": subject, "body": body}),
                received_at=received_at,
                sender=sender,
                subject=subject,
            )
            processed += 1
    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                LOGGER.debug("Не удалось корректно завершить COM-сеанс", exc_info=True)


def _detect_status(text: str) -> Optional[str]:
    """Определяет статус, если в тексте встречаются ключевые слова."""

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
    """Формирует краткий комментарий из темы и первой строки письма."""

    subject = message.get("subject", "").strip()
    body = message.get("body", "").strip()
    snippet = body.splitlines()[0] if body else ""
    parts = [value for value in (subject, snippet) if value]
    return " - ".join(parts) if parts else "Информация из письма не распознана"


def _iter_fake_messages() -> Iterator[ContractorMessage]:
    """Возвращает подготовленный набор тестовых писем."""

    for fake in FAKE_CONTRACTOR_MESSAGES:
        subject = fake["subject"]
        body = fake.get("body", "")
        request_number, position_number = _extract_numbers(subject, body)
        yield ContractorMessage(
            request_number=request_number or "",
            position_number=position_number,
            detected_status=_detect_status(f"{subject} {body}"),
            comment=_compose_comment(fake),
            received_at=fake.get("received", datetime.utcnow()),
            sender=fake.get("sender", "unknown@example.com"),
            subject=subject,
        )


def _resolve_backend_sequence(use_fake: bool, explicit_backend: Optional[str]) -> List[str]:
    """Определяет порядок источников писем в зависимости от настроек."""

    if use_fake:
        return [BACKEND_FAKE]

    backend = (explicit_backend or os.getenv(BACKEND_ENV_NAME, BACKEND_AUTO)).strip().lower()
    if backend not in VALID_BACKENDS:
        LOGGER.warning("Неизвестный backend '%s', используется 'auto'", backend)
        backend = BACKEND_AUTO

    mapping = {
        BACKEND_AUTO: [BACKEND_OAUTH, BACKEND_COM],
        BACKEND_OAUTH: [BACKEND_OAUTH],
        BACKEND_COM: [BACKEND_COM],
        BACKEND_FAKE: [BACKEND_FAKE],
    }
    return mapping[backend]


def fetch_contractor_messages(
    *, use_fake: bool = True, backend: Optional[str] = None
) -> Iterable[ContractorMessage]:
    """Последовательно перебирает выбранные источники и отдаёт письма подрядчика."""

    yielded_any = False
    for candidate in _resolve_backend_sequence(use_fake, backend):
        if candidate == BACKEND_FAKE:
            yielded_any = True
            yield from _iter_fake_messages()
            return

        if candidate == BACKEND_OAUTH:
            settings = _load_outlook_settings()
            if not settings:
                continue
            yielded = False
            for message in _iter_outlook_messages(settings):
                yielded = True
                yielded_any = True
                yield message
            if yielded:
                return
            continue

        if candidate == BACKEND_COM:
            common = _load_common_mail_settings()
            yielded = False
            for message in _iter_outlook_com_messages(common):
                yielded = True
                yielded_any = True
                yield message
            if yielded:
                return

    if not yielded_any:
        LOGGER.warning(
            "Не удалось получить письма из Outlook (backend=%s). Проверьте настройки.",
            backend or os.getenv(BACKEND_ENV_NAME, BACKEND_AUTO),
        )


def process_mailbox(use_fake: bool = True, backend: Optional[str] = None) -> List[str]:
    """Обновляет базу заявок на основе писем и возвращает текстовые отчёты."""

    results: List[str] = []

    for message in fetch_contractor_messages(use_fake=use_fake, backend=backend):
        if not message.request_number:
            LOGGER.warning("Не удалось определить номер заявки (subject=%s)", message.subject)
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
        "--backend",
        choices=[BACKEND_AUTO, BACKEND_OAUTH, BACKEND_COM, BACKEND_FAKE],
        help="явно выбрать источник писем (по умолчанию auto или переменная окружения)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="уровень логирования (по умолчанию INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    results = process_mailbox(use_fake=args.fake, backend=args.backend)
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
