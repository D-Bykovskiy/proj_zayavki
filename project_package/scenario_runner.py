"""Scenario runner for OMIS functional tests."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .project import database, mail_checker, notifier
from . import runner

LOGGER = logging.getLogger(__name__)

SUPPORTED_ACTIONS = {"add_request", "mail_fake", "notify", "runner"}


def _load_scenarios(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse scenarios file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Scenario file must contain a JSON object with scenarios")
    result: Dict[str, List[Dict[str, Any]]] = {}
    for name, steps in data.items():
        if not isinstance(name, str):
            raise ValueError("Scenario names must be strings")
        if not isinstance(steps, list):
            raise ValueError(f"Scenario '{name}' must be a list of steps")
        result[name] = steps
    return result


def _ensure_known_action(step: Dict[str, Any]) -> str:
    action = step.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError(f"Invalid step action: {step!r}")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"Unsupported action '{action}' in step {step!r}")
    return action


def _execute_add_request(params: Dict[str, Any]) -> str:
    try:
        request_number = str(params["request_number"])
        position_number = str(params["position_number"])
        comment = str(params.get("comment", ""))
        comment_author = str(params.get("comment_author", "Tester"))
    except KeyError as exc:
        raise ValueError(f"Missing required parameter for add_request: {exc}") from exc

    allow_existing = bool(params.get('allow_existing', True))

    try:
        database.add_request(
            request_number=request_number,
            position_number=position_number,
            comment=comment,
            comment_author=comment_author,
        )
        created = True
    except sqlite3.IntegrityError:
        if not allow_existing:
            raise
        LOGGER.info(
            "Request %s/%s already exists — skipping creation",
            request_number,
            position_number,
        )
        created = False

    status = params.get('status')
    if status:
        database.update_status(
            request_number=request_number,
            new_status=str(status),
            position_number=position_number,
        )

    backdate = params.get('backdate_minutes')
    if backdate:
        database.backdate_request(
            request_number=request_number,
            minutes=int(backdate),
            position_number=position_number,
        )

    return (
        f"Added request {request_number}/{position_number}"
        if created
        else f"Request {request_number}/{position_number} already existed"
    )


def _execute_mail_fake(params: Dict[str, Any]) -> str:
    use_fake = bool(params.get("use_fake", True))
    backend = params.get("backend")
    backend_name = str(backend) if backend is not None else None
    results = mail_checker.process_mailbox(use_fake=use_fake, backend=backend_name)
    if not results:
        label = backend_name or ("fake" if use_fake else "auto")
        return f"Mail checker: no messages processed (backend={label})"
    for line in results:
        LOGGER.info("MAIL: %s", line)
    return f"Mail checker processed {len(results)} message(s)"


def _execute_notify(params: Dict[str, Any]) -> str:
    minutes = int(params.get("minutes", 60))
    dry_run = bool(params.get("dry_run", False))
    messages = notifier.notify_delays(minutes=minutes, send=not dry_run)
    if not messages:
        return f"Notifier: no delays (threshold {minutes} minutes)"
    for message in messages:
        LOGGER.info("NOTIFY: %s", message.replace("\n", " | "))
    return f"Notifier prepared {len(messages)} message(s)"


def _execute_runner(params: Dict[str, Any]) -> str:
    argv: List[str] = []
    if params.get("fake_mail"):
        argv.append("--fake-mail")
    if params.get("dry_run"):
        argv.append("--dry-run")
    if "minutes" in params:
        argv.extend(["--minutes", str(params["minutes"])])
    mail_backend = params.get("mail_backend")
    if mail_backend:
        argv.extend(["--mail-backend", str(mail_backend)])
    if params.get("skip_mail"):
        argv.append("--skip-mail")
    if params.get("skip_notifier"):
        argv.append("--skip-notifier")
    runner.main(argv)
    return "Runner completed"


def execute_step(step: Dict[str, Any]) -> str:
    action = _ensure_known_action(step)
    params = step.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"Parameters for action '{action}' must be an object")

    LOGGER.info("Executing step: %s", action)
    if action == "add_request":
        return _execute_add_request(params)
    if action == "mail_fake":
        return _execute_mail_fake(params)
    if action == "notify":
        return _execute_notify(params)
    if action == "runner":
        return _execute_runner(params)
    raise ValueError(f"Unhandled action '{action}'")


def run_scenario(steps: List[Dict[str, Any]]) -> List[str]:
    outputs: List[str] = []
    for idx, step in enumerate(steps, start=1):
        try:
            message = execute_step(step)
            outputs.append(f"Step {idx}: {message}")
        except Exception as exc:
            LOGGER.exception("Failed on step %s: %s", idx, exc)
            raise
    return outputs


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run predefined OMIS scenarios (database + mail + notifier).",
    )
    parser.add_argument(
        "--scenario",
        help="name of the scenario to run",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=Path("ops/testing/scenarios.json"),
        help="path to JSON file with scenarios definitions",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list available scenarios and exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="logging level",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))

    scenarios = _load_scenarios(args.file)
    if args.list:
        for name in sorted(scenarios):
            print(name)
        return 0

    if not args.scenario:
        parser.error("--scenario is required unless --list is specified")

    steps = scenarios.get(args.scenario)
    if steps is None:
        parser.error(f"Scenario '{args.scenario}' not found in {args.file}")

    LOGGER.info("Running scenario '%s' from %s", args.scenario, args.file)
    outputs = run_scenario(steps)
    for line in outputs:
        print(line)
    LOGGER.info("Scenario '%s' completed", args.scenario)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
