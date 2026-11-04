from sqlalchemy import func, select

from app.core.timeutil import from_epoch_seconds
from app.database.models import Candle, DataQualityEvent
from app.database.repositories import MarketRepository
from app.services.backfill_service import BackfillService
from tests.conftest import btc_product, make_settings

BASE = 1_699_999_800


def _rows(start: int, end: int, step: int = 60):
    rows = []
    cursor = start
    while cursor <= end:
        rows.append({"time": cursor, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1})
        cursor += step
    return list(reversed(rows))


class Catalogue:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def iter_products(self):
        yield btc_product()

    def fetch_candles(self, symbol, resolution, start, end):
        self.calls.append((resolution, start, end))
        step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[resolution]
        return {"success": True, "result": _rows(start, end, step)}


class Truncated(Catalogue):
    def fetch_candles(self, symbol, resolution, start, end):
        self.calls.append((resolution, start, end))
        rows = list(reversed(_rows(start, end, 60)))
        if len(rows) > 2:
            rows = rows[-2:]
        return {"success": True, "result": list(reversed(rows))}


def _count(session) -> int:
    return session.scalar(select(func.count()).select_from(Candle))


def test_backfill_resumes_and_does_not_duplicate(session):
    product = MarketRepository(session).upsert_product(btc_product())
    session.commit()
    client = Catalogue(None)
    service = BackfillService(client, make_settings(candle_chunk_size=3))
    as_of = from_epoch_seconds(BASE + 7200)
    first = service.fill_range(session, product, "1m", BASE, BASE + 360, as_of)
    assert first["requests"] == 2
    assert _count(session) == 6
    second = service.fill_range(session, product, "1m", BASE, BASE + 360, as_of)
    assert second["requests"] == 0
    assert _count(session) == 6
    older = service.fill_range(session, product, "1m", BASE - 180, BASE + 360, as_of)
    assert older["requests"] == 1
    assert _count(session) == 9
    assert client.calls[-1][1] == BASE - 180


def test_truncated_page_is_fetched_again_instead_of_dropped(session):
    product = MarketRepository(session).upsert_product(btc_product())
    session.commit()
    client = Truncated(None)
    service = BackfillService(client, make_settings(candle_chunk_size=100, max_candle_page=2))
    as_of = from_epoch_seconds(BASE + 7200)
    service.fill_range(session, product, "1m", BASE, BASE + 360, as_of)
    assert len(client.calls) > 1
    assert _count(session) == 6
    kinds = session.scalars(select(DataQualityEvent.kind)).all()
    assert "truncated_page" in kinds
