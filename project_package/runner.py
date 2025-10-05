"""Утилита пакетного запуска почтовых и уведомительных задач OMIS."""
from __future__ import annotations

import argparse
import logging
from typing import Optional, Sequence

from .project import mail_checker, notifier

LOGGER = logging.getLogger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Запускает обработку почты и проверку задержек подрядчика."""

    parser = argparse.ArgumentParser(
        description=(
            "Выполняет проектные задачи OMIS: сначала обновляет статусы по почте,"
            " затем отправляет уведомления о задержках."
        ),
    )
    parser.add_argument(
        "--skip-mail",
        action="store_true",
        help="не запускать обработку почты",
    )
    parser.add_argument(
        "--skip-notifier",
        action="store_true",
        help="не запускать проверку задержек",
    )
    parser.add_argument(
        "--fake-mail",
        action="store_true",
        help="использовать тестовые письма (аналог --fake для mail_checker)",
    )
    parser.add_argument(
        "--mail-backend",
        choices=[
            mail_checker.BACKEND_AUTO,
            mail_checker.BACKEND_OAUTH,
            mail_checker.BACKEND_COM,
            mail_checker.BACKEND_FAKE,
        ],
        help="принудительно выбрать источник писем (иначе auto или переменная окружения)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="порог задержки для уведомлений (по умолчанию 60 минут)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="не отправлять сообщения в Telegram, только вывести их в лог",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="уровень логирования (по умолчанию INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    if not args.skip_mail:
        LOGGER.info("Запускаем обработку почты подрядчика")
        results = mail_checker.process_mailbox(
            use_fake=args.fake_mail,
            backend=args.mail_backend,
        )
        if results:
            for line in results:
                LOGGER.info("MAIL: %s", line)
        else:
            LOGGER.info("MAIL: новых писем не найдено")
    else:
        LOGGER.info("Обработка почты пропущена (--skip-mail)")

    if not args.skip_notifier:
        LOGGER.info("Запускаем проверку задержек (%s минут)", args.minutes)
        messages = notifier.notify_delays(
            minutes=args.minutes,
            send=not args.dry_run,
        )
        if messages:
            for message in messages:
                LOGGER.info("NOTIFY: %s", message.replace("\n", " | "))
        else:
            LOGGER.info(
                "NOTIFY: задержек не обнаружено (порог %s минут)",
                args.minutes,
            )
    else:
        LOGGER.info("Проверка задержек пропущена (--skip-notifier)")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI обёртка
    raise SystemExit(main())
