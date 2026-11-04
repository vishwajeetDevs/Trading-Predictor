import copy

import pytest

from app.data.product_resolver import AmbiguousProduct, ProductNotFound, is_btc_perpetual, public_metadata, select_btc_perpetual
from tests.conftest import btc_product


def test_fixture_is_the_live_btc_perpetual():
    product = btc_product()
    assert is_btc_perpetual(product)
    chosen = select_btc_perpetual([{"symbol": "ETHUSD", "contract_type": "perpetual_futures", "state": "live"}, product])
    assert chosen["symbol"] == "BTCUSD"
    assert chosen["id"] == product["id"]
    assert chosen["id"] != 0


def test_rejects_spot_options_and_expired():
    product = btc_product()
    spot = copy.deepcopy(product)
    spot["symbol"] = "BTC_INR"
    option = copy.deepcopy(product)
    option["symbol"] = "C-BTC-90000-300126"
    expired = copy.deepcopy(product)
    expired["state"] = "expired"
    expired["symbol"] = "BTCUSD-EXP"
    settled = copy.deepcopy(product)
    settled["settlement_time"] = "2024-01-01T00:00:00Z"
    settled["symbol"] = "BTCUSD-SET"
    assert not any(is_btc_perpetual(item) for item in (spot, option, expired, settled))
    with pytest.raises(ProductNotFound):
        select_btc_perpetual([spot, option, expired])


def test_ambiguous_usd_perpetuals_are_not_guessed():
    first = btc_product()
    second = copy.deepcopy(first)
    second["id"] = int(first["id"]) + 1
    second["symbol"] = "BTC2USD"
    with pytest.raises(AmbiguousProduct):
        select_btc_perpetual([first, second])


def test_public_metadata_omits_leverage_and_margin():
    meta = public_metadata(btc_product())
    assert meta["symbol"] == "BTCUSD"
    assert meta["underlying_asset"] == "BTC"
    assert meta["quoting_asset"] == "USD"
    assert meta["settling_asset"] == "USD"
    assert meta["tick_size"] == "0.5"
    assert meta["contract_type"] == "perpetual_futures"
    blob = " ".join(meta.keys())
    assert "leverage" not in blob
    assert "margin" not in blob
