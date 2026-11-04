from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select

from app.core.timeutil import from_epoch_seconds, utcnow
from app.data.normalizer import draft_from_ohlc
from app.database.models import Candle
from app.database.repositories import MarketRepository
from tests.conftest import btc_product


def _draft(epoch: int, *, close: str = "10", source: str = "delta_rest", closed: bool = True):
    opened = from_epoch_seconds(epoch)
    return draft_from_ohlc(
        symbol="BTCUSD",
        timeframe="1m",
        open_time=opened,
        timestamp_unit="s",
        exchange_time_raw=str(epoch),
        open_="10",
        high="12",
        low="9",
        close=close,
        volume="1.5",
        source=source,
        as_of=opened + timedelta(seconds=120 if closed else 10),
    )


def test_repository_dedupes_and_preserves_native_candles(session):
    repo = MarketRepository(session)
    product = repo.upsert_product(btc_product())
    assert not any(word in key.lower() for key in product.raw_metadata for word in ("leverage", "margin", "wallet", "position"))
    epoch = 1_699_999_800
    first = repo.upsert_candles(product.id, [_draft(epoch, closed=False)])
    second = repo.upsert_candles(product.id, [_draft(epoch, closed=False)])
    closed = repo.upsert_candles(product.id, [_draft(epoch, closed=True)])
    assert (first.inserted, second.unchanged, closed.updated) == (1, 1, 1)
    assert session.scalar(select(func.count()).select_from(Candle)) == 1
    changed = repo.upsert_candles(product.id, [_draft(epoch, close="11")])
    assert changed.updated == 1
    aggregate = repo.upsert_candles(product.id, [_draft(epoch, close="11.5", source="aggregated_1m")])
    assert aggregate.conflicts
    assert aggregate.updated == 0
    stored = session.scalar(select(Candle))
    assert Decimal(str(stored.close)) == Decimal("11")
    assert stored.source == "delta_rest"
    assert stored.is_closed is True
    repo.add_quality("missing_candle", {"count": 1}, product_id=product.id, timeframe="1m")
    repo.update_state(product.id, "1m", "backfill", oldest=stored.open_time, newest=stored.open_time, status="ok")
    session.commit()
    assert repo.quality_counts(utcnow() - timedelta(hours=1))["missing_candle"] == 1


def test_db_error_text_removes_credentials():
    from app.database.session import describe_db_error

    message = describe_db_error(Exception("connect failed postgresql+psycopg://owner:secret@db.example/neondb ssl"))
    assert "secret" not in message
    assert "owner" not in message
    assert "postgresql://***" in message
    resolved = describe_db_error(Exception("failed to resolve host 'db.example.neon.tech': [Errno 11001] getaddrinfo failed"))
    assert "neon.tech" not in resolved
    assert resolved == "Database host could not be resolved."


def test_alembic_upgrade_creates_the_market_tables(tmp_path):
    database = tmp_path / "mig.db"
    url = "sqlite:///" + database.as_posix()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    names = set(inspect(create_engine(url)).get_table_names())
    assert {"products", "candles", "websocket_events", "ingestion_state", "data_quality_events"} <= names
