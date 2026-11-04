"""Public WebSocket protocol for Delta India. One connection, heartbeat, and bounded reconnects."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.core.timeutil import as_utc, epoch_to_utc
from app.data.candle_builder import FormingBook
from app.data.delta_rest import backoff_seconds
from app.data.normalizer import CandleDraft, CandleRejected, parse_ws_candlestick


@dataclass
class ParsedEvent:
    kind: str
    candles: list[CandleDraft] = field(default_factory=list)
    sequence: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class SubscriptionBook:
    sent: set[str] = field(default_factory=set)

    def message_for(self, symbol: str) -> dict | None:
        channels = []
        for name, symbols in (
            ("candlestick_1m", (symbol,)),
            ("ticker", (symbol,)),
            ("mark_price", ("MARK:" + symbol,)),
            ("funding_rate", (symbol,)),
            ("ob_updates", (symbol,)),
            ("system_status", ()),
        ):
            key = name + ":" + ",".join(symbols)
            if key in self.sent:
                continue
            self.sent.add(key)
            item = {"name": name}
            if symbols:
                item["symbols"] = list(symbols)
            channels.append(item)
        if not channels:
            return None
        return {"type": "subscribe", "payload": {"channels": channels}}

    def resubscribe(self, name: str, symbols: tuple[str, ...]) -> dict:
        item = {"name": name}
        if symbols:
            item["symbols"] = list(symbols)
        return {"type": "subscribe", "payload": {"channels": [item]}}


def enable_heartbeat_message() -> dict:
    return {"type": "enable_heartbeat"}


def public_socket_options() -> dict:
    """Library keepalive is off.

    Delta's documented liveness check is the application heartbeat: the server
    sends one about every 30 seconds, and the client reconnects after 35 seconds
    without one. The websockets library's own ping closed this feed with
    ``1011 keepalive ping timeout`` before that 35-second deadline.
    """
    return {"ping_interval": None, "ping_timeout": None}


def seconds_until_heartbeat_deadline(last_heartbeat: datetime, now: datetime, limit_seconds: float) -> float:
    elapsed = (as_utc(now) - as_utc(last_heartbeat)).total_seconds()
    if elapsed < 0:
        return limit_seconds
    return max(0.0, limit_seconds - elapsed)


class StreamParser:
    """Tracks the forming 1-minute traded candle. ``cst`` is the open; ``ts`` is not."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.book = FormingBook("1m")

    def parse(self, raw: str, as_of: datetime) -> ParsedEvent:
        try:
            message = json.loads(raw, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            return ParsedEvent("malformed", detail={"error": str(exc)})
        if not isinstance(message, dict):
            return ParsedEvent("malformed", detail={"error": "message is not an object"})
        kind = str(message.get("type") or "")
        if kind == "heartbeat":
            return ParsedEvent("heartbeat", detail={"published_at": _optional_time(message.get("ts_publish") or message.get("ts_origin"))})
        if kind in ("ping", "pong"):
            return ParsedEvent(kind)
        if kind == "subscriptions":
            return ParsedEvent("subscriptions", detail={"channels": message.get("channels") or []})
        if kind == "system_status":
            return ParsedEvent(
                "system_status",
                detail={"status": message.get("status"), "event": message.get("event"), "timestamp": message.get("timestamp")},
            )
        if kind == "mark_price":
            return ParsedEvent("mark_price", detail={"symbol": message.get("sy"), "price": str(message.get("p")), "ts": message.get("ts")})
        if kind == "ticker":
            return ParsedEvent("ticker", detail={"symbol": message.get("sy"), "ts": message.get("ts")})
        if kind == "funding_rate":
            return ParsedEvent("funding_rate", detail=message)
        if kind == "ob_updates":
            return ParsedEvent("ob_updates", detail=message)
        if kind.startswith("candlestick_"):
            return self._candle(message, as_of)
        return ParsedEvent("ignored", detail={"type": kind})

    def close_due(self, as_of: datetime) -> CandleDraft | None:
        return self.book.close_if_due(as_of)

    def _candle(self, message: dict, as_of: datetime) -> ParsedEvent:
        try:
            draft = parse_ws_candlestick(message, as_of=as_of)
        except CandleRejected as exc:
            return ParsedEvent("malformed", detail={"error": exc.reason, "type": message.get("type")})
        if draft.symbol != self.symbol or draft.timeframe != "1m":
            return ParsedEvent("ignored", detail={"symbol": draft.symbol, "timeframe": draft.timeframe})
        candles, sequence = self.book.apply(draft)
        return ParsedEvent(
            "candle",
            candles=candles,
            sequence=sequence,
            detail={"publish_ts": message.get("ts"), "candle_start": message.get("cst")},
        )


def _optional_time(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        moment, _unit = epoch_to_utc(int(value))
    except (TypeError, ValueError, OSError):
        return None
    return moment.astimezone(timezone.utc).isoformat()


def reconnect_delay(attempt: int, rng: random.Random | None = None) -> float:
    return backoff_seconds(attempt, rng or random.Random(), base=1.0, cap=60.0)
