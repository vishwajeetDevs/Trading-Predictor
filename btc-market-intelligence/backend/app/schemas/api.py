from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class PositionInput(BaseModel):
    side: Literal["long", "short"]
    entry_price: float = Field(gt=0)
    size: float | None = Field(default=None, gt=0)


class VerdictRequest(BaseModel):
    focus_timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1d"] = "15m"
    horizon: Literal["15m", "30m", "1h", "4h", "24h"] = "4h"
    position: PositionInput | None = None
    account_size: float | None = Field(default=None, gt=0)
    risk_percent: float = Field(default=1.0, gt=0, le=10)
    force_refresh: bool = False


class VerdictDecision(BaseModel):
    verdict: Literal["ENTER_NOW", "WAIT_FOR_BETTER_ENTRY", "AVOID_HIGH_RISK", "HOLD", "CONSIDER_EXIT"]
    label: str
    directional_probability: float
    confidence: float
    reasons: list[str]
    risks: list[str]
    what_changes_this_view: list[str]
    position: dict | None
    disclaimer: str


class VerdictResponse(BaseModel):
    """Loose envelope; the layer payloads are documented in docs/ANALYSIS_ENGINE_METHODOLOGY.md."""

    model_config = ConfigDict(extra="allow")

    engine_version: str
    focus_timeframe: str
    horizon: str
    computed_at: str
    close: float | None
    market_read: dict
    verdict: VerdictDecision
    probabilities: dict
    confidence: float
    why_bullish: list[dict]
    why_bearish: list[dict]
    risks: list[str]
    what_changes_this_view: list[str]
    trade_plan: dict
    layers: list[dict]
    layer_summaries: list[dict]
    data_freshness: dict
    disclaimer: str
    cached: bool = False
    run_id: int | None = None


def decimal_text(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")
