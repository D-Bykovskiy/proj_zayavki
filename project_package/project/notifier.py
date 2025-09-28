"""Уведомления о задержках в Telegram."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Sequence
from urllib import parse, request

from . import config, database

LOGGER = logging.getLogger(__name__)
TELEGRAM_API_URL = "https://api.telegram.org"

# <<< При необходимости адаптируйте шаблон сообщения под формат команды >>>


def _format_delay_message(row: Dict[str, object]) -> str:
    """Собирает текст напоминания о заявке с задержкой."""
    updated_at = str(row.get("status_updated_at", ""))
    try:
        dt = datetime.fromisoformat(updated_at)
        timestamp = dt.strftime("%Y.%m.%d %H:%M")
    except ValueError:
        timestamp = updated_at

    request_number = str(row.get("request_number", "?"))
    position_number = str(row.get("position_number") or "-")
    status = str(row.get("status", "неизвестно"))

    message_template = (
        "⚠ Заявка №{req} (позиция {pos}) давно без обновлений.\n"
        "Текущий статус: {status}.\n"
        "Последнее обновление: {ts}."
    )
    return message_template.format(
        req=request_number,
        pos=position_number,
        status=status,
        ts=timestamp,
    )


def send_message(text: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """Отправляет сообщение в Telegram или логирует его, если бот не настроен."""
    token = token or config.TELEGRAM_TOKEN
    chat_id = chat_id or config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        LOGGER.info("[FAKE TELEGRAM] %s", text)
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    data = parse.urlencode(payload).encode()
    url = f"{TELEGRAM_API_URL}/bot{token}/sendMessage"

    try:
        with request.urlopen(url, data=data, timeout=10) as response:
            body = response.read().decode(errors="ignore")
            if response.getcode() != 200:
                LOGGER.error("Telegram API error: %s", body)
                return False
            result = json.loads(body or "{}")
            success = result.get("ok", True)
            if not success:
                LOGGER.error("Telegram returned failure: %s", result)
            return success
    except Exception as exc:  # pragma: no cover - защитное логирование
        LOGGER.exception("Failed to send Telegram message: %s", exc)
        return False


def notify_delays(minutes: int = 60, send: bool = True) -> List[str]:
    """Формирует список уведомлений по заявкам, которые не обновлялись дольше `minutes`."""
    delayed = database.get_delayed_requests(minutes)
    notifications: List[str] = []

    for row in delayed:
        message = _format_delay_message(row)
        notifications.append(message)
        if send:
            send_message(message)
        else:
            LOGGER.info("[DRY RUN] %s", message)

    if not delayed:
        LOGGER.debug("Нет заявок с задержкой более %s минут", minutes)

    return notifications


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Командный интерфейс для проверки задержавшихся заявок."""
    parser = argparse.ArgumentParser(
        description="Отправляет напоминания в Telegram о заявках без обновлений.",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="порог задержки в минутах (по умолчанию 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="не отправлять в Telegram, а только печатать сообщения",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="уровень логирования (по умолчанию INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    notifications = notify_delays(minutes=args.minutes, send=not args.dry_run)
    if not notifications:
        print(
            f"Заявок с задержкой более {args.minutes} минут не найдено."
        )
        return 0

    for message in notifications:
        print(message)
    return 0


__all__ = ["main", "notify_delays", "send_message"]


if __name__ == "__main__":  # pragma: no cover - CLI обёртка
    raise SystemExit(main())
