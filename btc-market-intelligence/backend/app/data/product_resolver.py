"""Pick the live BTC perpetual from a Delta product catalogue. No product id is hardcoded."""

from __future__ import annotations

from typing import Iterable


class ProductResolutionError(Exception):
    pass


class ProductNotFound(ProductResolutionError):
    pass


class AmbiguousProduct(ProductResolutionError):
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        super().__init__("more than one live BTC perpetual: " + ", ".join(symbols))


def _asset_symbol(product: dict, key: str) -> str | None:
    asset = product.get(key)
    if isinstance(asset, dict):
        symbol = asset.get("symbol")
        return str(symbol) if symbol else None
    return None


def is_btc_perpetual(product: dict) -> bool:
    if product.get("contract_type") != "perpetual_futures":
        return False
    if product.get("state") != "live":
        return False
    if product.get("settlement_time"):
        return False
    symbol = str(product.get("symbol") or "")
    if symbol.startswith(("C-", "P-")) or "_" in symbol:
        return False
    if _asset_symbol(product, "underlying_asset") != "BTC":
        return False
    unit = product.get("contract_unit_currency")
    if unit not in (None, "BTC"):
        return False
    return True


def select_btc_perpetual(products: Iterable[dict]) -> dict:
    matches = [product for product in products if is_btc_perpetual(product)]
    if not matches:
        raise ProductNotFound(
            "no live BTC perpetual futures contract in the Delta product catalogue"
        )
    if len(matches) == 1:
        return matches[0]
    usd = [
        product
        for product in matches
        if _asset_symbol(product, "quoting_asset") == "USD" and product.get("symbol") == "BTCUSD"
    ]
    other_usd = [
        product
        for product in matches
        if _asset_symbol(product, "quoting_asset") == "USD" and product.get("symbol") != "BTCUSD"
    ]
    if len(usd) == 1 and not other_usd:
        return usd[0]
    raise AmbiguousProduct([str(product.get("symbol")) for product in matches])


def public_metadata(product: dict) -> dict:
    """Fields the API may show. Leverage and margin settings are not included."""
    spot = product.get("spot_index") if isinstance(product.get("spot_index"), dict) else {}
    return {
        "exchange_product_id": int(product["id"]),
        "symbol": product.get("symbol"),
        "contract_type": product.get("contract_type"),
        "state": product.get("state"),
        "trading_status": product.get("trading_status"),
        "underlying_asset": _asset_symbol(product, "underlying_asset"),
        "quoting_asset": _asset_symbol(product, "quoting_asset"),
        "settling_asset": _asset_symbol(product, "settling_asset"),
        "tick_size": product.get("tick_size"),
        "contract_value": product.get("contract_value"),
        "contract_unit_currency": product.get("contract_unit_currency"),
        "notional_type": product.get("notional_type"),
        "settlement_time": product.get("settlement_time"),
        "launch_time": product.get("launch_time"),
        "description": product.get("description"),
        "short_description": product.get("short_description"),
        "spot_index_symbol": spot.get("symbol"),
    }
