from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: dict
    websocket: dict
    latest_market_event_at: str | None
    latest_closed_candle_at: str | None
    freshness_seconds: float | None
    stale: bool
    recent_missing_candles: int
    gaps: bool
    checked_at: str


class ProductResponse(BaseModel):
    exchange_product_id: int
    symbol: str
    contract_type: str
    state: str
    trading_status: str | None
    underlying_asset: str | None
    quoting_asset: str | None
    settling_asset: str | None
    tick_size: str | None
    contract_value: str | None
    contract_unit_currency: str | None
    notional_type: str | None
    spot_index_symbol: str | None
    description: str | None
    short_description: str | None = None
    launch_time: str | None = None
    settlement_time: str | None = None
    resolved_at: datetime
    updated_at: datetime


class CandleResponse(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    open: str
    high: str
    low: str
    close: str
    volume: str
    source: str
    is_closed: bool
    timestamp_unit: str
    received_at: datetime


class CandleListResponse(BaseModel):
    symbol: str | None
    timeframe: str
    count: int
    candles: list[CandleResponse]


class StatusResponse(BaseModel):
    product: dict | None
    streams: list[dict]
    quality_events_24h: dict


def decimal_text(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")
