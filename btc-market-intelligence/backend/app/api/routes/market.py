from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_runtime, get_settings
from app.core.timeutil import RESOLUTIONS, as_utc, from_epoch_seconds
from app.database.repositories import MarketRepository
from app.schemas.api import (
    CandleListResponse,
    CandleResponse,
    HealthResponse,
    ProductResponse,
    StatusResponse,
    decimal_text,
)
from app.services.health_service import build_diagnostics, build_health, build_status, probe_public_rest
from app.services.live_service import live_view

router = APIRouter()


@router.get("/")
def index():
    return {
        "service": "btc-market-intelligence",
        "read_only": True,
        "endpoints": [
            "/health",
            "/products/btc",
            "/candles",
            "/data/status",
            "/diagnostics",
            "/diagnostics/delta",
            "/analysis/snapshot",
            "/analysis/history",
            "/analysis/verdict",
            "/analysis/decision",
            "/analysis/verdict/history",
            "/market/live",
            "/derivatives/snapshot",
            "/derivatives/funding",
            "/derivatives/open-interest",
            "/derivatives/orderbook",
            "/derivatives/mark-price",
        ],
        "dashboard": "http://127.0.0.1:5173/",
    }


def _parse_time(value: str | None):
    if value is None or value == "":
        return None
    if value.isdigit():
        return from_epoch_seconds(int(value))
    from datetime import datetime

    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return as_utc(moment)


@router.get("/market/live")
def market_live(timeframe: str = Query("1m"), session: Session = Depends(get_db), runtime=Depends(get_runtime)):
    """Forming bar and last traded price from the public socket. Not a closed candle."""
    if timeframe not in RESOLUTIONS:
        raise HTTPException(status_code=422, detail="timeframe must be one of %s" % ", ".join(RESOLUTIONS))
    return live_view(session, runtime, timeframe)


@router.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_db), settings=Depends(get_settings), runtime=Depends(get_runtime)):
    return build_health(session, settings, runtime)


@router.get("/products/btc", response_model=ProductResponse)
def btc_product(session: Session = Depends(get_db)):
    product = MarketRepository(session).get_product()
    if product is None:
        raise HTTPException(status_code=404, detail="BTC perpetual is not in the database yet. Run the backfill command.")
    raw = product.raw_metadata or {}
    return ProductResponse(
        exchange_product_id=product.exchange_product_id,
        symbol=product.symbol,
        contract_type=product.contract_type,
        state=product.state,
        trading_status=product.trading_status,
        underlying_asset=product.underlying_asset,
        quoting_asset=product.quoting_asset,
        settling_asset=product.settling_asset,
        tick_size=product.tick_size,
        contract_value=product.contract_value,
        contract_unit_currency=product.contract_unit_currency,
        notional_type=product.notional_type,
        spot_index_symbol=product.spot_index_symbol,
        description=product.description,
        short_description=raw.get("short_description"),
        launch_time=raw.get("launch_time"),
        settlement_time=raw.get("settlement_time"),
        resolved_at=product.resolved_at,
        updated_at=product.updated_at,
    )


@router.get("/candles", response_model=CandleListResponse)
def candles(
    timeframe: str = Query("1m"),
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    include_forming: bool = False,
    session: Session = Depends(get_db),
):
    if timeframe not in RESOLUTIONS:
        raise HTTPException(status_code=422, detail="timeframe must be one of %s" % ", ".join(RESOLUTIONS))
    product = MarketRepository(session).get_product()
    if product is None:
        return CandleListResponse(symbol=None, timeframe=timeframe, count=0, candles=[])
    rows = MarketRepository(session).candles(
        product.id,
        timeframe,
        _parse_time(start),
        _parse_time(end),
        limit,
        closed_only=not include_forming,
        descending=order == "desc",
    )
    payload = [
        CandleResponse(
            symbol=row.symbol,
            timeframe=row.timeframe,
            timestamp=row.open_time,
            open=decimal_text(row.open),
            high=decimal_text(row.high),
            low=decimal_text(row.low),
            close=decimal_text(row.close),
            volume=decimal_text(row.volume),
            source=row.source,
            is_closed=row.is_closed,
            timestamp_unit=row.timestamp_unit,
            received_at=row.received_at,
        )
        for row in rows
    ]
    return CandleListResponse(symbol=product.symbol, timeframe=timeframe, count=len(payload), candles=payload)


@router.get("/data/status", response_model=StatusResponse)
def data_status(session: Session = Depends(get_db)):
    return build_status(session)


@router.get("/diagnostics")
def diagnostics(session: Session = Depends(get_db), runtime=Depends(get_runtime)):
    return build_diagnostics(session, runtime)


@router.get("/diagnostics/delta")
def delta_status(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return probe_public_rest(session, settings)
