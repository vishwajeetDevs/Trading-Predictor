"""PostgreSQL checks. Writes stay in schema bmi_itest and that schema is removed afterward."""

import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.core.timeutil import from_epoch_seconds
from app.data.normalizer import draft_from_ohlc
from app.database.models import Candle
from app.database.repositories import MarketRepository
from app.database.session import describe_db_error, make_engine
from app.main import create_app
from tests.conftest import btc_product

pytestmark = pytest.mark.postgres

SCHEMA = "bmi_itest"


def _attempt(action, attempts: int = 5):
    last = None
    for attempt in range(attempts):
        try:
            return action()
        except OperationalError as exc:
            last = exc
            if "getaddrinfo" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(2)
    raise last


def _connect(engine, attempts: int = 5):
    last = None
    for attempt in range(attempts):
        try:
            return engine.connect()
        except OperationalError as exc:
            last = exc
            if "getaddrinfo" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(2)
    raise last


def _url() -> str:
    if os.environ.get("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests")
    url = get_settings().database_url
    if not url.startswith("postgresql"):
        pytest.skip("DATABASE_URL is not a PostgreSQL URL")
    return url


def _draft(epoch: int):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="100",
        high="110",
        low="90",
        close="105",
        volume="2",
        source="delta_rest",
        as_of=opened + timedelta(seconds=120),
    )


@pytest.fixture(scope="module")
def public_candle_count():
    url = _url()
    engine = make_engine(url)
    try:
        with _connect(engine) as connection:
            before = connection.execute(text("select count(*) from public.candles")).scalar()
        yield int(before)
        with _connect(engine) as connection:
            after = connection.execute(text("select count(*) from public.candles")).scalar()
        assert int(after) == int(before)
    finally:
        engine.dispose()


def test_public_database_is_reachable_and_constrained(public_candle_count):
    engine = make_engine(_url())
    try:
        with _connect(engine) as connection:
            assert connection.execute(text("select 1")).scalar() == 1
            version = connection.execute(text("select version_num from public.alembic_version")).scalar()
            assert version in {"0001_initial", "0002_analysis", "0003_sampling", "0004_derivatives", "0005_verdict_runs"}
            tables = {row[0] for row in connection.execute(text(
                "select table_name from information_schema.tables where table_schema = 'public'"
            ))}
            assert {"products", "candles", "ingestion_state", "websocket_events", "data_quality_events"} <= tables
            if version in {"0002_analysis", "0003_sampling", "0004_derivatives", "0005_verdict_runs"}:
                assert {"study_runs", "condition_matches", "horizon_stats", "analysis_snapshots"} <= tables
            slot = connection.execute(text(
                "select 1 from pg_constraint where conname = 'uq_candle_slot' and connamespace = 'public'::regnamespace"
            )).scalar()
            assert slot == 1
            symbol = connection.execute(text("select symbol from public.products")).scalar()
            assert symbol == "BTCUSD"
        assert public_candle_count > 1000
        engine.dispose()
        with _connect(engine) as connection:
            assert connection.execute(text("select current_database()")).scalar()
    finally:
        engine.dispose()


def test_failed_connection_does_not_reveal_credentials():
    _url()
    engine = make_engine("postgresql+psycopg://owner:secret@127.0.0.1:1/none")
    with pytest.raises(OperationalError) as caught:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    message = describe_db_error(caught.value)
    assert "secret" not in message
    assert "postgresql+psycopg://" not in message
    engine.dispose()


def test_isolated_schema_migration_upsert_and_duplicate_constraint(public_candle_count):
    url = _url()

    def reset_schema() -> None:
        admin = make_engine(url)
        try:
            with admin.begin() as connection:
                connection.execute(text("DROP SCHEMA IF EXISTS %s CASCADE" % SCHEMA))
                connection.execute(text("CREATE SCHEMA %s" % SCHEMA))
        finally:
            admin.dispose()

    _attempt(reset_schema)

    os.environ["BMI_MIGRATION_SCHEMA"] = SCHEMA
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    engine = make_engine(url)

    try:
        _attempt(lambda: command.upgrade(config, "head"))
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        session = factory()

        @event.listens_for(session, "after_begin")
        def _search_path(_session, _transaction, connection):
            connection.exec_driver_sql("SET LOCAL search_path TO %s" % SCHEMA)
        repo = MarketRepository(session)
        product = repo.upsert_product(btc_product())
        again = repo.upsert_product(btc_product())
        assert again.id == product.id
        epoch = 1_699_999_800
        first = repo.upsert_candles(product.id, [_draft(epoch)])
        second = repo.upsert_candles(product.id, [_draft(epoch)])
        session.commit()
        assert first.inserted == 1
        assert second.unchanged == 1
        stored = session.scalar(text("select count(*) from candles"))
        assert int(stored) == 1
        repo.update_state(product.id, "1m", "backfill", oldest=from_epoch_seconds(epoch), newest=from_epoch_seconds(epoch), status="ok")
        session.commit()
        existing = session.scalar(select(Candle))
        session.add(
            Candle(
                product_id=existing.product_id,
                symbol=existing.symbol,
                timeframe=existing.timeframe,
                open_time=existing.open_time,
                open=existing.open,
                high=existing.high,
                low=existing.low,
                close=existing.close,
                volume=existing.volume,
                source="delta_rest",
                is_closed=True,
                exchange_time_raw=existing.exchange_time_raw,
                timestamp_unit="s",
                received_at=existing.received_at,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.close()
        engine.dispose()
        with _connect(engine) as connection:
            assert connection.execute(text("select count(*) from %s.candles" % SCHEMA)).scalar() == 1
    finally:
        os.environ.pop("BMI_MIGRATION_SCHEMA", None)
        engine.dispose()

        def drop_schema() -> None:
            cleanup = make_engine(url)
            try:
                with cleanup.begin() as connection:
                    connection.execute(text("DROP SCHEMA IF EXISTS %s CASCADE" % SCHEMA))
            finally:
                cleanup.dispose()

        _attempt(drop_schema)
    assert public_candle_count > 1000


def test_fastapi_reads_postgres_records(public_candle_count):
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = create_app(settings, factory, engine=None, start_socket=False)
    try:
        with TestClient(app) as client:
            health = client.get("/health")
            product = client.get("/products/btc")
            candles = client.get("/candles", params={"timeframe": "1m", "limit": 3})
            status = client.get("/data/status")
        assert health.status_code == 200
        assert health.json()["database"]["connected"] is True
        assert product.status_code == 200
        body = product.json()
        assert body["symbol"] == "BTCUSD"
        assert body["exchange_product_id"] == 27
        assert "leverage" not in body
        payload = candles.json()
        assert payload["count"] == 3
        assert payload["candles"][0]["symbol"] == "BTCUSD"
        assert status.json()["product"]["symbol"] == "BTCUSD"
        assert public_candle_count > 1000
    finally:
        engine.dispose()
