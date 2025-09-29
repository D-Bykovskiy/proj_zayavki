"""Utilities for generating a reproducible OMIS server bootstrap plan."""
from __future__ import annotations

import argparse
import textwrap
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence


@dataclass
class ServerSetupPlan:
    """Structured representation of the instructions required to bootstrap OMIS."""

    target: str
    commands: List[str]
    config_files: List[tuple[str, str]]
    post_checks: List[str]


# Core runtime dependencies that are required regardless of deployment target.
CORE_PYTHON_PACKAGES = (
    "flask",
    "requests",
    "python-dotenv",
    "exchangelib",
)


def _systemd_unit(service_name: str, user: str, workdir: str, venv_path: str) -> str:
    """Render a minimal systemd unit for running the OMIS runner on boot."""

    return textwrap.dedent(
        f"""
        [Unit]
        Description=OMIS automation runner
        Wants=network-online.target
        After=network-online.target

        [Service]
        Type=simple
        User={user}
        WorkingDirectory={workdir}
        Environment=PATH={venv_path}/bin
        ExecStart={venv_path}/bin/python -m project_package.runner --log-level INFO
        Restart=on-failure
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
        """
    ).strip()


def _normalize_project_dir(raw: str) -> tuple[str, str]:
    """Return normalized POSIX-style project dir and its parent."""

    normalized = raw.rstrip("/") or "/opt/omis"
    if not normalized.startswith("/"):
        raise ValueError("project_dir must be an absolute POSIX-style path")
    parent, _, _ = normalized.rpartition("/")
    parent = parent or "/"
    return normalized, parent


def prepare_server(
    target: str,
    *,
    python_version: str = "3.11",
    project_dir: str = "/opt/omis",
    service_user: str = "omis",
    service_name: str = "omis-runner",
    use_nginx: bool = True,
    additional_packages: Optional[Iterable[str]] = None,
    extra_pip: Optional[Iterable[str]] = None,
) -> ServerSetupPlan:
    """Generate command list and config files for the requested server type."""

    normalized_target = target.lower()
    if normalized_target not in {"virtual", "production", "baremetal", "real"}:
        raise ValueError(
            "target must be one of {'virtual', 'production', 'baremetal', 'real'}"
        )

    # Map human friendly shortcuts to the two major branches we support.
    branch = "virtual" if normalized_target == "virtual" else "production"

    project_dir, parent_dir = _normalize_project_dir(project_dir)

    packages = [
        f"python{python_version}",
        f"python{python_version}-venv",
        "git",
        "pipx",
    ]
    if use_nginx:
        packages.append("nginx")
    packages.extend(list(additional_packages or ()))

    commands: List[str] = []

    commands.append(
        "sudo apt-get update && sudo apt-get install -y " + " ".join(packages)
    )
    commands.append(
        f"sudo useradd --system --create-home --shell /bin/bash {service_user} || true"
    )
    commands.append(f"sudo mkdir -p {parent_dir}")
    clone_cmd = (
        "if [ ! -d {dir} ]; then sudo git clone https://github.com/talyu/omis.git {dir}; "
        "else sudo git -C {dir} pull --ff-only; fi"
    ).format(dir=project_dir)
    commands.append(clone_cmd)
    commands.append(f"sudo chown -R {service_user}:{service_user} {project_dir}")
    commands.append(
        f"sudo -u {service_user} python{python_version} -m venv {project_dir}/.venv"
    )
    commands.append(
        f"sudo -u {service_user} {project_dir}/.venv/bin/pip install --upgrade pip"
    )

    pip_packages = sorted(set(CORE_PYTHON_PACKAGES).union(extra_pip or ()))
    pip_cmd = "sudo -u {user} {venv}/bin/pip install {packages}".format(
        user=service_user,
        venv=f"{project_dir}/.venv",
        packages=" ".join(pip_packages),
    )
    commands.append(pip_cmd)

    # Run a smoke test depending on the profile so the operator immediately sees
    # whether the freshly configured server can reach the mail/notification code.
    if branch == "virtual":
        commands.append(
            "sudo -u {user} {venv}/bin/python -m project_package.project.mail_checker --fake".format(
                user=service_user,
                venv=f"{project_dir}/.venv",
            )
        )
    else:
        commands.append(
            f"sudo -u {service_user} {project_dir}/.venv/bin/python -m project_package.project.mail_checker --log-level INFO"
        )

    unit_path = f"/etc/systemd/system/{service_name}.service"
    config_files = [
        (
            unit_path,
            _systemd_unit(
                service_name=service_name,
                user=service_user,
                workdir=project_dir,
                venv_path=f"{project_dir}/.venv",
            ),
        )
    ]

    if use_nginx:
        nginx_conf = textwrap.dedent(
            f"""
            server {{
                listen 80;
                server_name _;

                location / {{
                    proxy_pass http://127.0.0.1:5000;
                    proxy_set_header Host $host;
                    proxy_set_header X-Real-IP $remote_addr;
                    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                    proxy_set_header X-Forwarded-Proto $scheme;
                }}
            }}
            """
        ).strip()
        config_files.append((f"/etc/nginx/sites-available/{service_name}.conf", nginx_conf))
        commands.extend(
            [
                f"sudo ln -sf /etc/nginx/sites-available/{service_name}.conf /etc/nginx/sites-enabled/{service_name}.conf",
                "sudo nginx -t",
                "sudo systemctl reload nginx",
            ]
        )

    commands.extend(
        [
            "sudo systemctl daemon-reload",
            f"sudo systemctl enable {service_name}",
            f"sudo systemctl start {service_name}",
        ]
    )

    post_checks = [
        f"sudo systemctl status {service_name}",
        f"sudo journalctl -u {service_name} -n 50",
        "curl -f http://127.0.0.1:5000/",  # health check for the Flask app
    ]
    if use_nginx:
        post_checks.append("curl -I http://localhost/")

    return ServerSetupPlan(
        target=branch,
        commands=commands,
        config_files=config_files,
        post_checks=post_checks,
    )


