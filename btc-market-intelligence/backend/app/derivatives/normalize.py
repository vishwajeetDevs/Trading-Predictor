"""Parse public Delta derivatives payloads and compute descriptive metrics.

Exchange fields stay labelled as exchange fields. Spread, imbalance, and
funding rank are calculated here and are not exchange fields.
"""

from __future__ import annotations

import zlib
from decimal import Decimal, InvalidOperation

from app.analysis.stats import quantile
from app.core.timeutil import epoch_to_utc, to_decimal


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return to_decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _time(value):
    if value in (None, ""):
        return None
    try:
        moment, _unit = epoch_to_utc(int(value))
    except (TypeError, ValueError, OSError):
        return None
    return moment


def parse_ticker(payload: dict) -> dict:
    """Normalize `GET /v2/tickers/{symbol}`. Missing numbers stay missing."""
    if not isinstance(payload, dict):
        raise ValueError("ticker payload is not an object")
    quotes = payload.get("quotes") if isinstance(payload.get("quotes"), dict) else {}
    observed = _time(payload.get("timestamp"))
    return {
        "symbol": payload.get("symbol"),
        "observed_at": observed,
        "funding_rate": _decimal(payload.get("funding_rate")),
        "mark_price": _decimal(payload.get("mark_price")),
        "last_price": _decimal(payload.get("close")),
        "index_price": _decimal(payload.get("spot_price")),
        "open_interest_value": _decimal(payload.get("oi")),
        "open_interest_contracts": _decimal(payload.get("oi_contracts")),
        "open_interest_symbol": payload.get("oi_value_symbol"),
        "open_interest_usd": _decimal(payload.get("oi_value_usd")),
        "exchange_oi_change_6h": _decimal(payload.get("oi_change_usd_6h")),
        "best_bid": _decimal(quotes.get("best_bid")),
        "best_ask": _decimal(quotes.get("best_ask")),
        "bid_size": _decimal(quotes.get("bid_size")),
        "ask_size": _decimal(quotes.get("ask_size")),
        "source": "delta_rest_ticker",
    }


def parse_trades(payload) -> list[dict]:
    """Normalize `GET /v2/trades/{symbol}`.

    The documented example nests trades under `result.trades` and uses `side`.
    The India response observed on 2026-11-04 is a list of trades with
    `buyer_role` and `seller_role` instead of `side`. Both shapes are accepted.
    """
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("trades")
        if rows is None and isinstance(payload.get("result"), list):
            rows = payload.get("result")
        elif rows is None and isinstance(payload.get("result"), dict):
            rows = payload["result"].get("trades")
    if not isinstance(rows, list):
        raise ValueError("trades payload is not a list")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _decimal(row.get("price"))
        observed = _time(row.get("timestamp"))
        if price is None or observed is None:
            continue
        parsed.append(
            {
                "price": price,
                "size": _decimal(row.get("size")),
                "observed_at": observed,
                "symbol": row.get("symbol"),
                "buyer_role": row.get("buyer_role"),
                "seller_role": row.get("seller_role"),
                "side": row.get("side"),
            }
        )
    parsed.sort(key=lambda item: item["observed_at"])
    return parsed


def latest_trade(payload) -> dict | None:
    rows = parse_trades(payload)
    return None if not rows else rows[-1]


def parse_orderbook(payload: dict, depth: int | None = None) -> dict:
    """Normalize `GET /v2/l2orderbook/{symbol}`. `depth` on each level is the exchange cumulative size."""
    if not isinstance(payload, dict):
        raise ValueError("order book payload is not an object")
    limit = None if depth is None or depth < 1 else depth

    def levels(name: str) -> list[dict]:
        raw = payload.get(name) or []
        if not isinstance(raw, list):
            raise ValueError("order book %s is not a list" % name)
        output = []
        for row in raw[:limit] if limit else raw:
            if not isinstance(row, dict):
                continue
            price = _decimal(row.get("price"))
            size = _decimal(row.get("size"))
            if price is None or size is None:
                continue
            output.append({"price": price, "size": size, "depth": _decimal(row.get("depth"))})
        return output

    return {
        "symbol": payload.get("symbol"),
        "observed_at": _time(payload.get("last_updated_at")),
        "bids": levels("buy"),
        "asks": levels("sell"),
        "source": "delta_rest_l2orderbook",
    }


def parse_funding_message(message: dict) -> dict:
    """Normalize the public `funding_rate` WebSocket message."""
    if not isinstance(message, dict) or message.get("type") != "funding_rate":
        raise ValueError("not a funding_rate message")
    rate = _decimal(message.get("fr"))
    observed = _time(message.get("ts"))
    if rate is None or observed is None:
        raise ValueError("funding_rate message is missing the rate or timestamp")
    interval = message.get("fi")
    return {
        "symbol": message.get("sy"),
        "funding_rate": rate,
        "interval_seconds": None if interval in (None, "") else int(interval),
        "next_funding_at": _time(message.get("nfr")),
        "observed_at": observed,
        "source": "delta_ws_funding_rate",
    }


