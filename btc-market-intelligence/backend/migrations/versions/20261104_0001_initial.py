"""Initial market-data tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange_product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("contract_type", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("trading_status", sa.String(length=64)),
        sa.Column("underlying_asset", sa.String(length=32)),
        sa.Column("quoting_asset", sa.String(length=32)),
        sa.Column("settling_asset", sa.String(length=32)),
        sa.Column("tick_size", sa.String(length=64)),
        sa.Column("contract_value", sa.String(length=64)),
        sa.Column("contract_unit_currency", sa.String(length=32)),
        sa.Column("notional_type", sa.String(length=32)),
        sa.Column("spot_index_symbol", sa.String(length=64)),
        sa.Column("description", sa.Text()),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange_product_id"),
        sa.UniqueConstraint("symbol"),
    )
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(38, 12), nullable=False),
        sa.Column("high", sa.Numeric(38, 12), nullable=False),
        sa.Column("low", sa.Numeric(38, 12), nullable=False),
        sa.Column("close", sa.Numeric(38, 12), nullable=False),
        sa.Column("volume", sa.Numeric(38, 12), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("exchange_time_raw", sa.String(length=32), nullable=False),
        sa.Column("timestamp_unit", sa.String(length=8), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "timeframe", "open_time", name="uq_candle_slot"),
    )
    op.create_index("ix_candles_product_id", "candles", ["product_id"])
    op.create_table(
        "websocket_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_websocket_events_occurred_at", "websocket_events", ["occurred_at"])
    op.create_table(
        "ingestion_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("stream", sa.String(length=32), nullable=False),
        sa.Column("oldest_open_time", sa.DateTime(timezone=True)),
        sa.Column("newest_open_time", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint("product_id", "timeframe", "stream", name="uq_ingestion_stream"),
    )
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id")),
        sa.Column("timeframe", sa.String(length=8)),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_data_quality_events_occurred_at", "data_quality_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("data_quality_events")
    op.drop_table("ingestion_state")
    op.drop_table("websocket_events")
    op.drop_table("candles")
    op.drop_table("products")
