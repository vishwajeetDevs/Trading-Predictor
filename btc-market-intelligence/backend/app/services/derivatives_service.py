"""Collect and read public derivatives observations. GET requests do not call the exchange."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.timeutil import as_utc, to_decimal, utcnow
from app.data.delta_rest import DeltaRestClient
from app.database.models import (
    Candle,
    DerivativesQualityEvent,
    FundingRateObservation,
    MarkPriceObservation,
    OpenInterestObservation,
    OrderBookSnapshot,
    Product,
)
from app.database.repositories import MarketRepository, json_safe
from app.derivatives.context import context_states
from app.derivatives.normalize import (
    book_change,
    book_metrics,
    classify_funding,
    latest_trade,
    mark_last_difference,
    oi_price_context,
    parse_funding_message,
    parse_orderbook,
    parse_ticker,
    percent_change,
)

log = logging.getLogger(__name__)
OI_INTERVALS = (("1h", 3600), ("4h", 14400), ("24h", 86400))


def collect_once(session: Session, settings: Settings, client: DeltaRestClient | None = None) -> dict:
    product = MarketRepository(session).get_product()
    if product is None:
        return {"status": "no_product"}
    owned = client is None
    if owned:
        client = DeltaRestClient(
            settings.delta_rest_base,
            settings.user_agent,
            timeout=settings.http_timeout_seconds,
            min_interval=settings.request_min_interval_seconds,
            max_retries=settings.max_retries,
        )
        client.__enter__()
    try:
        ticker = parse_ticker(client.fetch_ticker(product.symbol))
        book = parse_orderbook(client.fetch_orderbook(product.symbol, settings.orderbook_depth), settings.orderbook_depth)
        trade = latest_trade(client.fetch_trades(product.symbol))
    finally:
        if owned:
            client.__exit__(None, None, None)
    if trade is not None:
        ticker["last_price"] = trade["price"]
        ticker["last_trade_at"] = trade["observed_at"]
    specs = ((product.raw_metadata or {}).get("product_specs") or {})
    interval = specs.get("rate_exchange_interval")
    now = utcnow()
    stored = {
        "funding": _store_funding(session, product.id, ticker, interval, now),
        "open_interest": _store_oi(session, product.id, ticker, now),
        "mark": _store_mark(session, product.id, ticker, now),
        "orderbook": _store_book(session, product.id, book, now),
    }
    _retain(session, product.id, now, settings)
    session.commit()
    stored["status"] = "collected"
    return stored


def store_funding_message(session: Session, message: dict) -> bool:
    product = MarketRepository(session).get_product()
    if product is None:
        return False
    parsed = parse_funding_message(message)
    if parsed.get("symbol") not in (None, product.symbol):
        return False
    created = _insert(
        session,
        FundingRateObservation,
        {
            "product_id": product.id,
            "observed_at": parsed["observed_at"],
            "funding_rate": parsed["funding_rate"],
            "interval_seconds": parsed["interval_seconds"],
            "next_funding_at": parsed["next_funding_at"],
            "source": parsed["source"],
            "received_at": utcnow(),
        },
    )
    session.commit()
    return created


def funding_view(session: Session, settings: Settings) -> dict:
    product = _product(session)
    rows = _history(session, FundingRateObservation, product.id, 500)
    latest = rows[-1] if rows else None
    history = [to_decimal(row.funding_rate) for row in rows if _within(row.observed_at, settings.funding_lookback_days)]
    rate = None if latest is None else to_decimal(latest.funding_rate)
    classification = classify_funding(rate, history, Decimal(settings.funding_near_zero), settings.funding_min_sample)
    values = None
    if latest is not None:
        values = {
            "funding_rate": _text(latest.funding_rate),
            "interval_seconds": latest.interval_seconds,
            "next_funding_at": _iso(latest.next_funding_at),
            "classification": classification,
            "history": [
                {"observed_at": _iso(row.observed_at), "funding_rate": _text(row.funding_rate), "source": row.source}
                for row in rows
            ],
            "history_note": "History is the observations this process stored. Delta India's documented public API has no funding-rate history endpoint.",
        }
    warnings = []
    if latest is None:
        warnings.append("No funding observation has been stored.")
    elif latest.next_funding_at is None:
        warnings.append("The REST ticker does not include the next funding time. That field arrives on the funding_rate WebSocket channel.")
    return _envelope(product, None if latest is None else latest.source, None if latest is None else latest.observed_at, values, warnings, settings)


def open_interest_view(session: Session, settings: Settings) -> dict:
    product = _product(session)
    rows = _history(session, OpenInterestObservation, product.id, 500)
    latest = rows[-1] if rows else None
    changes = []
    if latest is not None:
        current_contracts = to_decimal(latest.contracts) if latest.contracts is not None else to_decimal(latest.value)
        latest_close = _close_at(session, product.id, as_utc(latest.observed_at))
        for name, seconds in OI_INTERVALS:
            earlier = _at_or_before(rows, as_utc(latest.observed_at) - timedelta(seconds=seconds), settings.derivatives_poll_seconds * 2)
            if earlier is None or earlier.observed_at == latest.observed_at:
                changes.append({"interval": name, "state": "INSUFFICIENT_HISTORY", "open_interest_change": None, "price_change": None})
                continue
            previous_contracts = to_decimal(earlier.contracts) if earlier.contracts is not None else to_decimal(earlier.value)
            oi_change = percent_change(current_contracts, previous_contracts)
            earlier_close = _close_at(session, product.id, as_utc(earlier.observed_at))
            price_change = percent_change(latest_close, earlier_close)
            context = oi_price_context(oi_change, price_change)
            changes.append(
                {
                    "interval": name,
                    "state": context["state"],
                    "note": context["note"],
                    "open_interest_change": _text(oi_change),
                    "price_change": _text(price_change),
                    "earlier_observed_at": _iso(earlier.observed_at),
                    "calculated_locally": True,
                }
            )
    values = None
    if latest is not None:
        values = {
            "contracts": _text(latest.contracts),
            "value": _text(latest.value),
            "value_symbol": latest.value_symbol,
            "value_usd": _text(latest.value_usd),
            "exchange_change_6h": _text(latest.exchange_change_6h),
            "exchange_change_note": "oi_change_usd_6h is copied from the public ticker. It is not calculated by this service.",
            "changes": changes,
            "history": [
                {"observed_at": _iso(row.observed_at), "contracts": _text(row.contracts), "value_usd": _text(row.value_usd)}
                for row in rows
            ],
            "history_note": "Historical open interest is only the series this process has sampled. No public open-interest history endpoint is documented.",
        }
    warnings = [] if latest is not None else ["No open-interest observation has been stored."]
    return _envelope(product, None if latest is None else latest.source, None if latest is None else latest.observed_at, values, warnings, settings)


def mark_price_view(session: Session, settings: Settings) -> dict:
    product = _product(session)
    rows = _history(session, MarkPriceObservation, product.id, 500)
    latest = rows[-1] if rows else None
    values = None
    if latest is not None:
        diff = mark_last_difference(
            None if latest.mark_price is None else to_decimal(latest.mark_price),
            None if latest.last_price is None else to_decimal(latest.last_price),
            None if latest.index_price is None else to_decimal(latest.index_price),
        )
        values = {key: _text(value) if isinstance(value, Decimal) else value for key, value in diff.items()}
        values["history"] = [
            {
                "observed_at": _iso(row.observed_at),
                "mark_price": _text(row.mark_price),
                "last_price": _text(row.last_price),
                "index_price": _text(row.index_price),
            }
            for row in rows
        ]
        values["index_source"] = "ticker spot_price, the public index price. GET /v2/indices describes the index and does not return a price."
    warnings = [] if latest is not None else ["No mark-price observation has been stored."]
    return _envelope(product, None if latest is None else latest.source, None if latest is None else latest.observed_at, values, warnings, settings)


def orderbook_view(session: Session, settings: Settings) -> dict:
    product = _product(session)
    rows = list(
        session.scalars(
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.product_id == product.id)
            .order_by(OrderBookSnapshot.snapshot_at.desc())
            .limit(2)
        ).all()
    )
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    values = None
    if latest is not None:
        current = {
            "best_bid": None if latest.best_bid is None else to_decimal(latest.best_bid),
            "best_ask": None if latest.best_ask is None else to_decimal(latest.best_ask),
            "bid_depth": None if latest.bid_depth is None else to_decimal(latest.bid_depth),
            "ask_depth": None if latest.ask_depth is None else to_decimal(latest.ask_depth),
        }
        prior = None
        if previous is not None:
            prior = {
                "best_bid": None if previous.best_bid is None else to_decimal(previous.best_bid),
                "best_ask": None if previous.best_ask is None else to_decimal(previous.best_ask),
                "bid_depth": None if previous.bid_depth is None else to_decimal(previous.bid_depth),
                "ask_depth": None if previous.ask_depth is None else to_decimal(previous.ask_depth),
            }
        change = book_change(prior, current)
        values = {
            "best_bid": _text(latest.best_bid),
            "best_ask": _text(latest.best_ask),
            "spread": _text(latest.spread),
            "bid_depth": _text(latest.bid_depth),
            "ask_depth": _text(latest.ask_depth),
            "imbalance": _text(latest.imbalance),
            "levels": latest.levels,
            "book": latest.payload,
            "change": None if change is None else {key: _text(value) if isinstance(value, Decimal) else value for key, value in change.items()},
            "exchange_fields": ["price", "size", "depth", "last_updated_at", "symbol"],
            "local_fields": ["spread", "spread_percent", "imbalance", "concentration", "change"],
        }
    warnings = [] if latest is not None else ["No order-book snapshot has been stored."]
    if latest is not None and (latest.best_bid is None or latest.best_ask is None):
        warnings.append("The stored book is empty or one-sided, so spread and imbalance are withheld.")
    return _envelope(product, None if latest is None else latest.source, None if latest is None else latest.snapshot_at, values, warnings, settings)


def snapshot_view(session: Session, settings: Settings) -> dict:
    funding = funding_view(session, settings)
    interest = open_interest_view(session, settings)
    mark = mark_price_view(session, settings)
    book = orderbook_view(session, settings)
    available = any(item["availability"] == "AVAILABLE" for item in (funding, interest, mark, book))
    oi_state = None
    if interest["values"]:
        hour = next((item for item in interest["values"]["changes"] if item["interval"] == "1h"), None)
        oi_state = None if hour is None else hour["state"]
    mark_percent = None
    if mark["values"] and mark["values"].get("mark_last_percent") not in (None, ""):
        mark_percent = Decimal(mark["values"]["mark_last_percent"])
    imbalance = None
    if book["values"] and book["values"].get("imbalance") not in (None, ""):
        imbalance = Decimal(book["values"]["imbalance"])
    funding_class = None if not funding["values"] else funding["values"]["classification"]
    states = context_states(
        funding_class,
        oi_state,
        mark_percent,
        imbalance,
        mark_threshold=Decimal(settings.mark_divergence_percent),
        imbalance_threshold=Decimal(settings.book_imbalance_threshold),
        available=available,
    )
    timestamps = [item["timestamp"] for item in (funding, interest, mark, book) if item["timestamp"]]
    observed = max(timestamps) if timestamps else None
    return {
        "read_only": True,
        "product": funding["product"],
        "source": "stored_public_observations",
        "timestamp": observed,
        "freshness_seconds": None if observed is None else (utcnow() - as_utc(_parse(observed))).total_seconds(),
        "stale": any(item["stale"] for item in (funding, interest, mark, book)),
        "availability": "AVAILABLE" if available else "NOT_COLLECTED",
        "states": states,
        "funding": funding,
        "open_interest": interest,
        "mark_price": mark,
        "orderbook": book,
        "unsupported": [
            "No documented public REST history endpoint exists for funding rate, open interest, or mark price.",
            "Open interest does not identify long versus short positions.",
            "Mark price is not an executable trade price.",
            "Order-book imbalance is not a directional signal.",
        ],
        "warnings": [warning for item in (funding, interest, mark, book) for warning in item["warnings"]],
    }


def record_quality(session: Session, kind: str, detail: dict) -> None:
    now = utcnow()
    session.add(DerivativesQualityEvent(occurred_at=now, kind=kind, detail=json_safe(detail)))
    cutoff = now - timedelta(days=14)
    session.execute(delete(DerivativesQualityEvent).where(DerivativesQualityEvent.occurred_at < cutoff))
    session.commit()


def _store_funding(session, product_id, ticker, interval, now) -> bool:
    if ticker["funding_rate"] is None or ticker["observed_at"] is None:
        return False
    return _insert(
        session,
        FundingRateObservation,
        {
            "product_id": product_id,
            "observed_at": ticker["observed_at"],
            "funding_rate": ticker["funding_rate"],
            "interval_seconds": None if interval in (None, "") else int(interval),
            "next_funding_at": None,
            "source": ticker["source"],
            "received_at": now,
        },
    )


def _store_oi(session, product_id, ticker, now) -> bool:
    if ticker["observed_at"] is None or ticker["open_interest_contracts"] is None and ticker["open_interest_value"] is None:
        return False
    return _insert(
        session,
        OpenInterestObservation,
        {
            "product_id": product_id,
            "observed_at": ticker["observed_at"],
            "contracts": ticker["open_interest_contracts"],
            "value": ticker["open_interest_value"],
            "value_symbol": ticker["open_interest_symbol"],
            "value_usd": ticker["open_interest_usd"],
            "exchange_change_6h": ticker["exchange_oi_change_6h"],
            "source": ticker["source"],
            "received_at": now,
        },
    )


def _store_mark(session, product_id, ticker, now) -> bool:
    if ticker["observed_at"] is None or ticker["mark_price"] is None:
        return False
    return _insert(
        session,
        MarkPriceObservation,
        {
            "product_id": product_id,
            "observed_at": ticker["observed_at"],
            "mark_price": ticker["mark_price"],
            "last_price": ticker["last_price"],
            "index_price": ticker["index_price"],
            "source": "delta_rest_ticker_and_trades" if ticker.get("last_trade_at") else ticker["source"],
            "received_at": now,
        },
    )


def _store_book(session, product_id, book, now) -> bool:
    if book["observed_at"] is None:
        return False
    metrics = book_metrics(book["bids"], book["asks"])
    payload = {
        "bids": [_level(level) for level in book["bids"]],
        "asks": [_level(level) for level in book["asks"]],
        "spread_percent": _text(metrics["spread_percent"]),
        "bid_concentration": _text(metrics["bid_concentration"]),
        "ask_concentration": _text(metrics["ask_concentration"]),
        "status": metrics["status"],
    }
    return _insert(
        session,
        OrderBookSnapshot,
        {
            "product_id": product_id,
            "snapshot_at": book["observed_at"],
            "best_bid": metrics["best_bid"],
            "best_ask": metrics["best_ask"],
            "spread": metrics["spread"],
            "bid_depth": metrics["bid_depth"],
            "ask_depth": metrics["ask_depth"],
            "imbalance": metrics["imbalance"],
            "levels": max(len(book["bids"]), len(book["asks"])),
            "payload": json_safe(payload),
            "source": book["source"],
            "received_at": now,
        },
    )


def _insert(session, model, values) -> bool:
    try:
        with session.begin_nested():
            session.add(model(**values))
            session.flush()
    except IntegrityError:
        return False
    return True


def _retain(session, product_id, now, settings: Settings) -> None:
    funding_cutoff = now - timedelta(days=settings.derivatives_retention_days)
    mark_cutoff = now - timedelta(days=settings.mark_retention_days)
    book_cutoff = now - timedelta(hours=settings.orderbook_retention_hours)
    session.execute(delete(FundingRateObservation).where(FundingRateObservation.product_id == product_id, FundingRateObservation.observed_at < funding_cutoff))
    session.execute(delete(OpenInterestObservation).where(OpenInterestObservation.product_id == product_id, OpenInterestObservation.observed_at < funding_cutoff))
    session.execute(delete(MarkPriceObservation).where(MarkPriceObservation.product_id == product_id, MarkPriceObservation.observed_at < mark_cutoff))
    session.execute(delete(OrderBookSnapshot).where(OrderBookSnapshot.product_id == product_id, OrderBookSnapshot.snapshot_at < book_cutoff))


def _history(session, model, product_id, limit: int):
    rows = session.scalars(
        select(model).where(model.product_id == product_id).order_by(model.observed_at.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


def _at_or_before(rows, target, slack_seconds: int):
    eligible = [row for row in rows if as_utc(row.observed_at) <= target]
    if not eligible:
        return None
    chosen = max(eligible, key=lambda row: row.observed_at)
    if (target - as_utc(chosen.observed_at)).total_seconds() > slack_seconds:
        return None
    return chosen


def _close_at(session, product_id, moment) -> Decimal | None:
    close = session.scalar(
        select(Candle.close)
        .where(Candle.product_id == product_id, Candle.timeframe == "1m", Candle.is_closed.is_(True), Candle.open_time <= moment)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    return None if close is None else to_decimal(close)


def _within(moment, days: int) -> bool:
    return as_utc(moment) >= utcnow() - timedelta(days=days)


def _product(session) -> Product:
    product = MarketRepository(session).get_product()
    if product is None:
        raise LookupError("no product")
    return product


def _envelope(product, source, observed, values, warnings, settings) -> dict:
    lag = None if observed is None else (utcnow() - as_utc(observed)).total_seconds()
    return {
        "read_only": True,
        "product": {"symbol": product.symbol, "exchange_product_id": product.exchange_product_id, "contract_type": product.contract_type},
        "source": source,
        "timestamp": _iso(observed),
        "freshness_seconds": lag,
        "stale": observed is None or lag > settings.stale_after_seconds,
        "availability": "NOT_COLLECTED" if observed is None else "AVAILABLE",
        "values": values,
        "warnings": warnings,
    }


def _iso(value):
    if value is None:
        return None
    return as_utc(value).isoformat()


def _text(value):
    if value is None:
        return None
    try:
        text = format(to_decimal(value), "f")
    except Exception:
        return None
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _level(level: dict) -> dict:
    return {"price": _text(level["price"]), "size": _text(level["size"]), "depth": _text(level.get("depth"))}


def _parse(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
