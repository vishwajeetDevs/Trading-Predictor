"""Live tape view: the forming bar for a timeframe, built from stored closed minutes plus the socket's forming minute.

Nothing here is invented. If the socket has not delivered a minute, the forming bar is built from
stored closed minutes only, and the response says how old the newest input is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import RESOLUTIONS, as_utc, utcnow
from app.database.models import Candle
from app.database.repositories import MarketRepository

LIVE_STALE_SECONDS = 30.0


def bucket_open(moment: datetime, timeframe: str) -> datetime:
    step = RESOLUTIONS[timeframe]
    epoch = int(as_utc(moment).timestamp())
    return datetime.fromtimestamp(epoch // step * step, tz=timezone.utc)


def _text(value) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f").rstrip("0").rstrip(".")
    return str(value)


def _aggregate(rows: list[dict], timeframe: str, opened: datetime) -> dict | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda item: item["open_time"])
    return {
        "timeframe": timeframe,
        "open_time": opened.isoformat(),
        "open": _text(rows[0]["open"]),
        "high": _text(max(item["high"] for item in rows)),
        "low": _text(min(item["low"] for item in rows)),
        "close": _text(rows[-1]["close"]),
        "volume": _text(sum((item["volume"] for item in rows), Decimal("0"))),
        "minutes_included": len(rows),
        "is_closed": False,
        "source": "stored_closed_minutes" if all(item["source"] != "delta_ws_forming" for item in rows) else "stored_closed_minutes_plus_ws_forming_minute",
    }


def live_view(session: Session, runtime, timeframe: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    product = MarketRepository(session).get_product()
    if product is None:
        return {"read_only": True, "symbol": None, "timeframe": timeframe, "availability": "NO_PRODUCT", "forming_candle": None, "last_price": None, "warnings": ["No product is stored."]}
    opened = bucket_open(now, timeframe)
    warnings: list[str] = []
    rows: list[dict] = []
    stmt = (
        select(Candle.open_time, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume, Candle.source)
        .where(Candle.product_id == product.id, Candle.timeframe == "1m", Candle.is_closed.is_(True), Candle.open_time >= opened)
        .order_by(Candle.open_time.asc())
    )
    for open_time, o, h, l, c, v, source in session.execute(stmt).all():
        rows.append({"open_time": as_utc(open_time), "open": Decimal(str(o)), "high": Decimal(str(h)), "low": Decimal(str(l)), "close": Decimal(str(c)), "volume": Decimal(str(v)), "source": source})
    forming = getattr(runtime, "forming_candle", None)
    forming_at = getattr(runtime, "forming_candle_at", None)
    last_price = None
    last_price_at = None
    last_price_source = None
    if forming:
        forming_open = datetime.fromisoformat(forming["open_time"])
        if forming_open >= opened and all(item["open_time"] != forming_open for item in rows):
            rows.append({"open_time": forming_open, "open": Decimal(forming["open"]), "high": Decimal(forming["high"]), "low": Decimal(forming["low"]), "close": Decimal(forming["close"]), "volume": Decimal(forming["volume"]), "source": "delta_ws_forming"})
        last_price = forming["close"]
        last_price_at = None if forming_at is None else as_utc(forming_at).isoformat()
        last_price_source = "delta_ws_candlestick_1m_forming_close"
    elif rows:
        last_price = _text(rows[-1]["close"])
        last_price_at = (rows[-1]["open_time"] + timedelta(seconds=60)).isoformat()
        last_price_source = "stored_closed_1m_close"
        warnings.append("No forming minute from the socket; the last price is the newest stored closed-minute close.")
    bar = _aggregate(rows, timeframe, opened)
    if bar is None:
        warnings.append("No closed minute has been stored inside the current %s bucket yet." % timeframe)
    newest_input = None
    if forming_at is not None:
        newest_input = as_utc(forming_at)
    elif rows:
        newest_input = rows[-1]["open_time"] + timedelta(seconds=60)
    age = None if newest_input is None else (now - newest_input).total_seconds()
    mark_at = getattr(runtime, "mark_price_at", None)
    return {
        "read_only": True,
        "symbol": product.symbol,
        "timeframe": timeframe,
        "bucket_open": opened.isoformat(),
        "availability": "AVAILABLE" if bar is not None or last_price is not None else "NOT_AVAILABLE",
        "websocket_connected": bool(getattr(runtime, "ws_connected", False)),
        "last_price": last_price,
        "last_price_at": last_price_at,
        "last_price_source": last_price_source,
        "mark_price": getattr(runtime, "mark_price", None),
        "mark_price_at": None if mark_at is None else as_utc(mark_at).isoformat(),
        "forming_candle": bar,
        "age_seconds": None if age is None else round(age, 3),
        "stale": age is None or age > LIVE_STALE_SECONDS,
        "stale_after_seconds": LIVE_STALE_SECONDS,
        "note": "The forming bar is built from stored closed minutes plus the socket's forming minute. It is not a closed candle and will change until the bucket closes.",
        "warnings": warnings,
    }
