"""Load stored candles, persist studies, and build the read-only snapshot.

The HTTP snapshot does not rescan the full history. `run_studies` does that work.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.definitions import (
    BOOTSTRAP_BLOCK,
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_HORIZONS,
    BOOTSTRAP_SEED,
    DEFINITION_VERSION,
    HORIZONS,
    HIGHER_TIMEFRAME,
    MIN_SAMPLE,
    PIVOT_WIDTH,
    SNAPSHOT_BARS,
    TAIL_LOOKBACK,
    TAIL_TIMEFRAMES,
    TIMEFRAMES,
    VOL_TIMEFRAMES,
    Bar,
    definition_hash,
    valid_bar,
)
from app.analysis.events import breakout_events
from app.analysis.snapshot import build_levels, comparison, public_view, timeframe_view
from app.analysis.stats import block_bootstrap, compare_base_rate, follow_through, summarize_sample
from app.analysis.studies import extreme_downside_events, latest_tail_match, measure_forward, partition_events, unconditional_returns, volatility_events
from app.core.timeutil import RESOLUTIONS, as_utc, utcnow
from app.database.models import AnalysisSnapshot, Candle, ConditionMatch, HorizonStat, StudyRun
from app.database.repositories import MarketRepository, json_safe
from app.services.health_service import build_health

log = logging.getLogger(__name__)
FORBIDDEN = ("buy", "sell", "stop-loss", "take-profit", "leverage", "order")


def _load(session: Session, product_id: int, timeframe: str, limit: int | None, since=None) -> list[Bar]:
    query = select(Candle.open_time, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume).where(
        Candle.product_id == product_id, Candle.timeframe == timeframe, Candle.is_closed.is_(True)
    )
    if since is not None:
        query = query.where(Candle.open_time >= since)
    if limit is None:
        query = query.order_by(Candle.open_time.asc())
    else:
        query = query.order_by(Candle.open_time.desc()).limit(limit)
    rows = list(session.execute(query).all())
    if limit is not None:
        rows.reverse()
    bars: list[Bar] = []
    for open_time, opened, high, low, close, volume in rows:
        bar = Bar(as_utc(open_time), float(opened), float(high), float(low), float(close), float(volume))
        if valid_bar(bar):
            bars.append(bar)
    return bars


def _chunked(session: Session, model, rows: list[dict]) -> None:
    for start in range(0, len(rows), 400):
        if rows[start : start + 400]:
            session.execute(model.__table__.insert(), rows[start : start + 400])


def run_studies(session: Session) -> dict:
    started = time.perf_counter()
    product = MarketRepository(session).get_product()
    if product is None:
        return {"status": "no_product"}
    digest = definition_hash()
    latest = MarketRepository(session).latest_closed(product.id, "1m")
    watermark = None if latest is None else as_utc(latest.open_time)
    existing = session.scalar(
        select(StudyRun)
        .where(StudyRun.product_id == product.id, StudyRun.definition_hash == digest, StudyRun.watermark_open_time == watermark)
        .limit(1)
    )
    if existing is not None and existing.status == "ok":
        return {"status": "current", "definition_hash": digest, "watermark": None if watermark is None else watermark.isoformat()}
    product_id = product.id
    series = {timeframe: _load(session, product_id, timeframe, None) for timeframe in TIMEFRAMES}
    minutes = {int(bar.open_time.timestamp()): bar for bar in series.get("1m", [])}
    # The scan is CPU-bound. Release the connection first so a hosted Postgres
    # idle timeout cannot drop it before the write.
    session.commit()
    session.close()
    now = utcnow()
    match_rows: list[dict] = []
    stat_rows: list[dict] = []
    counts: dict[str, dict[str, int]] = {}
    for timeframe in TIMEFRAMES:
        bars = series[timeframe]
        step = RESOLUTIONS[timeframe]
        events = breakout_events(bars, PIVOT_WIDTH[timeframe]) if len(bars) > PIVOT_WIDTH[timeframe] * 2 else []
        if timeframe in VOL_TIMEFRAMES:
            events.extend(volatility_events(bars))
        if timeframe in TAIL_TIMEFRAMES:
            events.extend(extreme_downside_events(bars, TAIL_LOOKBACK[timeframe]))
        deduped = []
        seen = set()
        for event in events:
            key = (event["condition"], event["open_time"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        counts[timeframe] = {}
        base_by_horizon = {
            horizon: unconditional_returns(bars, minutes, step, seconds) if minutes else {"return": [], "excluded": 0}
            for horizon, seconds in HORIZONS
        }
        by_condition: dict[str, list] = {}
        for event in deduped:
            counts[timeframe][event["condition"]] = counts[timeframe].get(event["condition"], 0) + 1
            by_condition.setdefault(event["condition"], []).append(event)
            match_rows.append(
                {
                    "product_id": product_id,
                    "definition_hash": digest,
                    "timeframe": timeframe,
                    "condition": event["condition"],
                    "open_time": event["open_time"],
                    "direction": int(event.get("direction") or 0),
                    "close_price": event["close"],
                }
            )
        for condition, raw_events in by_condition.items():
            for horizon, seconds in HORIZONS:
                parts = partition_events(raw_events, minutes, step, seconds) if minutes else {
                    "independent": [],
                    "independent_count": 0,
                    "clustered_out": 0,
                    "excluded": len(raw_events),
                    "raw_count": len(raw_events),
                }
                measured = measure_forward(parts["independent"], minutes, step, [(horizon, seconds)]) if parts["independent"] else {}
                pack = measured.get(condition, {}).get(horizon, {"return": [], "mfe": [], "mae": [], "excluded": 0})
                summary = summarize_sample(pack["return"], pack["mfe"], pack["mae"])
                base = base_by_horizon[horizon]
                base_positive = None if len(base["return"]) < MIN_SAMPLE else sum(1 for item in base["return"] if item > 0) / len(base["return"])
                compared = compare_base_rate(summary["positive_rate"], base_positive, summary["sample_size"], len(base["return"]))
                bootstrap = None
                if horizon in BOOTSTRAP_HORIZONS and summary["status"] == "OK":
                    bootstrap = block_bootstrap(
                        pack["return"],
                        BOOTSTRAP_SEED,
                        BOOTSTRAP_BLOCK,
                        BOOTSTRAP_DRAWS,
                        sampling_interval_seconds=seconds,
                    )
                kept = parts["independent"]
                stat_rows.append(
                    {
                        "product_id": product_id,
                        "definition_hash": digest,
                        "timeframe": timeframe,
                        "condition": condition,
                        "horizon": horizon,
                        "status": summary["status"],
                        "sample_size": summary["sample_size"],
                        "raw_match_count": len(raw_events),
                        "independent_count": parts["independent_count"],
                        "clustered_out": parts["clustered_out"],
                        "comparison_status": compared["status"],
                        "excluded": parts["excluded"],
                        "first_match_at": min((item["open_time"] for item in kept), default=None),
                        "last_match_at": max((item["open_time"] for item in kept), default=None),
                        "median_return": summary["median_return"],
                        "p10_return": summary["p10_return"],
                        "p90_return": summary["p90_return"],
                        "positive_rate": summary["positive_rate"],
                        "negative_rate": summary["negative_rate"],
                        "median_mfe": summary["median_mfe"],
                        "median_mae": summary["median_mae"],
                        "base_positive_rate": compared["base_positive_rate"],
                        "base_sample_size": len(base["return"]),
                        "rate_difference": compared["difference"],
                        "explanation": summary["explanation"] or compared["explanation"],
                        "bootstrap": bootstrap,
                        "computed_at": now,
                    }
                )
    session.execute(delete(ConditionMatch).where(ConditionMatch.product_id == product_id, ConditionMatch.definition_hash == digest))
    session.execute(delete(HorizonStat).where(HorizonStat.product_id == product_id, HorizonStat.definition_hash == digest))
    _chunked(session, ConditionMatch, match_rows)
    _chunked(session, HorizonStat, stat_rows)
    detail = {"counts": counts, "seconds": round(time.perf_counter() - started, 3), "matches": len(match_rows)}
    session.add(
        StudyRun(
            product_id=product_id,
            definition_hash=digest,
            watermark_open_time=watermark,
            computed_at=now,
            status="ok",
            detail=json_safe(detail),
        )
    )
    session.add(
        AnalysisSnapshot(
            product_id=product_id,
            created_at=now,
            definition_hash=digest,
            watermark_open_time=watermark,
            payload=json_safe({"counts": counts, "match_count": len(match_rows), "stat_count": len(stat_rows)}),
        )
    )
    session.commit()
    detail["status"] = "computed"
    detail["definition_hash"] = digest
    log.info("study computed matches=%s seconds=%s", len(match_rows), detail["seconds"])
    return detail


def _comparison_text(status: str | None, explanation: str | None) -> str | None:
    if status != "NOT_COMPARABLE":
        return explanation
    if explanation and explanation.startswith("NOT_COMPARABLE"):
        return explanation
    if explanation:
        return "NOT_COMPARABLE. " + explanation
    return "NOT_COMPARABLE. A difference is withheld."


def _study_clock(run: StudyRun | None, market_watermark) -> dict:
    if run is None:
        return {
            "status": "NOT_COMPUTED",
            "watermark": None,
            "computed_at": None,
            "lag_seconds": None,
            "up_to_date": False,
            "explanation": "No persisted study exists for this definition yet. Run the study command.",
        }
    study_watermark = None if run.watermark_open_time is None else as_utc(run.watermark_open_time)
    lag = None
    if study_watermark is not None and market_watermark is not None:
        lag = (as_utc(market_watermark) - study_watermark).total_seconds()
    status = "CURRENT" if lag is not None and lag <= 0 else "LAGGING"
    return {
        "status": status,
        "watermark": None if study_watermark is None else study_watermark.isoformat(),
        "computed_at": as_utc(run.computed_at).isoformat(),
        "lag_seconds": lag,
        "up_to_date": status == "CURRENT",
        "explanation": "The stored study uses the latest closed 1-minute candle."
        if status == "CURRENT"
        else "The stored study watermark is behind the latest closed 1-minute candle. A lagged study is not reported as up to date.",
    }


def _stats_payload(session: Session, product_id: int, digest: str, market_watermark) -> dict:
    run = session.scalar(
        select(StudyRun)
        .where(StudyRun.product_id == product_id, StudyRun.definition_hash == digest)
        .order_by(StudyRun.computed_at.desc())
        .limit(1)
    )
    clock = _study_clock(run, market_watermark)
    if run is None:
        return clock
    rows = session.scalars(select(HorizonStat).where(HorizonStat.product_id == product_id, HorizonStat.definition_hash == digest)).all()
    grouped: dict[str, dict] = {}
    for row in rows:
        grouped.setdefault(row.timeframe, {}).setdefault(row.condition, {})[row.horizon] = {
            "status": row.status,
            "sample_size": row.sample_size,
            "raw_match_count": row.raw_match_count,
            "independent_count": row.independent_count,
            "clustered_out": row.clustered_out,
            "excluded": row.excluded,
            "sampling": {
                "method": "cooldown",
                "cooldown": "one forward horizon",
                "observation_unit": row.timeframe,
                "note": "Raw matches can have overlapping forward windows. Frequencies use the de-clustered sample only and do not treat the raw matches as independent events.",
            },
            "first_match_at": None if row.first_match_at is None else as_utc(row.first_match_at).isoformat(),
            "last_match_at": None if row.last_match_at is None else as_utc(row.last_match_at).isoformat(),
            "median_return": None if row.median_return is None else float(row.median_return),
            "p10_return": None if row.p10_return is None else float(row.p10_return),
            "p90_return": None if row.p90_return is None else float(row.p90_return),
            "positive_rate": None if row.positive_rate is None else float(row.positive_rate),
            "negative_rate": None if row.negative_rate is None else float(row.negative_rate),
            "median_mfe": None if row.median_mfe is None else float(row.median_mfe),
            "median_mae": None if row.median_mae is None else float(row.median_mae),
            "base_rate": {
                "status": row.comparison_status or "NOT_COMPARABLE",
                "timeframe": row.timeframe,
                "horizon": row.horizon,
                "observation_unit": "closed %s bar" % row.timeframe,
                "positive_rate": None if row.base_positive_rate is None else float(row.base_positive_rate),
                "sample_size": row.base_sample_size,
                "difference": None if row.rate_difference is None else float(row.rate_difference),
                "explanation": _comparison_text(row.comparison_status, row.explanation),
            },
            "bootstrap": row.bootstrap,
            "follow_through": follow_through(None if row.median_return is None else float(row.median_return))
            if row.condition == "extreme_downside"
            else None,
        }
    clock["definition_hash"] = digest
    clock["by_timeframe"] = grouped
    return clock


def _day_bars(session: Session, product_id: int, window: list[Bar]) -> list[Bar]:
    """Closed 1-minute bars from the previous UTC midnight through the latest bar.

    The indicator window is the latest 2000 bars. After about 09:20 UTC that window no longer
    contains the whole previous UTC day, so the day extremes are loaded separately.
    """
    if not window:
        return []
    latest = as_utc(window[-1].open_time)
    start = datetime(latest.year, latest.month, latest.day, tzinfo=timezone.utc) - timedelta(days=1)
    if as_utc(window[0].open_time) <= start:
        return [bar for bar in window if as_utc(bar.open_time) >= start]
    return _load(session, product_id, "1m", None, since=start)


def build_snapshot(session: Session, settings, runtime) -> dict:
    product = MarketRepository(session).get_product()
    if product is None:
        return {"status": "no_product"}
    digest = definition_hash()
    health = build_health(session, settings, runtime)
    market_watermark = None
    if health["latest_closed_candle_at"]:
        market_watermark = as_utc(datetime.fromisoformat(health["latest_closed_candle_at"]))
    loaded = {timeframe: _load(session, product.id, timeframe, SNAPSHOT_BARS) for timeframe in TIMEFRAMES}
    raw = {}
    for timeframe in ("1d", "4h", "1h", "15m", "5m", "1m"):
        higher_name = HIGHER_TIMEFRAME.get(timeframe)
        higher = None if higher_name not in raw else {"structure": raw[higher_name]["structure_object"]}
        raw[timeframe] = timeframe_view(timeframe, loaded[timeframe], higher)
    day_bars = _day_bars(session, product.id, loaded["1m"])
    levels = {timeframe: build_levels(timeframe, raw[timeframe], day_bars) for timeframe in TIMEFRAMES}
    tail = {timeframe: latest_tail_match(loaded[timeframe], TAIL_LOOKBACK[timeframe]) for timeframe in TAIL_TIMEFRAMES}
    warnings = []
    for timeframe, view in raw.items():
        warnings.extend(view["warnings"])
        for name, value in view["indicators"].items():
            if value is None:
                warnings.append("%s %s is insufficient_history." % (timeframe, name))
    studies = _stats_payload(session, product.id, digest, market_watermark)
    study_clock = {
        key: studies[key]
        for key in ("status", "watermark", "computed_at", "lag_seconds", "up_to_date", "explanation")
        if key in studies
    }
    body = {
        "read_only": True,
        "product": {
            "symbol": product.symbol,
            "exchange_product_id": product.exchange_product_id,
            "contract_type": product.contract_type,
            "state": product.state,
        },
        "freshness": {
            "status": health["status"],
            "stale": health["stale"],
            "freshness_seconds": health["freshness_seconds"],
            "latest_closed_candle_at": health["latest_closed_candle_at"],
            "market": {
                "status": health["status"],
                "stale": health["stale"],
                "freshness_seconds": health["freshness_seconds"],
                "watermark": health["latest_closed_candle_at"],
            },
            "study": study_clock,
        },
        "data_watermark": health["latest_closed_candle_at"],
        "definition_hash": digest,
        "definition_version": DEFINITION_VERSION,
        "timeframes": {name: public_view(raw[name]) for name in TIMEFRAMES},
        "comparison": comparison(raw),
        "levels": levels,
        "studies": studies,
        "crash_screen": {
            "definition": "A closed 1h or 4h log return at or below the 5th percentile of the prior lookback, using only earlier returns.",
            "timeframes": tail,
            "note": "A match describes the historical tail rule. It is a description of stored history, not a forecast.",
        },
        "warnings": warnings,
        "snapshot_bars": SNAPSHOT_BARS,
    }
    return json_safe(body)


def list_snapshots(session: Session, limit: int) -> dict:
    product = MarketRepository(session).get_product()
    if product is None:
        return {"count": 0, "snapshots": []}
    rows = session.scalars(
        select(AnalysisSnapshot)
        .where(AnalysisSnapshot.product_id == product.id)
        .order_by(AnalysisSnapshot.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "count": len(rows),
        "snapshots": [
            {
                "id": row.id,
                "created_at": as_utc(row.created_at).isoformat(),
                "definition_hash": row.definition_hash,
                "watermark": None if row.watermark_open_time is None else as_utc(row.watermark_open_time).isoformat(),
                "payload": row.payload,
            }
            for row in rows
        ],
    }