def parse_mark_message(message: dict) -> dict:
    if not isinstance(message, dict) or message.get("type") != "mark_price":
        raise ValueError("not a mark_price message")
    price = _decimal(message.get("p"))
    observed = _time(message.get("ts"))
    if price is None or observed is None:
        raise ValueError("mark_price message is missing the price or timestamp")
    return {
        "symbol": message.get("sy"),
        "mark_price": price,
        "observed_at": observed,
        "source": "delta_ws_mark_price",
    }


def book_metrics(bids: list[dict], asks: list[dict]) -> dict:
    """Local calculations from one book snapshot. An empty book has no spread."""
    calculated = {
        "best_bid": None,
        "best_ask": None,
        "spread": None,
        "spread_percent": None,
        "bid_depth": None,
        "ask_depth": None,
        "imbalance": None,
        "bid_concentration": None,
        "ask_concentration": None,
        "status": "EMPTY",
        "note": "Spread, depth, imbalance, and concentration are calculated locally. They are not a directional signal.",
    }
    if not bids and not asks:
        return calculated
    if bids:
        calculated["best_bid"] = bids[0]["price"]
        calculated["bid_depth"] = _side_depth(bids)
        calculated["bid_concentration"] = _concentration(bids)
    if asks:
        calculated["best_ask"] = asks[0]["price"]
        calculated["ask_depth"] = _side_depth(asks)
        calculated["ask_concentration"] = _concentration(asks)
    if calculated["best_bid"] is None or calculated["best_ask"] is None:
        calculated["status"] = "ONE_SIDED"
        return calculated
    spread = calculated["best_ask"] - calculated["best_bid"]
    mid = (calculated["best_ask"] + calculated["best_bid"]) / Decimal(2)
    calculated["spread"] = spread
    calculated["spread_percent"] = None if mid == 0 else spread / mid
    bid_depth = calculated["bid_depth"] or Decimal(0)
    ask_depth = calculated["ask_depth"] or Decimal(0)
    total = bid_depth + ask_depth
    calculated["imbalance"] = None if total == 0 else (bid_depth - ask_depth) / total
    calculated["status"] = "OK"
    return calculated


def _side_depth(levels: list[dict]) -> Decimal:
    cumulative = levels[-1].get("depth")
    if cumulative is not None:
        return cumulative
    return sum((level["size"] for level in levels), Decimal(0))


def _concentration(levels: list[dict]) -> Decimal | None:
    total = _side_depth(levels)
    if total == 0:
        return None
    return levels[0]["size"] / total


def mark_last_difference(mark: Decimal | None, last: Decimal | None, index: Decimal | None = None) -> dict:
    """Mark price is the liquidation reference. It is not an executable trade price."""
    result = {
        "mark_price": mark,
        "last_price": last,
        "index_price": index,
        "mark_last_difference": None,
        "mark_last_percent": None,
        "mark_index_difference": None,
        "mark_index_percent": None,
        "note": "Mark price is the price used to mark positions. It is not the executable last traded price.",
    }
    if mark is not None and last not in (None, 0):
        result["mark_last_difference"] = mark - last
        result["mark_last_percent"] = (mark - last) / last
    if mark is not None and index not in (None, 0):
        result["mark_index_difference"] = mark - index
        result["mark_index_percent"] = (mark - index) / index
    return result


def classify_funding(rate: Decimal | None, history: list[Decimal], near_zero: Decimal, minimum: int) -> dict:
    """Sign is descriptive. Percentile rank needs the documented minimum sample."""
    if rate is None:
        return {
            "sign": "UNAVAILABLE",
            "historical": "UNAVAILABLE",
            "sample_size": len(history),
            "note": "No funding rate is available.",
        }
    if rate > near_zero:
        sign = "positive"
    elif rate < -near_zero:
        sign = "negative"
    else:
        sign = "near_zero"
    historical = "INSUFFICIENT_HISTORY"
    if len(history) >= minimum:
        p90 = quantile([float(item) for item in history], 0.9)
        p10 = quantile([float(item) for item in history], 0.1)
        if p90 is not None and rate >= Decimal(str(p90)):
            historical = "historically_elevated"
        elif p10 is not None and rate <= Decimal(str(p10)):
            historical = "historically_depressed"
        else:
            historical = "within_historical_range"
    return {
        "sign": sign,
        "historical": historical,
        "sample_size": len(history),
        "minimum_sample": minimum,
        "note": "A positive funding rate is not a forecast that price will fall. A negative funding rate is not a forecast that price will rise.",
    }


