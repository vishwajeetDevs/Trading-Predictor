"""Shared time and decimal helpers. Timestamp units are decided per payload, not assumed."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

RESOLUTIONS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def epoch_to_utc(value: int) -> tuple[datetime, str]:
    """Convert an integer epoch to UTC and name the unit.

    Verified against Delta on 2026-11-03:
    REST candle ``time`` is about 1.79e9 and lands on a minute boundary (seconds).
    WebSocket ``cst`` and ``ts`` are about 1.79e15 (microseconds). ``cst / 1e6``
    equals the REST candle open. ``ts`` is later in the same minute, so it is
    the publish time, not the open.
    """
    raw = int(value)
    if raw > 10**14:
        seconds, micros = divmod(raw, 1_000_000)
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)
        return moment, "us"
    if raw > 10**11:
        seconds, millis = divmod(raw, 1_000)
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=millis * 1_000)
        return moment, "ms"
    return datetime.fromtimestamp(raw, tz=timezone.utc), "s"


def floor_epoch(moment: datetime, step: int) -> int:
    return int(as_utc(moment).timestamp()) // step * step


def from_epoch_seconds(seconds: int) -> datetime:
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, bool) or value is None:
        raise InvalidOperation("missing number")
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Binary floats are only accepted after an explicit str() of the token.
        # Callers that parsed JSON should pass Decimal already.
        number = Decimal(str(value))
    else:
        number = Decimal(str(value).strip())
    if not number.is_finite():
        raise InvalidOperation("non-finite number")
    return number


def is_aligned(moment: datetime, step: int) -> bool:
    return int(as_utc(moment).timestamp()) % step == 0
