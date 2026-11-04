"""Command line entry points. Run from the backend directory."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn
from sqlalchemy.exc import OperationalError

from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.core.timeutil import utcnow
from app.data.delta_rest import DeltaRestClient
from app.database.session import describe_db_error, session_factory_from_settings
from app.services.backfill_service import BackfillService
from app.services.ingestion_service import run_public_socket
from app.services.study_service import run_studies


def _migrate(database_url: str) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _call(action) -> None:
    try:
        action()
    except OperationalError as exc:
        print("database connection failed: %s" % describe_db_error(exc), file=sys.stderr)
        raise SystemExit(1) from None


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(description="BTC Market Intelligence data tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--days", type=int, default=None)
    sub.add_parser("ws")
    sub.add_parser("study")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.command == "migrate":
        _call(lambda: _migrate(settings.database_url))
        print("migrations applied")
        return
    if args.command == "serve":
        uvicorn.run("app.main:app", host=args.host, port=args.port, factory=False)
        return
    engine, factory = session_factory_from_settings(settings)
    if args.command == "backfill":

        def run_backfill() -> None:
            _migrate(settings.database_url)
            session = factory()
            try:
                with DeltaRestClient(
                    settings.delta_rest_base,
                    settings.user_agent,
                    timeout=settings.http_timeout_seconds,
                    min_interval=settings.request_min_interval_seconds,
                    max_retries=settings.max_retries,
                ) as client:
                    summary = BackfillService(client, settings).backfill(session, days=args.days, as_of=utcnow())
                print(json.dumps(summary, default=str))
            finally:
                session.close()
                engine.dispose()

        _call(run_backfill)
        return
    if args.command == "ws":

        def run_socket() -> None:
            _migrate(settings.database_url)
            stop = asyncio.Event()
            try:
                asyncio.run(run_public_socket(settings, factory, stop))
            except KeyboardInterrupt:
                stop.set()
            finally:
                engine.dispose()

        _call(run_socket)
        return
    if args.command == "study":

        def run_study() -> None:
            _migrate(settings.database_url)
            session = factory()
            try:
                print(json.dumps(run_studies(session), default=str))
            finally:
                session.close()
                engine.dispose()

        _call(run_study)


if __name__ == "__main__":
    main()