def percent_change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous


def oi_price_context(oi_change: Decimal | None, price_change: Decimal | None) -> dict:
    """Open interest does not identify whether the outstanding contracts are long or short."""
    note = "Open interest is the number of outstanding contracts. It does not show whether those contracts are long or short."
    if oi_change is None or price_change is None:
        return {"state": "INSUFFICIENT_HISTORY", "note": note}
    if oi_change == 0 and price_change == 0:
        return {"state": "unchanged", "note": note}
    price = "rising" if price_change > 0 else "falling" if price_change < 0 else "unchanged"
    interest = "rising" if oi_change > 0 else "falling" if oi_change < 0 else "unchanged"
    if price == "unchanged" or interest == "unchanged":
        state = "price_%s_oi_%s" % (price, interest)
    else:
        state = "price_%s_oi_%s" % (price, interest)
    return {"state": state, "note": note}


def book_change(previous: dict | None, current: dict | None) -> dict | None:
    """Compare two stored snapshots. This is not an exchange incremental delta."""
    if not previous or not current:
        return None
    return {
        "method": "stored_snapshot_difference",
        "best_bid_change": _subtract(current.get("best_bid"), previous.get("best_bid")),
        "best_ask_change": _subtract(current.get("best_ask"), previous.get("best_ask")),
        "bid_depth_change": _subtract(current.get("bid_depth"), previous.get("bid_depth")),
        "ask_depth_change": _subtract(current.get("ask_depth"), previous.get("ask_depth")),
        "note": "Calculated from two locally stored snapshots. It is not an exchange-provided order-book delta.",
    }


def _subtract(left, right):
    if left is None or right is None:
        return None
    return left - right


class LocalOrderBook:
    """Maintain one symbol book from `ob_updates` snapshots and sequence-checked updates."""

    def __init__(self):
        self.bids: dict[str, str] = {}
        self.asks: dict[str, str] = {}
        self.sequence: int | None = None
        self.stale = True
        self.last_checksum: int | None = None
        self.checksum_matches: bool | None = None

    def apply(self, message: dict) -> str:
        if not isinstance(message, dict) or message.get("type") != "ob_updates":
            return "ignored"
        action = message.get("action")
        if action == "error":
            self.stale = True
            return "error"
        if action == "snapshot":
            self.bids = {}
            self.asks = {}
            self._write(self.bids, message.get("b"))
            self._write(self.asks, message.get("a"))
            self.sequence = _sequence(message.get("seq"))
            self.stale = False
            self._check(message.get("cs"))
            return "snapshot"
        if action == "update":
            incoming = _sequence(message.get("seq"))
            if self.stale or self.sequence is None or incoming is None or incoming != self.sequence + 1:
                self.stale = True
                return "sequence_gap"
            self._write(self.bids, message.get("b"))
            self._write(self.asks, message.get("a"))
            self.sequence = incoming
            self._check(message.get("cs"))
            if self.checksum_matches is False:
                self.stale = True
                return "checksum_mismatch"
            return "update"
        return "ignored"

    def levels(self, limit: int = 15) -> dict:
        bids = sorted(self.bids.items(), key=lambda item: Decimal(item[0]), reverse=True)[:limit]
        asks = sorted(self.asks.items(), key=lambda item: Decimal(item[0]))[:limit]
        return {
            "bids": [{"price": Decimal(price), "size": Decimal(size), "depth": None} for price, size in bids],
            "asks": [{"price": Decimal(price), "size": Decimal(size), "depth": None} for price, size in asks],
            "sequence": self.sequence,
            "stale": self.stale,
            "checksum_matches": self.checksum_matches,
        }

    def _write(self, side: dict[str, str], rows) -> None:
        if not rows:
            return
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            price, size = str(row[0]), str(row[1])
            if Decimal(size) == 0:
                side.pop(price, None)
            else:
                side[price] = size

    def _check(self, expected) -> None:
        if expected in (None, ""):
            self.checksum_matches = None
            return
        self.last_checksum = int(expected)
        self.checksum_matches = self.last_checksum == checksum(self.asks, self.bids)


def _sequence(value) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def checksum(asks: dict[str, str], bids: dict[str, str]) -> int:
    """CRC32 of the first 10 ask and bid levels, as documented for `ob_updates`."""
    ask_rows = sorted(asks.items(), key=lambda item: Decimal(item[0]))[:10]
    bid_rows = sorted(bids.items(), key=lambda item: Decimal(item[0]), reverse=True)[:10]
    ask_text = ",".join("%s:%s" % pair for pair in ask_rows)
    bid_text = ",".join("%s:%s" % pair for pair in bid_rows)
    return zlib.crc32(("%s|%s" % (ask_text, bid_text)).encode("utf-8")) & 0xFFFFFFFF
