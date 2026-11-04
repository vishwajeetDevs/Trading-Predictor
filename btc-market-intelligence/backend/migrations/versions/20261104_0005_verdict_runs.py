"""Verdict engine runs. Existing tables stay in place.

Revision ID: 0005_verdict_runs
Revises: 0004_derivatives
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_verdict_runs"
down_revision = "0004_derivatives"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verdict_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("focus_timeframe", sa.String(8), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("regime", sa.String(32)),
        sa.Column("close", sa.Numeric(38, 12)),
        sa.Column("p_bull", sa.Numeric(18, 8)),
        sa.Column("p_bear", sa.Numeric(18, 8)),
        sa.Column("p_range", sa.Numeric(18, 8)),
        sa.Column("confidence", sa.Numeric(18, 8)),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.JSON()),
    )
    op.create_index("ix_verdict_runs_product_id", "verdict_runs", ["product_id"])
    op.create_index("ix_verdict_runs_computed_at", "verdict_runs", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_verdict_runs_computed_at", table_name="verdict_runs")
    op.drop_index("ix_verdict_runs_product_id", table_name="verdict_runs")
    op.drop_table("verdict_runs")
