"""Run Tern using PostgreSQL settings from the environment or project .env file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
TERN_CONFIG = ROOT / "gateway" / "tern.conf"
MIGRATIONS_DIR = ROOT / "gateway" / "db" / "migrations"


def configure_database_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        parsed = urlsplit(database_url)
        if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")
        environment = {
            "PGHOST": parsed.hostname,
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": unquote(parsed.username or os.getenv("PGUSER", "")),
            "PGPASSWORD": unquote(parsed.password or os.getenv("PGPASSWORD", "")),
            "PGDATABASE": unquote(parsed.path.lstrip("/")),
        }
        query = parse_qs(parsed.query)
        if "sslmode" in query:
            environment["PGSSLMODE"] = query["sslmode"][-1]
        for name, value in environment.items():
            os.environ[name] = value
    else:
        aliases = {
            "PGHOST": ("PGHOST", "localhost"),
            "PGPORT": ("PGPORT", "5432"),
            "PGUSER": ("PGUSER", os.getenv("POSTGRES_USER", "omniretrive")),
            "PGPASSWORD": ("PGPASSWORD", os.getenv("POSTGRES_PASSWORD", "")),
            "PGDATABASE": ("PGDATABASE", os.getenv("POSTGRES_DB", "omniretrive")),
            "PGSSLMODE": ("PGSSLMODE", "disable"),
        }
        for key, (source, default) in aliases.items():
            os.environ[key] = os.getenv(source) or default

    if not os.environ.get("PGPASSWORD"):
        raise ValueError(
            "Set PGPASSWORD or POSTGRES_PASSWORD in the environment or .env before migrating."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "status", "new"))
    parser.add_argument("--tern", default="tern", help="Tern executable")
    parser.add_argument("--name", help="Migration name for the new action")
    parser.add_argument(
        "--confirm",
        default="",
        help="Explicit confirmation for destructive migration rollback",
    )
    args = parser.parse_args()

    if args.action == "new":
        if not args.name:
            parser.error("new requires --name")
        command = [
            args.tern,
            "new",
            args.name,
            "-m",
            str(MIGRATIONS_DIR),
        ]
    elif args.action == "down":
        if args.confirm != "yes":
            parser.error(
                "down rolls back the latest migration and may destroy data. "
                "Repeat with --confirm yes after checking its down migration."
            )
        try:
            configure_database_environment()
        except ValueError as exc:
            parser.error(str(exc))
    else:
        try:
            configure_database_environment()
        except ValueError as exc:
            parser.error(str(exc))

    if args.action == "status":
        command = [
            args.tern,
            "status",
            "-c",
            str(TERN_CONFIG),
            "-m",
            str(MIGRATIONS_DIR),
        ]
    elif args.action in ("up", "down"):
        command = [
            args.tern,
            "migrate",
            "-c",
            str(TERN_CONFIG),
            "-m",
            str(MIGRATIONS_DIR),
        ]
        if args.action == "down":
            command.extend(["-d", "-1"])

    try:
        result = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False)
    except FileNotFoundError:
        print(
            f"Tern executable '{args.tern}' was not found. Install Tern or set TERN to its executable path.",
            file=sys.stderr,
        )
        return 127
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
