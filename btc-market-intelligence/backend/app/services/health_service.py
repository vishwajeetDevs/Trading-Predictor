from datetime import timedelta

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.timeutil import RESOLUTIONS, as_utc, utcnow
from app.data.candle_builder import missing_opens
from app.database.repositories import MarketRepository
from app.database.session import describe_db_error
from app.services.ingestion_service import RuntimeStatus


def build_health(session: Session, settings: Settings, status: RuntimeStatus, now=None) -> dict:
    moment = as_utc(now or utcnow())
    repo = MarketRepository(session)
    database_ok = False
    database_error = None
    try:
        database_ok = repo.ping()
    except Exception as exc:
        database_error = describe_db_error(exc)
    product = repo.get_product() if database_ok else None
    latest = repo.latest_closed(product.id, "1m") if product else None
    freshness = None
    stale = True
    gaps = 0
    if latest is not None:
        closed_at = as_utc(latest.open_time) + timedelta(seconds=RESOLUTIONS["1m"])
        freshness = (moment - closed_at).total_seconds()
        stale = freshness > settings.stale_after_seconds
        opens = repo.recent_closed_opens(product.id, "1m", limit=300)
        gaps = len(missing_opens(opens, RESOLUTIONS["1m"]))
    heartbeat_age = None
    if status.last_heartbeat_at is not None:
        heartbeat_age = (moment - as_utc(status.last_heartbeat_at)).total_seconds()
    return {
        "status": "ok" if database_ok and not stale and gaps == 0 else "degraded",
        "database": {"connected": database_ok, "error": database_error},
        "websocket": {
            "connected": status.ws_connected,
            "subscriptions": status.subscriptions or [],
            "last_message_at": _iso(status.last_message_at),
            "last_heartbeat_at": _iso(status.last_heartbeat_at),
            "heartbeat_age_seconds": heartbeat_age,
            "system_status": status.system_status,
            "last_error": status.last_error,
            "reconnect_count": status.reconnect_count,
            "recovery_succeeded": status.recovery_succeeded,
            "recovery_failed": status.recovery_failed,
            "last_recovery_at": _iso(status.last_recovery_at),
            "last_recovery_status": status.last_recovery_status,
            "last_recovery_error": status.last_recovery_error,
        },
        "latest_market_event_at": _iso(status.last_message_at) or _iso(latest.received_at if latest else None),
        "latest_closed_candle_at": _iso(latest.open_time if latest else None),
        "freshness_seconds": freshness,
        "stale": stale,
        "recent_missing_candles": gaps,
        "gaps": gaps > 0,
        "checked_at": moment.isoformat(),
    }


def build_status(session: Session) -> dict:
    repo = MarketRepository(session)
    product = repo.get_product()
    since = utcnow() - timedelta(hours=24)
    return {
        "product": None
        if product is None
        else {"symbol": product.symbol, "exchange_product_id": product.exchange_product_id},
        "streams": [
            {
                "timeframe": row.timeframe,
                "stream": row.stream,
                "status": row.status,
                "oldest_open_time": _iso(row.oldest_open_time),
                "newest_open_time": _iso(row.newest_open_time),
                "last_success_at": _iso(row.last_success_at),
                "detail": row.detail,
            }
            for row in repo.list_states()
        ],
        "quality_events_24h": repo.quality_counts(since),
    }


def build_diagnostics(session: Session, status: RuntimeStatus) -> dict:
    repo = MarketRepository(session)
    summary = repo.storage_summary()
    summary["latest_received_at"] = _iso(summary.get("latest_received_at"))
    summary["recent_quality_events"] = [
        {
            "occurred_at": _iso(row.occurred_at),
            "kind": row.kind,
            "timeframe": row.timeframe,
            "detail": row.detail,
        }
        for row in repo.recent_quality(15)
    ]
    summary["websocket"] = {
        "connected": status.ws_connected,
        "reconnect_count": status.reconnect_count,
        "last_message_at": _iso(status.last_message_at),
        "last_error": status.last_error,
    }
    return summary


def probe_public_rest(session: Session, settings: Settings) -> dict:
    """One small public candle read. The result is not stored."""
    from app.core.timeutil import floor_epoch
    from app.data.delta_rest import DeltaRestClient

    moment = utcnow()
    product = MarketRepository(session).get_product()
    if product is None:
        return {"ok": False, "error": "no BTC product is stored", "checked_at": moment.isoformat()}
    end = floor_epoch(moment, 60)
    start = end - 180
    try:
        with DeltaRestClient(
            settings.delta_rest_base,
            settings.user_agent,
            timeout=settings.http_timeout_seconds,
            min_interval=0,
            max_retries=1,
        ) as client:
            payload = client.fetch_candles(product.symbol, "1m", start, end)
    except Exception as exc:
        return {"ok": False, "symbol": product.symbol, "error": str(exc)[:240], "checked_at": moment.isoformat()}
    rows = payload.get("result") if isinstance(payload, dict) else None
    return {
        "ok": bool(isinstance(payload, dict) and payload.get("success") is True and rows),
        "symbol": product.symbol,
        "candle_count": len(rows or []),
        "checked_at": moment.isoformat(),
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()
