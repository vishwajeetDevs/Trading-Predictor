"""Live public-socket consumer. Closed candles are stored; holes are filled from REST."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime

import websockets

from app.config.settings import Settings
from app.core.timeutil import RESOLUTIONS, as_utc, utcnow
from app.data.candle_builder import missing_opens
from app.data.delta_rest import DeltaRestClient
from app.data.delta_websocket import (
    StreamParser,
    SubscriptionBook,
    enable_heartbeat_message,
    public_socket_options,
    reconnect_delay,
    seconds_until_heartbeat_deadline,
)
from app.derivatives.normalize import LocalOrderBook
from app.data.normalizer import CandleDraft
from app.database.repositories import MarketRepository
from app.services.backfill_service import BackfillService

log = logging.getLogger(__name__)


@dataclass
class RuntimeStatus:
    ws_connected: bool = False
    last_message_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_error: str | None = None
    subscriptions: list[str] | None = None
    system_status: str | None = None
    reconnect_count: int = 0
    recovery_succeeded: int = 0
    recovery_failed: int = 0
    last_recovery_at: datetime | None = None
    last_recovery_status: str | None = None
    last_recovery_error: str | None = None


runtime_status = RuntimeStatus()


async def run_public_socket(settings: Settings, session_factory, stop: asyncio.Event, status: RuntimeStatus | None = None) -> None:
    status = status or runtime_status
    attempt = 0
    try:
        while not stop.is_set():
            try:
                await _consume(settings, session_factory, stop, status)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                status.reconnect_count += 1
                status.ws_connected = False
                status.last_error = str(exc)
                delay = reconnect_delay(attempt)
                log.warning("websocket disconnected error=%s reconnect_in=%.1f", exc, delay)
                await asyncio.to_thread(
                    _record, session_factory, "disconnected", {"error": str(exc), "reconnect_seconds": round(delay, 3)}
                )
                await _wait(delay, stop)
    finally:
        status.ws_connected = False


async def _consume(settings: Settings, session_factory, stop: asyncio.Event, status: RuntimeStatus) -> None:
    symbol = await asyncio.to_thread(_load_symbol, settings, session_factory)
    await asyncio.to_thread(_run_recovery, settings, session_factory, status)
    parser = StreamParser(symbol)
    subscriptions = SubscriptionBook()
    options = public_socket_options()
    async with websockets.connect(
        settings.delta_ws_url,
        open_timeout=settings.http_timeout_seconds,
        ping_interval=options["ping_interval"],
        ping_timeout=options["ping_timeout"],
        additional_headers={"User-Agent": settings.user_agent},
        max_size=2_000_000,
    ) as socket:
        status.ws_connected = True
        status.last_error = None
        status.last_heartbeat_at = utcnow()
        await socket.send(json.dumps(enable_heartbeat_message()))
        subscribe = subscriptions.message_for(symbol)
        if subscribe is None:
            raise RuntimeError("subscription already sent on a new connection")
        await socket.send(json.dumps(subscribe))
        status.subscriptions = [item["name"] for item in subscribe["payload"]["channels"]]
        await asyncio.to_thread(
            _record, session_factory, "connected", {"url": settings.delta_ws_url, "channels": status.subscriptions}
        )
        log.info("websocket connected url=%s symbol=%s", settings.delta_ws_url, symbol)
        sweep = asyncio.create_task(_sweep_gaps(settings, session_factory, stop, status))
        try:
            await _read_socket(settings, session_factory, socket, stop, status, symbol, parser, subscriptions, LocalOrderBook())
        finally:
            sweep.cancel()
            try:
                await sweep
            except asyncio.CancelledError:
                pass


async def _read_socket(settings, session_factory, socket, stop, status, symbol, parser, subscriptions, book) -> None:
    while not stop.is_set():
        timeout = seconds_until_heartbeat_deadline(
            status.last_heartbeat_at or utcnow(), utcnow(), settings.ws_heartbeat_timeout_seconds
        )
        if timeout == 0:
            raise ConnectionError("no heartbeat for %.0f seconds" % settings.ws_heartbeat_timeout_seconds)
        raw = await _receive(socket, timeout, stop)
        if raw is None:
            break
        received = utcnow()
        status.last_message_at = received
        event = parser.parse(raw, received)
        if event.kind == "heartbeat":
            status.last_heartbeat_at = received
        elif event.kind == "system_status":
            status.system_status = event.detail.get("status")
            await asyncio.to_thread(_record, session_factory, "system_status", event.detail)
        elif event.kind == "malformed":
            await asyncio.to_thread(_quality, session_factory, "malformed", event.detail)
        elif event.kind == "candle":
            await asyncio.to_thread(
                _store_candles, settings, session_factory, symbol, event.candles, event.sequence, status
            )
        elif event.kind == "funding_rate":
            await asyncio.to_thread(_store_funding, session_factory, event.detail)
        elif event.kind == "ob_updates":
            outcome = book.apply(event.detail)
            if outcome in ("sequence_gap", "error", "checksum_mismatch"):
                await socket.send(json.dumps(subscriptions.resubscribe("ob_updates", (symbol,))))
                await asyncio.to_thread(_derivatives_quality, session_factory, outcome, {"sequence": book.sequence})
        due = parser.close_due(utcnow())
        if due is not None:
            await asyncio.to_thread(_store_candles, settings, session_factory, symbol, [due], "closed", status)


async def _receive(socket, timeout: float, stop: asyncio.Event) -> str | None:
    recv_task = asyncio.create_task(socket.recv())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {recv_task, stop_task}, timeout=max(0.0, timeout), return_when=asyncio.FIRST_COMPLETED
        )
        if stop.is_set() and recv_task not in done:
            return None
        if recv_task not in done:
            raise ConnectionError("no heartbeat before the deadline")
        return recv_task.result()
    finally:
        for task in (recv_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(recv_task, stop_task, return_exceptions=True)


def _store_funding(session_factory, message: dict) -> None:
    from app.services.derivatives_service import store_funding_message

    session = session_factory()
    try:
        store_funding_message(session, message)
    except Exception:
        log.exception("funding observation was not stored")
        session.rollback()
    finally:
        session.close()


def _derivatives_quality(session_factory, kind: str, detail: dict) -> None:
    from app.services.derivatives_service import record_quality

    session = session_factory()
    try:
        record_quality(session, kind, detail)
    except Exception:
        log.exception("derivatives quality event was not stored")
        session.rollback()
    finally:
        session.close()


def _load_symbol(settings: Settings, session_factory) -> str:
    session = session_factory()
    try:
        with DeltaRestClient(
            settings.delta_rest_base,
            settings.user_agent,
            timeout=settings.http_timeout_seconds,
            min_interval=settings.request_min_interval_seconds,
            max_retries=settings.max_retries,
        ) as client:
            product = BackfillService(client, settings).sync_product(session)
            return product.symbol
    finally:
        session.close()


def _store_candles(
    settings: Settings,
    session_factory,
    symbol: str,
    candles: list[CandleDraft],
    sequence: str | None,
    status: RuntimeStatus | None = None,
) -> None:
    if not candles:
        return
    session = session_factory()
    try:
        repo = MarketRepository(session)
        product = repo.get_product()
        if product is None or product.symbol != symbol:
            return
        repo.upsert_candles(product.id, candles)
        closed = [item for item in candles if item.is_closed]
        if closed:
            repo.update_state(
                product.id,
                "1m",
                "websocket",
                oldest=closed[0].open_time,
                newest=closed[-1].open_time,
                status="ok",
                detail={"sequence": sequence},
            )
        if sequence == "gap" and len(candles) >= 2:
            missing = missing_opens([item.open_time for item in candles], RESOLUTIONS["1m"])
            if missing:
                with DeltaRestClient(
                    settings.delta_rest_base,
                    settings.user_agent,
                    timeout=settings.http_timeout_seconds,
                    min_interval=settings.request_min_interval_seconds,
                    max_retries=settings.max_retries,
                ) as client:
                    outcome = BackfillService(client, settings).fill_and_verify(session, product, missing, utcnow())
                _note_recovery(status, outcome)
        if sequence == "non_monotonic":
            repo.add_quality(
                "non_monotonic",
                {"open_time": candles[-1].open_time.isoformat()},
                product_id=product.id,
                timeframe="1m",
            )
        session.commit()
    finally:
        session.close()


def _run_recovery(settings: Settings, session_factory, status: RuntimeStatus) -> None:
    session = session_factory()
    try:
        product = MarketRepository(session).get_product()
        if product is None:
            return
        with DeltaRestClient(
            settings.delta_rest_base,
            settings.user_agent,
            timeout=settings.http_timeout_seconds,
            min_interval=settings.request_min_interval_seconds,
            max_retries=settings.max_retries,
        ) as client:
            outcome = BackfillService(client, settings).recover_until_current(session, product, utcnow)
        _note_recovery(status, outcome)
    except Exception as exc:
        status.recovery_failed += 1
        status.last_recovery_at = utcnow()
        status.last_recovery_status = "failed"
        status.last_recovery_error = str(exc)[:200]
        log.exception("gap recovery failed")
    finally:
        session.close()


def _note_recovery(status: RuntimeStatus | None, outcome: dict) -> None:
    if status is None:
        return
    status.last_recovery_at = utcnow()
    status.last_recovery_status = outcome.get("status")
    passes = int(outcome.get("passes_succeeded") or 0)
    if outcome.get("status") == "succeeded":
        passes = max(passes, 1)
    if passes:
        status.recovery_succeeded += passes
        status.last_recovery_error = None
    if outcome.get("status") == "failed":
        status.recovery_failed += 1
        status.last_recovery_error = outcome.get("reason") or outcome.get("error")


async def _sweep_gaps(settings: Settings, session_factory, stop: asyncio.Event, status: RuntimeStatus) -> None:
    """Check the recent closed series once a minute without blocking the socket read."""
    while not stop.is_set():
        await _wait(60, stop)
        if stop.is_set():
            return
        await asyncio.to_thread(_run_recovery, settings, session_factory, status)


def _record(session_factory, event_type: str, detail: dict) -> None:
    session = session_factory()
    try:
        MarketRepository(session).add_ws_event(event_type, detail)
        session.commit()
    except Exception:
        log.exception("could not record websocket event %s", event_type)
        session.rollback()
    finally:
        session.close()


def _quality(session_factory, kind: str, detail: dict) -> None:
    session = session_factory()
    try:
        MarketRepository(session).add_quality(kind, detail, timeframe="1m")
        session.commit()
    except Exception:
        log.exception("could not record quality event")
        session.rollback()
    finally:
        session.close()


async def _wait(seconds: float, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(1.0, seconds))
    except asyncio.TimeoutError:
        return
