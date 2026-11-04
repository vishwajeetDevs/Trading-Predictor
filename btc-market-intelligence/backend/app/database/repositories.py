"""Database reads and writes for products, candles, and ingestion bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc, to_decimal, utcnow
from app.data.normalizer import CandleDraft
from app.data.product_resolver import public_metadata
from app.database.models import Candle, DataQualityEvent, IngestionState, Product, WebSocketEvent


def _without_trading_settings(payload: dict) -> dict:
    hidden = ("leverage", "margin", "wallet", "position")
    return {key: value for key, value in payload.items() if not any(word in str(key).lower() for word in hidden)}


def json_safe(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: list[dict] = field(default_factory=list)


class MarketRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_product(self, payload: dict, resolved_at: datetime | None = None) -> Product:
        meta = public_metadata(payload)
        now = as_utc(resolved_at or utcnow())
        row = self.session.scalar(select(Product).where(Product.exchange_product_id == meta["exchange_product_id"]))
        values = dict(
            exchange_product_id=meta["exchange_product_id"],
            symbol=meta["symbol"],
            contract_type=meta["contract_type"],
            state=meta["state"],
            trading_status=meta["trading_status"],
            underlying_asset=meta["underlying_asset"],
            quoting_asset=meta["quoting_asset"],
            settling_asset=meta["settling_asset"],
            tick_size=None if meta["tick_size"] is None else str(meta["tick_size"]),
            contract_value=None if meta["contract_value"] is None else str(meta["contract_value"]),
            contract_unit_currency=meta["contract_unit_currency"],
            notional_type=meta["notional_type"],
            spot_index_symbol=meta["spot_index_symbol"],
            description=meta["description"],
            raw_metadata=json_safe(_without_trading_settings(payload)),
            updated_at=now,
        )
        if row is None:
            row = Product(resolved_at=now, **values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self.session.flush()
        return row

    def get_product(self) -> Product | None:
        return self.session.scalar(select(Product).order_by(Product.id))

    def upsert_candles(self, product_id: int, drafts: list[CandleDraft]) -> UpsertResult:
        result = UpsertResult()
        if not drafts:
            return result
        timeframe = drafts[0].timeframe
        existing = self.session.scalars(
            select(Candle).where(
                Candle.product_id == product_id,
                Candle.timeframe == timeframe,
                Candle.open_time.in_([draft.open_time for draft in drafts]),
            )
        ).all()
        by_open = {as_utc(row.open_time): row for row in existing}
        pending: dict = {}
        for draft in drafts:
            key = as_utc(draft.open_time)
            current = by_open.get(key)
            if current is None:
                self._queue_insert(pending, key, product_id, draft, result)
                continue
            if _same_bar(current, draft):
                if draft.is_closed and not current.is_closed:
                    current.is_closed = True
                    current.received_at = draft.received_at
                    result.updated += 1
                else:
                    result.unchanged += 1
                continue
            replaced = not (current.source in ("delta_rest", "delta_ws") and draft.source == "aggregated_1m")
            result.conflicts.append(
                {
                    "open_time": as_utc(draft.open_time).isoformat(),
                    "stored_source": current.source,
                    "incoming_source": draft.source,
                    "stored_close": str(to_decimal(current.close)),
                    "incoming_close": str(draft.close),
                    "replaced": replaced,
                    "reason": (
                        "The incoming candle differs from the stored candle. The stored exchange candle was kept because the incoming source is an aggregate."
                        if not replaced
                        else "The incoming candle differs from the stored candle and replaced it."
                    ),
                }
            )
            if not replaced:
                continue
            current.open = draft.open
            current.high = draft.high
            current.low = draft.low
            current.close = draft.close
            current.volume = draft.volume
            current.source = draft.source
            current.is_closed = current.is_closed or draft.is_closed
            current.exchange_time_raw = draft.exchange_time_raw
            current.timestamp_unit = draft.timestamp_unit
            current.received_at = draft.received_at
            result.updated += 1
        if pending:
            self.session.execute(insert(Candle), list(pending.values()))
        self.session.flush()
        return result

    def _queue_insert(self, pending: dict, key, product_id: int, draft: CandleDraft, result: UpsertResult) -> None:
        row = pending.get(key)
        if row is None:
            pending[key] = {
                "product_id": product_id,
                "symbol": draft.symbol,
                "timeframe": draft.timeframe,
                "open_time": draft.open_time,
                "open": draft.open,
                "high": draft.high,
                "low": draft.low,
                "close": draft.close,
                "volume": draft.volume,
                "source": draft.source,
                "is_closed": draft.is_closed,
                "exchange_time_raw": draft.exchange_time_raw,
                "timestamp_unit": draft.timestamp_unit,
                "received_at": draft.received_at,
            }
            result.inserted += 1
            return
        if _same_values(row, draft):
            if draft.is_closed and not row["is_closed"]:
                row["is_closed"] = True
                row["received_at"] = draft.received_at
                result.updated += 1
            else:
                result.unchanged += 1
            return
        result.conflicts.append(
            {
                "open_time": key.isoformat(),
                "stored_source": row["source"],
                "incoming_source": draft.source,
                "stored_close": str(to_decimal(row["close"])),
                "incoming_close": str(draft.close),
            }
        )
        if row["source"] in ("delta_rest", "delta_ws") and draft.source == "aggregated_1m":
            return
        row["open"] = draft.open
        row["high"] = draft.high
        row["low"] = draft.low
        row["close"] = draft.close
        row["volume"] = draft.volume
        row["source"] = draft.source
        row["is_closed"] = row["is_closed"] or draft.is_closed
        row["exchange_time_raw"] = draft.exchange_time_raw
        row["timestamp_unit"] = draft.timestamp_unit
        row["received_at"] = draft.received_at
        result.updated += 1

    def candles(
        self,
        product_id: int,
        timeframe: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        closed_only: bool = True,
        descending: bool = False,
    ) -> list[Candle]:
        query = select(Candle).where(Candle.product_id == product_id, Candle.timeframe == timeframe)
        if closed_only:
            query = query.where(Candle.is_closed.is_(True))
        if start is not None:
            query = query.where(Candle.open_time >= as_utc(start))
        if end is not None:
            query = query.where(Candle.open_time < as_utc(end))
        direction = Candle.open_time.desc() if descending else Candle.open_time.asc()
        query = query.order_by(direction).limit(limit)
        return list(self.session.scalars(query))

    def latest_closed(self, product_id: int, timeframe: str = "1m") -> Candle | None:
        return self.session.scalar(
            select(Candle)
            .where(Candle.product_id == product_id, Candle.timeframe == timeframe, Candle.is_closed.is_(True))
            .order_by(Candle.open_time.desc())
            .limit(1)
        )

    def recent_closed_opens(self, product_id: int, timeframe: str, limit: int = 300) -> list[datetime]:
        rows = self.session.scalars(
            select(Candle.open_time)
            .where(Candle.product_id == product_id, Candle.timeframe == timeframe, Candle.is_closed.is_(True))
            .order_by(Candle.open_time.desc())
            .limit(limit)
        ).all()
        return sorted(as_utc(item) for item in rows)

    def get_state(self, product_id: int, timeframe: str, stream: str) -> IngestionState | None:
        return self.session.scalar(
            select(IngestionState).where(
                IngestionState.product_id == product_id,
                IngestionState.timeframe == timeframe,
                IngestionState.stream == stream,
            )
        )

    def update_state(self, product_id: int, timeframe: str, stream: str, *, oldest: datetime | None, newest: datetime | None, status: str, detail: dict | None = None) -> IngestionState:
        row = self.get_state(product_id, timeframe, stream)
        now = utcnow()
        if row is None:
            row = IngestionState(
                product_id=product_id,
                timeframe=timeframe,
                stream=stream,
                oldest_open_time=oldest,
                newest_open_time=newest,
                last_success_at=now,
                status=status,
                detail=detail or {},
            )
            self.session.add(row)
        else:
            if oldest is not None and (row.oldest_open_time is None or as_utc(oldest) < as_utc(row.oldest_open_time)):
                row.oldest_open_time = oldest
            if newest is not None and (row.newest_open_time is None or as_utc(newest) > as_utc(row.newest_open_time)):
                row.newest_open_time = newest
            row.last_success_at = now
            row.status = status
            if detail is not None:
                row.detail = detail
        self.session.flush()
        return row

    def add_quality(self, kind: str, detail: dict, *, product_id: int | None = None, timeframe: str | None = None, occurred_at: datetime | None = None) -> None:
        self.session.add(
            DataQualityEvent(
                occurred_at=as_utc(occurred_at or utcnow()),
                product_id=product_id,
                timeframe=timeframe,
                kind=kind,
                detail=json_safe(detail),
            )
        )
        self.session.flush()

    def quality_counts(self, since: datetime) -> dict[str, int]:
        rows = self.session.scalars(select(DataQualityEvent).where(DataQualityEvent.occurred_at >= as_utc(since))).all()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.kind] = counts.get(row.kind, 0) + 1
        return counts

    def add_ws_event(self, event_type: str, detail: dict, occurred_at: datetime | None = None) -> None:
        now = as_utc(occurred_at or utcnow())
        self.session.add(WebSocketEvent(occurred_at=now, event_type=event_type, detail=json_safe(detail)))
        cutoff = now - timedelta(days=7)
        old = self.session.scalars(select(WebSocketEvent).where(WebSocketEvent.occurred_at < cutoff)).all()
        for row in old:
            self.session.delete(row)
        self.session.flush()

    def list_states(self) -> list[IngestionState]:
        return list(self.session.scalars(select(IngestionState).order_by(IngestionState.id)))

    def ping(self) -> bool:
        self.session.execute(select(1))
        return True

    def storage_summary(self) -> dict:
        bind = self.session.get_bind()
        names = set(inspect(bind).get_table_names())
        required = ("products", "candles", "ingestion_state", "websocket_events", "data_quality_events")
        revision = None
        if "alembic_version" in names:
            revision = self.session.execute(text("select version_num from alembic_version")).scalar()
        unique = {item.get("name") for item in inspect(bind).get_unique_constraints("candles")} if "candles" in names else set()
        by_timeframe = {}
        if "candles" in names:
            for timeframe, count in self.session.execute(select(Candle.timeframe, func.count()).group_by(Candle.timeframe)):
                by_timeframe[timeframe] = int(count)

        def _count(model) -> int:
            return int(self.session.scalar(select(func.count()).select_from(model)) or 0)

        latest_received = self.session.scalar(select(func.max(Candle.received_at))) if "candles" in names else None
        return {
            "alembic_revision": revision,
            "tables": {name: name in names for name in required},
            "constraints": {"uq_candle_slot": "uq_candle_slot" in unique},
            "counts": {
                "products": _count(Product) if "products" in names else 0,
                "candles": _count(Candle) if "candles" in names else 0,
                "ingestion_state": _count(IngestionState) if "ingestion_state" in names else 0,
                "websocket_events": _count(WebSocketEvent) if "websocket_events" in names else 0,
                "data_quality_events": _count(DataQualityEvent) if "data_quality_events" in names else 0,
                "by_timeframe": by_timeframe,
            },
            "latest_received_at": latest_received,
        }

    def recent_quality(self, limit: int = 15) -> list[DataQualityEvent]:
        return list(
            self.session.scalars(
                select(DataQualityEvent).order_by(DataQualityEvent.occurred_at.desc()).limit(limit)
            ).all()
        )


def _same_values(row: dict, draft: CandleDraft) -> bool:
    return (
        to_decimal(row["open"]) == draft.open
        and to_decimal(row["high"]) == draft.high
        and to_decimal(row["low"]) == draft.low
        and to_decimal(row["close"]) == draft.close
        and to_decimal(row["volume"]) == draft.volume
    )


def _same_bar(row: Candle, draft: CandleDraft) -> bool:
    return (
        to_decimal(row.open) == draft.open
        and to_decimal(row.high) == draft.high
        and to_decimal(row.low) == draft.low
        and to_decimal(row.close) == draft.close
        and to_decimal(row.volume) == draft.volume
    )
