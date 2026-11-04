from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_product_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    contract_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_status: Mapped[str | None] = mapped_column(String(64))
    underlying_asset: Mapped[str | None] = mapped_column(String(32))
    quoting_asset: Mapped[str | None] = mapped_column(String(32))
    settling_asset: Mapped[str | None] = mapped_column(String(32))
    tick_size: Mapped[str | None] = mapped_column(String(64))
    contract_value: Mapped[str | None] = mapped_column(String(64))
    contract_unit_currency: Mapped[str | None] = mapped_column(String(32))
    notional_type: Mapped[str | None] = mapped_column(String(32))
    spot_index_symbol: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict] = mapped_column(JSON, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("product_id", "timeframe", "open_time", name="uq_candle_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    high: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    low: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    close: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    volume: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exchange_time_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebSocketEvent(Base):
    __tablename__ = "websocket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)


class IngestionState(Base):
    __tablename__ = "ingestion_state"
    __table_args__ = (UniqueConstraint("product_id", "timeframe", "stream", name="uq_ingestion_stream"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    stream: Mapped[str] = mapped_column(String(32), nullable=False)
    oldest_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)


class StudyRun(Base):
    __tablename__ = "study_runs"
    __table_args__ = (UniqueConstraint("product_id", "definition_hash", "watermark_open_time", name="uq_study_watermark"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)


class ConditionMatch(Base):
    __tablename__ = "condition_matches"
    __table_args__ = (
        UniqueConstraint("product_id", "definition_hash", "timeframe", "condition", "open_time", name="uq_condition_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    condition: Mapped[str] = mapped_column(String(64), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[int] = mapped_column(Integer, nullable=False)
    close_price: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)


class HorizonStat(Base):
    __tablename__ = "horizon_stats"
    __table_args__ = (
        UniqueConstraint("product_id", "definition_hash", "timeframe", "condition", "horizon", name="uq_horizon_stat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    condition: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_match_count: Mapped[int | None] = mapped_column(Integer)
    independent_count: Mapped[int | None] = mapped_column(Integer)
    clustered_out: Mapped[int | None] = mapped_column(Integer)
    comparison_status: Mapped[str | None] = mapped_column(String(32))
    excluded: Mapped[int] = mapped_column(Integer, nullable=False)
    first_match_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_match_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    median_return: Mapped[float | None] = mapped_column(Numeric(38, 12))
    p10_return: Mapped[float | None] = mapped_column(Numeric(38, 12))
    p90_return: Mapped[float | None] = mapped_column(Numeric(38, 12))
    positive_rate: Mapped[float | None] = mapped_column(Numeric(18, 8))
    negative_rate: Mapped[float | None] = mapped_column(Numeric(18, 8))
    median_mfe: Mapped[float | None] = mapped_column(Numeric(38, 12))
    median_mae: Mapped[float | None] = mapped_column(Numeric(38, 12))
    base_positive_rate: Mapped[float | None] = mapped_column(Numeric(18, 8))
    base_sample_size: Mapped[int | None] = mapped_column(Integer)
    rate_difference: Mapped[float | None] = mapped_column(Numeric(18, 8))
    explanation: Mapped[str | None] = mapped_column(Text)
    bootstrap: Mapped[dict | None] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class FundingRateObservation(Base):
    __tablename__ = "funding_rate_observations"
    __table_args__ = (UniqueConstraint("product_id", "observed_at", "source", name="uq_funding_observation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    funding_rate: Mapped[object] = mapped_column(Numeric(38, 12), nullable=False)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    next_funding_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpenInterestObservation(Base):
    __tablename__ = "open_interest_observations"
    __table_args__ = (UniqueConstraint("product_id", "observed_at", "source", name="uq_open_interest_observation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contracts: Mapped[object | None] = mapped_column(Numeric(38, 12))
    value: Mapped[object | None] = mapped_column(Numeric(38, 12))
    value_symbol: Mapped[str | None] = mapped_column(String(32))
    value_usd: Mapped[object | None] = mapped_column(Numeric(38, 12))
    exchange_change_6h: Mapped[object | None] = mapped_column(Numeric(38, 12))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarkPriceObservation(Base):
    __tablename__ = "mark_price_observations"
    __table_args__ = (UniqueConstraint("product_id", "observed_at", "source", name="uq_mark_price_observation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mark_price: Mapped[object | None] = mapped_column(Numeric(38, 12))
    last_price: Mapped[object | None] = mapped_column(Numeric(38, 12))
    index_price: Mapped[object | None] = mapped_column(Numeric(38, 12))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    __table_args__ = (UniqueConstraint("product_id", "snapshot_at", "source", name="uq_orderbook_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    best_bid: Mapped[object | None] = mapped_column(Numeric(38, 12))
    best_ask: Mapped[object | None] = mapped_column(Numeric(38, 12))
    spread: Mapped[object | None] = mapped_column(Numeric(38, 12))
    bid_depth: Mapped[object | None] = mapped_column(Numeric(38, 12))
    ask_depth: Mapped[object | None] = mapped_column(Numeric(38, 12))
    imbalance: Mapped[object | None] = mapped_column(Numeric(38, 12))
    levels: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DerivativesQualityEvent(Base):
    __tablename__ = "derivatives_data_quality_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)


class DataQualityEvent(Base):
    __tablename__ = "data_quality_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    timeframe: Mapped[str | None] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)
