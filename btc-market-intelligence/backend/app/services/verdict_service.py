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
# One in-flight computation per request shape; concurrent pollers reuse the latest result.
_inflight: dict[tuple, threading.Lock] = {}

# Derivatives snapshot and health are many small remote queries and change slowly,
# so they are reused across verdict runs for a short window.
CONTEXT_CACHE_SECONDS = 60.0
_context_lock = threading.Lock()
_context: dict[str, tuple[float, object]] = {}


def _context_cached(name: str, loader, ttl: float = CONTEXT_CACHE_SECONDS):
    now = time.monotonic()
    with _context_lock:
        hit = _context.get(name)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = loader()
    with _context_lock:
        _context[name] = (now, value)
    return value


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
    started = time.monotonic()
    for timeframe, limit in BAR_LIMITS.items():
        try:
            bars[timeframe] = _load(session, product.id, timeframe, limit)
        except Exception:
            log.exception("failed loading %s bars", timeframe)
            bars[timeframe] = []
    bars_done = time.monotonic()
    if bars.get("1d"):
        bars["1w"] = synthesize_weekly(bars["1d"])
    derivatives = None
    try:
        derivatives = _context_cached("derivatives", lambda: json_safe(snapshot_view(session, settings)))
    except Exception:
        log.exception("derivatives snapshot unavailable for verdict")
    health = None
    try:
        health = _context_cached("health", lambda: build_health(session, settings, runtime))
    except Exception:
        log.exception("health unavailable for verdict")
    context_done = time.monotonic()
    empirical = None
    try:
        empirical = load_empirical(session, product.id, "15m")
    except Exception:
        log.exception("empirical prior unavailable for verdict")
    log.info(
        "verdict data loaded bars=%.0fms context=%.0fms empirical=%.0fms",
        (bars_done - started) * 1000,
        (context_done - bars_done) * 1000,
        (time.monotonic() - context_done) * 1000,
    )
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
    repo = MarketRepository(session)
    product = repo.get_product()
    if product is None:
        return {"status": "no_product"}
    latest = repo.latest_closed(product.id, "1m")
    watermark = None if latest is None else latest.open_time.isoformat()
    # A new closed 1-minute candle invalidates the cache, so the decision follows the tape.
    prefix = _cache_key(request)
    key = prefix + (watermark,)
    now = time.monotonic()
    with _cache_lock:
        gate = _inflight.setdefault(prefix, threading.Lock())
    if not force_refresh:
        hit = _cache_hit(key, now, settings.verdict_cache_seconds)
        if hit is not None:
            return {**hit, "cached": True}
        previous = _latest_for(prefix)
        if previous is not None:
            # A newer candle (or an expired entry) exists but an older result is on hand.
            # Answer immediately with it and refresh once in the background so pollers
            # never queue behind remote data loads; the next poll picks up the new run.
            _refresh_in_background(gate, session, settings, runtime, request, product, key, prefix, watermark)
            return {**previous, "cached": True, "refreshing": True}
    if not gate.acquire(blocking=False):
        # Another request is already recomputing this shape. Serve the most recent
        # result (possibly one candle old) instead of stacking remote loads; only wait
        # when nothing has been computed yet.
        previous = None if force_refresh else _latest_for(prefix)
        if previous is not None:
            return {**previous, "cached": True, "refreshing": True}
        gate.acquire()
        try:
            hit = _cache_hit(key, time.monotonic(), settings.verdict_cache_seconds)
            if hit is not None:
                return {**hit, "cached": True}
            return _compute_locked(session, settings, runtime, request, product, key, prefix, watermark)
        finally:
            gate.release()
    try:
        return _compute_locked(session, settings, runtime, request, product, key, prefix, watermark)
    finally:
        gate.release()


def _refresh_in_background(gate: threading.Lock, session: Session, settings, runtime, request: AnalyzeRequest, product, key: tuple, prefix: tuple, watermark) -> bool:
    """Start one recomputation on a worker thread. Returns False when one is already running."""
    if not gate.acquire(blocking=False):
        return False
    bind = session.get_bind()

    def work() -> None:
        try:
            with Session(bind=bind, autoflush=False, expire_on_commit=False) as worker_session:
                _compute_locked(worker_session, settings, runtime, request, product, key, prefix, watermark)
        except Exception:
            log.exception("background verdict refresh failed")
        finally:
            gate.release()

    threading.Thread(target=work, name="verdict-refresh", daemon=True).start()
    return True


def _cache_hit(key: tuple, now: float, ttl: float) -> dict | None:
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    return None


def _latest_for(prefix: tuple) -> dict | None:
    with _cache_lock:
        matches = [item for item in _cache.items() if item[0][:-1] == prefix]
    if not matches:
        return None
    return max(matches, key=lambda item: item[1][0])[1][1]


def _compute_locked(session: Session, settings, runtime, request: AnalyzeRequest, product, key: tuple, prefix: tuple, watermark) -> dict:
    now = time.monotonic()
    data = load_market_data(session, settings, runtime, product)
    loaded = time.monotonic()
    # Return the pooled connection while the CPU-bound layers run.
    session.close()
    result = pipeline.analyze(data, request)
    analyzed = time.monotonic()
    result["candle_watermark"] = watermark
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
    finished = time.monotonic()
    result["timings_ms"] = {
        "load": round((loaded - now) * 1000, 1),
        "engine": round((analyzed - loaded) * 1000, 1),
        "persist": round((finished - analyzed) * 1000, 1),
    }
    log.info("verdict computed focus=%s horizon=%s load=%.0fms engine=%.0fms persist=%.0fms", request.focus_timeframe, request.horizon, *(result["timings_ms"][name] for name in ("load", "engine", "persist")))
    with _cache_lock:
        stale_keys = [item for item in _cache if item[:-1] == prefix and item != key]
        for item in stale_keys:
            _cache.pop(item, None)
        _cache[key] = (finished, result)
    return result


def decision_view(result: dict) -> dict:
    """Compact decision envelope for polling clients."""
    return {
        "engine_version": result.get("engine_version"),
        "computed_at": result.get("computed_at"),
        "candle_watermark": result.get("candle_watermark"),
        "focus_timeframe": result.get("focus_timeframe"),
        "horizon": result.get("horizon"),
        "close": result.get("close"),
        "verdict": (result.get("verdict") or {}).get("verdict"),
        "market_read": result.get("market_read"),
        "probabilities": {name: (result.get("probabilities") or {}).get(name) for name in ("p_bull", "p_bear", "p_range")},
        "decision": result.get("decision"),
        "data_freshness": result.get("data_freshness"),
        "cached": result.get("cached", False),
        "refreshing": result.get("refreshing", False),
        "timings_ms": result.get("timings_ms"),
        "run_id": result.get("run_id"),
        "disclaimer": result.get("disclaimer"),
    }


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
    with _context_lock:
        _context.clear()
