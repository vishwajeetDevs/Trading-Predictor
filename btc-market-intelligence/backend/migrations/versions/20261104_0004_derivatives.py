"""Derivatives observations. Existing candle and study rows stay in place.

Revision ID: 0004_derivatives
Revises: 0003_sampling
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_derivatives"
down_revision = "0003_sampling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "funding_rate_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Numeric(38, 12), nullable=False),
        sa.Column("interval_seconds", sa.Integer()),
        sa.Column("next_funding_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "observed_at", "source", name="uq_funding_observation"),
    )
    op.create_index("ix_funding_rate_observations_product_id", "funding_rate_observations", ["product_id"])
    op.create_table(
        "open_interest_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contracts", sa.Numeric(38, 12)),
        sa.Column("value", sa.Numeric(38, 12)),
        sa.Column("value_symbol", sa.String(32)),
        sa.Column("value_usd", sa.Numeric(38, 12)),
        sa.Column("exchange_change_6h", sa.Numeric(38, 12)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "observed_at", "source", name="uq_open_interest_observation"),
    )
    op.create_index("ix_open_interest_observations_product_id", "open_interest_observations", ["product_id"])
    op.create_table(
        "mark_price_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mark_price", sa.Numeric(38, 12)),
        sa.Column("last_price", sa.Numeric(38, 12)),
        sa.Column("index_price", sa.Numeric(38, 12)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "observed_at", "source", name="uq_mark_price_observation"),
    )
    op.create_index("ix_mark_price_observations_product_id", "mark_price_observations", ["product_id"])
    op.create_table(
        "orderbook_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_bid", sa.Numeric(38, 12)),
        sa.Column("best_ask", sa.Numeric(38, 12)),
        sa.Column("spread", sa.Numeric(38, 12)),
        sa.Column("bid_depth", sa.Numeric(38, 12)),
        sa.Column("ask_depth", sa.Numeric(38, 12)),
        sa.Column("imbalance", sa.Numeric(38, 12)),
        sa.Column("levels", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "snapshot_at", "source", name="uq_orderbook_snapshot"),
    )
    op.create_index("ix_orderbook_snapshots_product_id", "orderbook_snapshots", ["product_id"])
    op.create_table(
        "derivatives_data_quality_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_derivatives_data_quality_events_occurred_at", "derivatives_data_quality_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("derivatives_data_quality_events")
    op.drop_table("orderbook_snapshots")
    op.drop_table("mark_price_observations")
    op.drop_table("open_interest_observations")
    op.drop_table("funding_rate_observations")
