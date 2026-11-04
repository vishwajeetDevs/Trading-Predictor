from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.timeutil import epoch_to_utc, from_epoch_seconds, to_decimal
from app.data.normalizer import CandleRejected, loads_decimal, parse_rest_candles, validate_ohlc
from tests.conftest import FIXTURES, load_json


def test_rest_fixture_sorts_oldest_first_and_keeps_decimal_tokens():
    payload = load_json("candles_1m_recent.json")
    as_of = from_epoch_seconds(1793747400) + timedelta(minutes=2)
    drafts, errors = parse_rest_candles(payload, symbol="BTCUSD", timeframe="1m", source="delta_rest", as_of=as_of)
    assert errors == []
    assert drafts[0].open_time < drafts[-1].open_time
    assert all(item.is_closed for item in drafts)
    assert all(item.timestamp_unit == "s" for item in drafts)
    raw = (FIXTURES / "candles_1m_recent.json").read_text(encoding="utf-8")
    parsed = loads_decimal(raw)
    assert any(row["close"] == Decimal("77800") for row in parsed["result"])
    assert any(item.close == Decimal("77800") for item in drafts)


def test_volume_scientific_notation_is_exact():
    payload = loads_decimal((FIXTURES / "candles_1m_recent.json").read_text(encoding="utf-8"))
    volumes = [row["volume"] for row in payload["result"]]
    assert Decimal("3300") in volumes
    assert all(isinstance(item, Decimal) for item in volumes)


def test_timestamp_units_are_chosen_per_magnitude():
    seconds, unit = epoch_to_utc(1793747280)
    micros, micro_unit = epoch_to_utc(1793747280000000)
    millis, milli_unit = epoch_to_utc(1793747280000)
    assert unit == "s" and int(seconds.timestamp()) == 1793747280
    assert micro_unit == "us" and micros == seconds
    assert milli_unit == "ms" and int(millis.timestamp()) == 1793747280


def test_decimal_rejects_null_bool_and_keeps_fraction():
    assert to_decimal("0.001") == Decimal("0.001")
    assert to_decimal("0.5") == Decimal("0.5")
    with pytest.raises(Exception):
        to_decimal(None)
    with pytest.raises(Exception):
        to_decimal(True)


def test_invalid_ohlc_is_rejected():
    with pytest.raises(CandleRejected):
        validate_ohlc(Decimal("10"), Decimal("9"), Decimal("11"), Decimal("10"), Decimal("1"))
    with pytest.raises(CandleRejected):
        validate_ohlc(Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"), Decimal("-1"))
    with pytest.raises(CandleRejected):
        validate_ohlc(Decimal("0"), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"))


def test_null_and_malformed_rows_are_reported_not_invented():
    as_of = from_epoch_seconds(1_700_000_120)
    payload = {
        "success": True,
        "result": [
            {"time": 1_700_000_000, "open": None, "high": 2, "low": 1, "close": 1, "volume": 1},
            {"time": 1_700_000_060, "open": 1, "high": 1, "low": 2, "close": 1, "volume": 1},
            "nope",
            {"time": 1_700_000_120, "open": 2, "high": 3, "low": 2, "close": 3, "volume": 1},
        ],
    }
    drafts, errors = parse_rest_candles(payload, symbol="BTCUSD", timeframe="1m", source="delta_rest", as_of=as_of)
    assert len(drafts) == 1
    assert len(errors) == 3