def _format_plan(plan: ServerSetupPlan) -> str:
    """Render the plan as human friendly text for CLI output."""

    lines: List[str] = []
    lines.append(f"Target profile: {plan.target}")
    lines.append("\nCommands:")
    for item in plan.commands:
        lines.append(f"  - {item}")
    lines.append("\nConfig files:")
    for path, content in plan.config_files:
        lines.append(f"  - {path}:")
        indented = textwrap.indent(content, prefix="      ")
        lines.append(indented)
    lines.append("\nPost-deployment checks:")
    for check in plan.post_checks:
        lines.append(f"  - {check}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint that prints the generated bootstrap plan."""

    parser = argparse.ArgumentParser(
        description="Generate step-by-step instructions for deploying OMIS on a server.",
    )
    parser.add_argument(
        "target",
        choices=["virtual", "production", "baremetal", "real"],
        help="Deployment profile: virtual sandbox or real/baremetal server.",
    )
    parser.add_argument(
        "--python-version",
        default="3.11",
        help="Python version to install (default: 3.11)",
    )
    parser.add_argument(
        "--project-dir",
        default="/opt/omis",
        help="Directory where the project will be placed on the server.",
    )
    parser.add_argument(
        "--service-user",
        default="omis",
        help="System user that will own the deployment.",
    )
    parser.add_argument(
        "--service-name",
        default="omis-runner",
        help="Name of the systemd service to generate.",
    )
    parser.add_argument(
        "--no-nginx",
        action="store_true",
        help="Skip generation of Nginx configuration and related commands.",
    )
    parser.add_argument(
        "--extra-package",
        action="append",
        default=[],
        help="Additional apt packages to install (can be passed multiple times).",
    )
    parser.add_argument(
        "--extra-pip",
        action="append",
        default=[],
        help="Additional pip packages to install inside the virtualenv.",
    )
    args = parser.parse_args(argv)

    plan = prepare_server(
        target=args.target,
        python_version=args.python_version,
        project_dir=args.project_dir,
        service_user=args.service_user,
        service_name=args.service_name,
        use_nginx=not args.no_nginx,
        additional_packages=args.extra_package,
        extra_pip=args.extra_pip,
    )

    print(_format_plan(plan))
    return 0


__all__ = [
    "ServerSetupPlan",
    "prepare_server",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    raise SystemExit(main())
