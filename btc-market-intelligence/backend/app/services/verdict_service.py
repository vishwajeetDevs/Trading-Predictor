"""Load market data for the verdict engine, run it, cache briefly, and persist each run."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.engine import pipeline
from app.analysis.engine.regime import synthesize_weekly
from app.analysis.engine.types import AnalyzeRequest, MarketData, Position
from app.analysis.definitions import DEFINITION_VERSION, definition_hash
from app.core.timeutil import utcnow
from app.database.models import HorizonStat, VerdictRun
from app.database.repositories import MarketRepository, json_safe
from app.services.derivatives_service import snapshot_view
from app.services.health_service import build_health
from app.services.study_service import _load

log = logging.getLogger(__name__)

# Bars loaded per timeframe. Enough for EMA200 on 1d and ~1 week of minutes.
BAR_LIMITS = {"1m": 1500, "5m": 900, "15m": 700, "1h": 600, "4h": 500, "1d": 400}

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_key(request: AnalyzeRequest) -> tuple:
    position = None if request.position is None else (request.position.side, request.position.entry_price, request.position.size)
    return (request.focus_timeframe, request.horizon, position, request.account_size, request.risk_percent)


def load_empirical(session: Session, product_id: int, timeframe: str) -> dict:
    """{condition: {horizon: {...}}} from the latest study definition."""
    rows = session.scalars(
        select(HorizonStat).where(HorizonStat.product_id == product_id, HorizonStat.timeframe == timeframe, HorizonStat.definition_hash == definition_hash())
    ).all()
    out: dict = {}
    for row in rows:
        out.setdefault(row.condition, {})[row.horizon] = {
            "status": row.status,
            "positive_rate": None if row.positive_rate is None else float(row.positive_rate),
            "sample_size": int(row.sample_size),
            "base_positive_rate": None if row.base_positive_rate is None else float(row.base_positive_rate),
            "base_sample_size": row.base_sample_size,
            "median_return": None if row.median_return is None else float(row.median_return),
            "definition_version": DEFINITION_VERSION,
        }
    return out


def load_market_data(session: Session, settings, runtime, product) -> MarketData:
    bars = {}
    for timeframe, limit in BAR_LIMITS.items():
        try:
            bars[timeframe] = _load(session, product.id, timeframe, limit)
        except Exception:
            log.exception("failed loading %s bars", timeframe)
            bars[timeframe] = []
    if bars.get("1d"):
        bars["1w"] = synthesize_weekly(bars["1d"])
    derivatives = None
    try:
        derivatives = json_safe(snapshot_view(session, settings))
    except Exception:
        log.exception("derivatives snapshot unavailable for verdict")
    health = None
    try:
        health = build_health(session, settings, runtime)
    except Exception:
        log.exception("health unavailable for verdict")
    empirical = None
    try:
        empirical = load_empirical(session, product.id, "15m")
    except Exception:
        log.exception("empirical prior unavailable for verdict")
    product_view = {
        "symbol": product.symbol,
        "contract_value": product.contract_value,
        "tick_size": product.tick_size,
        "contract_unit_currency": product.contract_unit_currency,
    }
    return MarketData(bars=bars, minute_bars=bars.get("1m", []), now=utcnow(), derivatives=derivatives, health=health, empirical=empirical, product=product_view)


def to_request(body, settings) -> AnalyzeRequest:
    position = None
    if body.position is not None:
        position = Position(side=body.position.side, entry_price=float(body.position.entry_price), size=body.position.size)
    account = body.account_size if body.account_size is not None else settings.default_account_size
    return AnalyzeRequest(
        focus_timeframe=body.focus_timeframe,
        horizon=body.horizon,
        position=position,
        account_size=account,
        risk_percent=float(body.risk_percent if body.risk_percent is not None else settings.default_risk_percent),
    )


def compute_verdict(session: Session, settings, runtime, request: AnalyzeRequest, force_refresh: bool = False) -> dict:
    product = MarketRepository(session).get_product()
    if product is None:
        return {"status": "no_product"}
    key = _cache_key(request)
    now = time.monotonic()
    if not force_refresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and now - hit[0] < settings.verdict_cache_seconds:
            return {**hit[1], "cached": True}
    data = load_market_data(session, settings, runtime, product)
    result = pipeline.analyze(data, request)
    result = json_safe(result)
    run_id = None
    try:
        run = VerdictRun(
            product_id=product.id,
            computed_at=datetime.fromisoformat(result["computed_at"]),
            engine_version=result["engine_version"],
            focus_timeframe=result["focus_timeframe"],
            horizon=result["horizon"],
            regime=result.get("regime"),
            close=result.get("close"),
            p_bull=result["probabilities"]["p_bull"],
            p_bear=result["probabilities"]["p_bear"],
            p_range=result["probabilities"]["p_range"],
            confidence=result["confidence"],
            verdict=result["verdict"]["verdict"],
            payload=result,
        )
        session.add(run)
        session.commit()
        run_id = run.id
    except Exception:
        log.exception("failed to persist verdict run")
        session.rollback()
    result["run_id"] = run_id
    result["cached"] = False
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def list_runs(session: Session, limit: int) -> list[dict]:
    rows = session.scalars(select(VerdictRun).order_by(VerdictRun.computed_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "computed_at": row.computed_at.isoformat(),
            "focus_timeframe": row.focus_timeframe,
            "horizon": row.horizon,
            "regime": row.regime,
            "close": None if row.close is None else float(row.close),
            "p_bull": None if row.p_bull is None else float(row.p_bull),
            "p_bear": None if row.p_bear is None else float(row.p_bear),
            "p_range": None if row.p_range is None else float(row.p_range),
            "confidence": None if row.confidence is None else float(row.confidence),
            "verdict": row.verdict,
            "resolved_at": None if row.resolved_at is None else row.resolved_at.isoformat(),
            "outcome": row.outcome,
        }
        for row in rows
    ]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
