"""Offline derivatives parsing, calculations, persistence, and API shape."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.timeutil import utcnow
from app.database.models import FundingRateObservation
from app.database.repositories import MarketRepository
from app.derivatives.context import context_states
from app.derivatives.normalize import (
    LocalOrderBook,
    book_metrics,
    checksum,
    classify_funding,
    latest_trade,
    mark_last_difference,
    oi_price_context,
    parse_funding_message,
    parse_orderbook,
    parse_ticker,
    percent_change,
)
from app.main import create_app
from app.services.derivatives_service import funding_view, orderbook_view, snapshot_view, store_funding_message
from tests.conftest import btc_product, make_settings


def _ticker():
    return {
        "symbol": "BTCUSD",
        "timestamp": 1_793_774_913_813_644,
        "funding_rate": "0.0002",
        "mark_price": "77902.5",
        "close": 77914,
        "spot_price": "77932.8",
        "oi": "815.2790",
        "oi_contracts": "815279",
        "oi_value_symbol": "BTC",
        "oi_value_usd": "63538605.8092",
        "oi_change_usd_6h": "-2782807.2700",
        "quotes": {"best_bid": "77914", "best_ask": "77914.5", "bid_size": "100", "ask_size": "40"},
    }


def test_ticker_and_funding_normalization():
    parsed = parse_ticker(_ticker())
    assert parsed["funding_rate"] == Decimal("0.0002")
    assert parsed["mark_price"] == Decimal("77902.5")
    assert parsed["open_interest_contracts"] == Decimal("815279")
    assert parsed["observed_at"] is not None
    message = {
        "type": "funding_rate",
        "sy": "BTCUSD",
        "fr": "0.0002",
        "fi": 28800,
        "nfr": 1_793_780_000_000_000,
        "ts": 1_793_774_913_000_000,
    }
    funding = parse_funding_message(message)
    assert funding["interval_seconds"] == 28800
    assert funding["next_funding_at"] is not None
    with pytest.raises(ValueError):
        parse_funding_message({"type": "ticker"})
    with pytest.raises(ValueError):
        parse_ticker(["nope"])


def test_trades_accept_the_india_list_shape_and_reject_garbage():
    latest = latest_trade([
        {"price": "10", "timestamp": 2_000_000, "size": 1},
        {"price": "11", "timestamp": 3_000_000, "size": 2, "buyer_role": "maker", "seller_role": "taker"},
    ])
    assert latest["price"] == Decimal("11")
    nested = latest_trade({"result": {"trades": [{"price": "9", "side": "buy", "timestamp": 4_000_000}]}})
    assert nested["side"] == "buy"
    with pytest.raises(ValueError):
        latest_trade({"result": "nope"})


def test_open_interest_percent_and_price_context_do_not_infer_side():
    assert percent_change(Decimal("110"), Decimal("100")) == Decimal("0.1")
    assert percent_change(Decimal("90"), Decimal(0)) is None
    rising = oi_price_context(Decimal("0.1"), Decimal("0.02"))
    assert rising["state"] == "price_rising_oi_rising"
    divergence = oi_price_context(Decimal("-0.1"), Decimal("0.02"))
    assert divergence["state"] == "price_rising_oi_falling"
    assert "long or short" in divergence["note"]
    assert oi_price_context(None, Decimal("1"))["state"] == "INSUFFICIENT_HISTORY"


def test_mark_difference_is_not_described_as_an_executable_price():
    diff = mark_last_difference(Decimal("100"), Decimal("99"), Decimal("101"))
    assert diff["mark_last_difference"] == Decimal("1")
    assert diff["mark_last_percent"] == Decimal("1") / Decimal("99")
    assert diff["mark_index_difference"] == Decimal("-1")
    assert "not the executable" in diff["note"]
    assert mark_last_difference(None, Decimal("1"))["mark_last_difference"] is None


def test_order_book_spread_imbalance_and_empty_book():
    book = parse_orderbook(
        {
            "symbol": "BTCUSD",
            "last_updated_at": 1_700_000_000_000_000,
            "buy": [{"price": "100", "size": 3, "depth": "3"}, {"price": "99", "size": 1, "depth": "4"}],
            "sell": [{"price": "101", "size": 1, "depth": "1"}],
        }
    )
    metrics = book_metrics(book["bids"], book["asks"])
    assert metrics["spread"] == Decimal("1")
    assert metrics["bid_depth"] == Decimal("4")
    assert metrics["imbalance"] == Decimal("3") / Decimal("5")
    assert metrics["bid_concentration"] == Decimal("3") / Decimal("4")
    empty = book_metrics([], [])
    assert empty["status"] == "EMPTY"
    assert empty["spread"] is None
    assert empty["imbalance"] is None
    one = book_metrics(book["bids"], [])
    assert one["status"] == "ONE_SIDED"
    assert one["spread"] is None
    with pytest.raises(ValueError):
        parse_orderbook({"buy": "nope"})


def test_funding_classification_needs_history_and_does_not_forecast():
    near = classify_funding(Decimal("0.00001"), [], Decimal("0.0001"), 30)
    assert near["sign"] == "near_zero"
    assert near["historical"] == "INSUFFICIENT_HISTORY"
    history = [Decimal("0.01") if index == 0 else Decimal("0.0001") for index in range(30)]
    elevated = classify_funding(Decimal("0.01"), history, Decimal("0.0001"), 30)
    assert elevated["sign"] == "positive"
    assert elevated["historical"] == "historically_elevated"
    assert "not a forecast" in elevated["note"]
    depressed = classify_funding(Decimal("-0.02"), [Decimal("-0.02")] + [Decimal("0")] * 29, Decimal("0.0001"), 30)
    assert depressed["historical"] == "historically_depressed"


def test_order_book_sequence_reconnect_and_checksum():
    book = LocalOrderBook()
    assert book.apply({"type": "ob_updates", "action": "update", "seq": 2, "b": [["10", "1"]], "a": []}) == "sequence_gap"
    snapshot = {"type": "ob_updates", "action": "snapshot", "seq": 1, "b": [["10", "2"], ["9", "1"]], "a": [["11", "1"]]}
    snapshot["cs"] = checksum({"11": "1"}, {"10": "2", "9": "1"})
    assert book.apply(snapshot) == "snapshot"
    assert book.apply({"type": "ob_updates", "action": "update", "seq": 3, "b": [], "a": []}) == "sequence_gap"
    assert book.stale is True
    assert book.apply({"type": "ob_updates", "action": "error"}) == "error"
    fresh = LocalOrderBook()
    fresh.apply({"type": "ob_updates", "action": "snapshot", "seq": 5, "b": [["10", "2"]], "a": [["11", "1"]]})
    assert fresh.apply({"type": "ob_updates", "action": "update", "seq": 6, "b": [["10", "0"]], "a": [["11", "4"]]}) == "update"
    levels = fresh.levels()
    assert levels["bids"] == []
    assert levels["asks"][0]["size"] == Decimal("4")


def test_duplicate_funding_event_is_not_stored_twice(session):
    MarketRepository(session).upsert_product(btc_product())
    session.commit()
    message = {"type": "funding_rate", "sy": "BTCUSD", "fr": "0.0003", "fi": 28800, "nfr": 1_793_800_000_000_000, "ts": 1_793_770_000_000_000}
    assert store_funding_message(session, message) is True
    assert store_funding_message(session, message) is False
    assert session.query(FundingRateObservation).count() == 1


def test_api_reports_missing_and_stale_derivatives_without_fake_zeros(session_factory, session):
    product = MarketRepository(session).upsert_product(btc_product())
    session.commit()
    app = create_app(make_settings(), session_factory, start_socket=False)
    with TestClient(app) as client:
        empty = client.get("/derivatives/funding")
        assert empty.status_code == 200
        body = empty.json()
        assert body["availability"] == "NOT_COLLECTED"
        assert body["values"] is None
        assert body["stale"] is True
        assert "0.000" not in empty.text
    session.add(
        FundingRateObservation(
            product_id=product.id,
            observed_at=utcnow() - timedelta(hours=2),
            funding_rate=Decimal("0.0002"),
            interval_seconds=28800,
            next_funding_at=None,
            source="delta_rest_ticker",
            received_at=utcnow(),
        )
    )
    session.commit()
    settings = make_settings(stale_after_seconds=60)
    view = funding_view(session, settings)
    assert view["stale"] is True
    assert view["values"]["funding_rate"] == "0.0002"
    assert view["values"]["classification"]["sign"] == "positive"
    book = orderbook_view(session, settings)
    assert book["availability"] == "NOT_COLLECTED"
    snap = snapshot_view(session, settings)
    assert any(item["name"] == "funding_positive" for item in snap["states"])
    assert "buy" not in str(snap).lower()


def test_context_states_do_not_score():
    states = context_states(
        {"sign": "negative", "historical": "INSUFFICIENT_HISTORY", "note": "no forecast"},
        "price_falling_oi_rising",
        Decimal("0.01"),
        Decimal("0.5"),
        mark_threshold=Decimal("0.001"),
        imbalance_threshold=Decimal("0.2"),
        available=True,
    )
    names = [item["name"] for item in states]
    assert "funding_negative" in names
    assert "price_oi_divergence" in names
    assert "order_book_imbalance" in names
    assert "score" not in names
    unavailable = context_states(None, None, None, None, mark_threshold=Decimal("0.001"), imbalance_threshold=Decimal("0.2"), available=False)
    assert unavailable[0]["name"] == "derivatives_data_unavailable"
