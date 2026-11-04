"""Historical backfill. Windows stay under Delta's 2,000-candle cap and resume from the last stored open."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.timeutil import RESOLUTIONS, as_utc, floor_epoch, from_epoch_seconds, to_decimal, utcnow
from app.data.candle_builder import aggregate, api_end_inclusive, iter_chunks, missing_opens
from app.data.delta_rest import DeltaRestClient
from app.data.normalizer import CandleDraft, parse_rest_candles
from app.data.product_resolver import select_btc_perpetual
from app.data.reconciliation import differences
from app.database.models import Product
from app.database.repositories import MarketRepository

log = logging.getLogger(__name__)

HIGHER_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


class BackfillService:
    def __init__(self, client: DeltaRestClient, settings: Settings):
        self.client = client
        self.settings = settings

    def sync_product(self, session: Session) -> Product:
        chosen = select_btc_perpetual(self.client.iter_products())
        repo = MarketRepository(session)
        product = repo.upsert_product(chosen)
        session.commit()
        log.info(
            "resolved product symbol=%s exchange_id=%s tick_size=%s",
            product.symbol,
            product.exchange_product_id,
            product.tick_size,
        )
        return product

    def backfill(self, session: Session, *, days: int | None = None, as_of: datetime | None = None) -> dict:
        moment = as_utc(as_of or utcnow())
        product = self.sync_product(session)
        lookback_days = self.settings.lookback_days if days is None else days
        end = floor_epoch(moment, 60)
        start = end - lookback_days * 86400
        summary = {"symbol": product.symbol, "start": start, "end": end, "timeframes": {}}
        summary["timeframes"]["1m"] = self.fill_range(session, product, "1m", start, end, moment)
        for timeframe in HIGHER_TIMEFRAMES:
            native = self.fill_range(session, product, timeframe, start, end, moment)
            compared = self._compare_aggregate(session, product, timeframe, start, end)
            native["aggregate"] = compared
            summary["timeframes"][timeframe] = native
        session.commit()
        return summary

    def fill_range(
        self,
        session: Session,
        product: Product,
        timeframe: str,
        start: int,
        end: int,
        as_of: datetime,
        *,
        resume: bool = True,
    ) -> dict:
        repo = MarketRepository(session)
        step = RESOLUTIONS[timeframe]
        start = floor_epoch(from_epoch_seconds(start), step)
        end = end if end % step == 0 else floor_epoch(from_epoch_seconds(end), step)
        state = repo.get_state(product.id, timeframe, "backfill") if resume else None
        ranges: list[tuple[int, int]] = []
        if state is not None and state.oldest_open_time is not None and int(as_utc(state.oldest_open_time).timestamp()) > start:
            ranges.append((start, int(as_utc(state.oldest_open_time).timestamp())))
        resume_from = start
        if state is not None and state.newest_open_time is not None:
            resume_from = max(start, int(as_utc(state.newest_open_time).timestamp()) + step)
        if resume_from < end:
            ranges.append((resume_from, end))
        stored = 0
        requested = 0
        for window_start, window_end in ranges:
            for chunk_start, chunk_end in iter_chunks(window_start, window_end, step, self.settings.candle_chunk_size):
                drafts, notes = self._fetch_chunk(product.symbol, timeframe, chunk_start, chunk_end, as_of, repo, product.id)
                requested += 1
                outcome = repo.upsert_candles(product.id, drafts)
                stored += outcome.inserted + outcome.updated
                self._record_quality(repo, product.id, timeframe, drafts, notes, outcome.conflicts)
                if drafts:
                    repo.update_state(
                        product.id,
                        timeframe,
                        "backfill",
                        oldest=drafts[0].open_time,
                        newest=drafts[-1].open_time,
                        status="ok",
                        detail={"chunk_start": chunk_start, "chunk_end": chunk_end, "rows": len(drafts)},
                    )
                session.commit()
                log.info(
                    "backfill timeframe=%s chunk=%s..%s rows=%s inserted=%s",
                    timeframe,
                    chunk_start,
                    chunk_end,
                    len(drafts),
                    outcome.inserted,
                )
        return {"requests": requested, "stored": stored}

    def recover_recent_gaps(self, session: Session, product: Product, as_of: datetime, *, window_minutes: int = 1440) -> dict:
        """Fill closed 1-minute holes in the recent window, then check that they are present."""
        repo = MarketRepository(session)
        step = RESOLUTIONS["1m"]
        closed_before = floor_epoch(as_of, step)
        latest = repo.latest_closed(product.id, "1m")
        if latest is None:
            return {"status": "skipped", "reason": "no closed candle is stored", "still_missing": 0}
        newest = int(as_utc(latest.open_time).timestamp())
        window_start = closed_before - window_minutes * step
        opens = [item for item in repo.recent_closed_opens(product.id, "1m", limit=window_minutes) if int(item.timestamp()) >= window_start]
        missing = [item for item in missing_opens(opens, step) if int(item.timestamp()) < closed_before]
        if closed_before > newest + step:
            tail_start = max(newest, window_start)
            missing.extend(missing_opens([from_epoch_seconds(tail_start), from_epoch_seconds(closed_before)], step))
        missing = sorted({as_utc(item) for item in missing})
        return self.fill_and_verify(session, product, missing, as_of)

    def recover_until_current(
        self,
        session: Session,
        product: Product,
        clock,
        *,
        attempts: int = 3,
        window_minutes: int = 1440,
    ) -> dict:
        """Repeat recovery while a pass succeeds.

        A pass can take long enough for more minutes to close. Those minutes are
        not in the snapshot that started the pass, so the next call reads the
        clock again. A failure or an empty gap stops the loop.
        """
        outcome = {"status": "not_needed", "still_missing": 0, "filled": 0}
        succeeded = 0
        for _ in range(max(1, attempts)):
            outcome = self.recover_recent_gaps(session, product, clock(), window_minutes=window_minutes)
            if outcome.get("status") != "succeeded":
                break
            succeeded += 1
        reported = dict(outcome)
        reported["passes_succeeded"] = succeeded
        return reported

    def fill_and_verify(self, session: Session, product: Product, missing: list[datetime], as_of: datetime) -> dict:
        repo = MarketRepository(session)
        if not missing:
            return {"status": "not_needed", "still_missing": 0, "filled": 0}
        ordered = sorted(as_utc(item) for item in missing)
        start = int(ordered[0].timestamp())
        end = int(ordered[-1].timestamp()) + RESOLUTIONS["1m"]
        repo.add_quality(
            "missing_candle",
            {
                "count": len(ordered),
                "first": ordered[0].isoformat(),
                "last": ordered[-1].isoformat(),
                "source": "recovery",
            },
            product_id=product.id,
            timeframe="1m",
        )
        session.commit()
        try:
            stored = self.fill_range(session, product, "1m", start, end, as_of, resume=False)
        except Exception as exc:
            detail = {
                "status": "failed",
                "reason": "REST recovery raised before the missing minutes could be checked.",
                "error": str(exc)[:200],
                "still_missing": len(ordered),
                "first": ordered[0].isoformat(),
                "last": ordered[-1].isoformat(),
            }
            repo.add_quality("recovery_failed", detail, product_id=product.id, timeframe="1m")
            session.commit()
            return detail
        span = max(1, (end - start) // RESOLUTIONS["1m"])
        rows = repo.candles(product.id, "1m", from_epoch_seconds(start), from_epoch_seconds(end), limit=span + 5)
        present = {int(as_utc(row.open_time).timestamp()) for row in rows if row.is_closed}
        still = [item for item in ordered if int(item.timestamp()) not in present]
        if still:
            detail = {
                "status": "failed",
                "reason": "REST did not return every missing closed minute. Those minutes were not invented.",
                "still_missing": len(still),
                "unavailable": [item.isoformat() for item in still[:50]],
                "requested": len(ordered),
                "stored": stored["stored"],
                "first": still[0].isoformat(),
                "last": still[-1].isoformat(),
            }
            kind = "recovery_failed"
        else:
            detail = {
                "status": "succeeded",
                "reason": "The missing closed minutes are present after the REST upsert.",
                "filled": len(ordered),
                "stored": stored["stored"],
                "first": ordered[0].isoformat(),
                "last": ordered[-1].isoformat(),
            }
            kind = "recovery_succeeded"
        repo.add_quality(kind, detail, product_id=product.id, timeframe="1m")
        session.commit()
        return detail

    def _fetch_chunk(self, symbol, timeframe, start, end, as_of, repo: MarketRepository, product_id: int):
        payload = self.client.fetch_candles(symbol, timeframe, start, api_end_inclusive(end))
        rows = payload.get("result") or []
        drafts, errors = parse_rest_candles(payload, symbol=symbol, timeframe=timeframe, source="delta_rest", as_of=as_of)
        if len(rows) >= self.settings.max_candle_page and drafts:
            oldest = int(drafts[0].open_time.timestamp())
            if oldest > start:
                repo.add_quality(
                    "truncated_page",
                    {"start": start, "oldest_returned": oldest, "rows": len(rows)},
                    product_id=product_id,
                    timeframe=timeframe,
                )
                older, older_errors = self._fetch_chunk(symbol, timeframe, start, oldest, as_of, repo, product_id)
                drafts = older + [item for item in drafts if int(item.open_time.timestamp()) >= oldest]
                errors = older_errors + errors
        return drafts, errors

    def _record_quality(self, repo, product_id, timeframe, drafts, errors, conflicts) -> None:
        for error in errors:
            repo.add_quality("malformed", {"error": error}, product_id=product_id, timeframe=timeframe)
        for conflict in conflicts:
            repo.add_quality("reconciliation_mismatch", conflict, product_id=product_id, timeframe=timeframe)
        missing = missing_opens([item.open_time for item in drafts], RESOLUTIONS[timeframe])
        if missing:
            repo.add_quality(
                "missing_candle",
                {"count": len(missing), "first": missing[0].isoformat(), "last": missing[-1].isoformat()},
                product_id=product_id,
                timeframe=timeframe,
            )
        for draft in drafts:
            if not draft.aligned:
                repo.add_quality(
                    "timestamp_unaligned",
                    {"open_time": draft.open_time.isoformat()},
                    product_id=product_id,
                    timeframe=timeframe,
                )

    def _compare_aggregate(self, session: Session, product: Product, timeframe: str, start: int, end: int) -> dict:
        repo = MarketRepository(session)
        one_minute = repo.candles(
            product.id,
            "1m",
            from_epoch_seconds(start),
            from_epoch_seconds(end),
            limit=10_000_000,
        )
        drafts = [
            CandleDraft(
                symbol=row.symbol,
                timeframe="1m",
                open_time=as_utc(row.open_time),
                open=to_decimal(row.open),
                high=to_decimal(row.high),
                low=to_decimal(row.low),
                close=to_decimal(row.close),
                volume=to_decimal(row.volume),
                source=row.source,
                is_closed=row.is_closed,
                exchange_time_raw=row.exchange_time_raw,
                timestamp_unit=row.timestamp_unit,
                received_at=as_utc(row.received_at),
                aligned=True,
            )
            for row in one_minute
        ]
        built, skipped = aggregate(drafts, timeframe)
        for item in skipped:
            repo.add_quality("incomplete_aggregate", item, product_id=product.id, timeframe=timeframe)
        native_rows = repo.candles(product.id, timeframe, from_epoch_seconds(start), from_epoch_seconds(end), limit=10_000_000)
        native = {as_utc(row.open_time): row for row in native_rows}
        mismatched = 0
        inserted = 0
        for candle in built:
            current = native.get(as_utc(candle.open_time))
            if current is None:
                outcome = repo.upsert_candles(product.id, [candle])
                inserted += outcome.inserted
                continue
            native_draft = CandleDraft(
                symbol=current.symbol,
                timeframe=timeframe,
                open_time=as_utc(current.open_time),
                open=to_decimal(current.open),
                high=to_decimal(current.high),
                low=to_decimal(current.low),
                close=to_decimal(current.close),
                volume=to_decimal(current.volume),
                source=current.source,
                is_closed=current.is_closed,
                exchange_time_raw=current.exchange_time_raw,
                timestamp_unit=current.timestamp_unit,
                received_at=as_utc(current.received_at),
                aligned=True,
            )
            delta = differences(native_draft, candle, tick_size=to_decimal(product.tick_size) if product.tick_size else None)
            if delta:
                mismatched += 1
                repo.add_quality(
                    "reconciliation_mismatch",
                    {"open_time": candle.open_time.isoformat(), "fields": delta},
                    product_id=product.id,
                    timeframe=timeframe,
                )
        session.commit()
        return {"aggregated": len(built), "skipped_buckets": len(skipped), "inserted": inserted, "mismatched": mismatched}
