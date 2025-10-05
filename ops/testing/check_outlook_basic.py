"""Connectivity probe for Outlook using exchangelib without Azure OAuth."""
from __future__ import annotations

import argparse
import getpass
import logging
import os
from typing import Optional

from dotenv import load_dotenv


def _load_env(env_path: Optional[str]) -> None:
    """Load variables from .env if provided explicitly or discoverable."""
    if env_path:
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Attempt to connect to an Outlook mailbox using exchangelib Credentials.\n"
            "This checks the legacy username/password flow so you can see if it still works"
            " before wiring it into the main project."
        ),
    )
    parser.add_argument(
        "--env",
        help="Path to a .env file with OMIS_OUTLOOK_* variables (optional).",
    )
    parser.add_argument(
        "--email",
        help="Mailbox email address. Defaults to OMIS_OUTLOOK_EMAIL variable.",
    )
    parser.add_argument(
        "--password",
        help=(
            "Password for the mailbox. If omitted, OMIS_OUTLOOK_LEGACY_PASSWORD env value "
            "is used or you will be prompted interactively."
        ),
    )
    parser.add_argument(
        "--server",
        help=(
            "Optional explicit EWS server hostname. If omitted autodiscover is used."
        ),
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=5,
        help="How many latest messages to fetch for verification (default: 5).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity for exchangelib internals (default: INFO).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env(args.env)

    logging.basicConfig(level=getattr(logging, args.log_level))

    try:
        from exchangelib import Account, Configuration, Credentials, DELEGATE
    except ImportError as exc:  # pragma: no cover - dependency check
        logging.error("exchangelib is not installed: %s", exc)
        return 1

    email = args.email or os.getenv("OMIS_OUTLOOK_EMAIL", "").strip()
    if not email:
        email = input("Enter Outlook email address: ").strip()

    password = (
        args.password
        or os.getenv("OMIS_OUTLOOK_LEGACY_PASSWORD")
        or os.getenv("OMIS_OUTLOOK_PASSWORD")
    )
    if not password:
        password = getpass.getpass("Enter password for %s: " % email)

    if not email or not password:
        logging.error("Email and password are required for the legacy flow.")
        return 2

    credentials = Credentials(username=email, password=password)

    configuration = None
    if args.server:
        configuration = Configuration(server=args.server, credentials=credentials)

    try:
        account = Account(
            primary_smtp_address=email,
            credentials=credentials,
            autodiscover=configuration is None,
            config=configuration,
            access_type=DELEGATE,
        )
    except Exception as exc:  # pragma: no cover - network/auth failures
        logging.exception("Failed to open mailbox via legacy auth: %s", exc)
        return 3

    try:
        inbox = account.inbox
        items = list(inbox.all().order_by("-datetime_received")[: args.max_messages])
    except Exception as exc:  # pragma: no cover - query failures
        logging.exception("Connected but failed to fetch messages: %s", exc)
        return 4

    if not items:
        print("Connected successfully, but no messages were returned from the mailbox.")
    else:
        print("Connected successfully. Showing the latest %s message(s):" % len(items))
        for idx, item in enumerate(items, start=1):
            subject = getattr(item, "subject", "(no subject)")
            received = getattr(item, "datetime_received", None)
            if received is not None:
                received = received.isoformat()
            print("%s. %s -- %s" % (idx, subject, received))

    return 0


if __name__ == "__main__":  # pragma: no cover - manual probe
    raise SystemExit(main())
