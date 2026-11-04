"""Parse Delta public payloads into candle drafts. Invalid bars are rejected, never filled in."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.timeutil import RESOLUTIONS, as_utc, epoch_to_utc, is_aligned, to_decimal


class CandleRejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CandleDraft:
    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str
    is_closed: bool
    exchange_time_raw: str
    timestamp_unit: str
    received_at: datetime
    aligned: bool


def loads_decimal(raw: str | bytes) -> Any:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return json.loads(text, parse_float=Decimal)


def _closed(open_time: datetime, step: int, as_of: datetime) -> bool:
    return as_utc(as_of) >= as_utc(open_time) + timedelta(seconds=step)


def validate_ohlc(open_: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal) -> None:
    if min(open_, high, low, close) <= 0:
        raise CandleRejected("price must be positive")
    if volume < 0:
        raise CandleRejected("volume is negative")
    if high < low:
        raise CandleRejected("high is below low")
    if high < max(open_, close) or low > min(open_, close):
        raise CandleRejected("open or close is outside the high-low range")


def draft_from_ohlc(
    *,
    symbol: str,
    timeframe: str,
    open_time: datetime,
    timestamp_unit: str,
    exchange_time_raw: str,
    open_: object,
    high: object,
    low: object,
    close: object,
    volume: object,
    source: str,
    as_of: datetime,
) -> CandleDraft:
    if timeframe not in RESOLUTIONS:
        raise CandleRejected("unsupported timeframe %s" % timeframe)
    try:
        prices = [to_decimal(item) for item in (open_, high, low, close, volume)]
    except (InvalidOperation, ValueError) as exc:
        raise CandleRejected("malformed number") from exc
    validate_ohlc(*prices)
    step = RESOLUTIONS[timeframe]
    moment = as_utc(open_time)
    return CandleDraft(
        symbol=symbol,
        timeframe=timeframe,
        open_time=moment,
        open=prices[0],
        high=prices[1],
        low=prices[2],
        close=prices[3],
        volume=prices[4],
        source=source,
        is_closed=_closed(moment, step, as_of),
        exchange_time_raw=exchange_time_raw,
        timestamp_unit=timestamp_unit,
        received_at=as_utc(as_of),
        aligned=is_aligned(moment, step),
    )


def parse_rest_candles(payload: Any, *, symbol: str, timeframe: str, source: str, as_of: datetime) -> tuple[list[CandleDraft], list[str]]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise CandleRejected("Delta candle response was not successful")
    rows = payload.get("result")
    if rows is None:
        raise CandleRejected("Delta candle response has no result")
    if not isinstance(rows, list):
        raise CandleRejected("Delta candle result is not a list")
    drafts: list[CandleDraft] = []
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("row is not an object")
            continue
        if row.get("time") is None or any(row.get(key) is None for key in ("open", "high", "low", "close", "volume")):
            errors.append("row is missing OHLC or time")
            continue
        try:
            opened, unit = epoch_to_utc(int(row["time"]))
            drafts.append(
                draft_from_ohlc(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=opened,
                    timestamp_unit=unit,
                    exchange_time_raw=str(row["time"]),
                    open_=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    source=source,
                    as_of=as_of,
                )
            )
        except (CandleRejected, TypeError, ValueError) as exc:
            errors.append(str(exc))
    drafts.sort(key=lambda item: item.open_time)
    return drafts, errors


def parse_ws_candlestick(message: dict, *, as_of: datetime) -> CandleDraft:
    if "cst" not in message:
        raise CandleRejected("candlestick has no cst candle-start field")
    raw_type = str(message.get("type") or "")
    resolution = message.get("res")
    if not resolution and raw_type.startswith("candlestick_"):
        resolution = raw_type.split("_", 1)[1]
    opened, unit = epoch_to_utc(int(message["cst"]))
    symbol = str(message.get("sy") or "")
    if symbol.startswith("MARK:"):
        raise CandleRejected("mark-price candlestick is not a traded-price candle")
    return draft_from_ohlc(
        symbol=symbol,
        timeframe=str(resolution),
        open_time=opened,
        timestamp_unit=unit,
        exchange_time_raw=str(message["cst"]),
        open_=message.get("o"),
        high=message.get("h"),
        low=message.get("l"),
        close=message.get("c"),
        volume=message.get("v"),
        source="delta_ws",
        as_of=as_of,
    )
