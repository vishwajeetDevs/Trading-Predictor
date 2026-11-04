"""Analysis tables. Existing market-data tables are left in place.

Revision ID: 0002_analysis
Revises: 0001_initial
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_analysis"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("watermark_open_time", sa.DateTime(timezone=True)),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint("product_id", "definition_hash", "watermark_open_time", name="uq_study_watermark"),
    )
    op.create_index("ix_study_runs_product_id", "study_runs", ["product_id"])
    op.create_table(
        "condition_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("condition", sa.String(length=64), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.Integer(), nullable=False),
        sa.Column("close_price", sa.Numeric(38, 12), nullable=False),
        sa.UniqueConstraint(
            "product_id", "definition_hash", "timeframe", "condition", "open_time", name="uq_condition_match"
        ),
    )
    op.create_index("ix_condition_matches_product_id", "condition_matches", ["product_id"])
    op.create_index("ix_condition_matches_lookup", "condition_matches", ["product_id", "definition_hash", "condition"])
    op.create_table(
        "horizon_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("condition", sa.String(length=64), nullable=False),
        sa.Column("horizon", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("excluded", sa.Integer(), nullable=False),
        sa.Column("first_match_at", sa.DateTime(timezone=True)),
        sa.Column("last_match_at", sa.DateTime(timezone=True)),
        sa.Column("median_return", sa.Numeric(38, 12)),
        sa.Column("p10_return", sa.Numeric(38, 12)),
        sa.Column("p90_return", sa.Numeric(38, 12)),
        sa.Column("positive_rate", sa.Numeric(18, 8)),
        sa.Column("negative_rate", sa.Numeric(18, 8)),
        sa.Column("median_mfe", sa.Numeric(38, 12)),
        sa.Column("median_mae", sa.Numeric(38, 12)),
        sa.Column("base_positive_rate", sa.Numeric(18, 8)),
        sa.Column("base_sample_size", sa.Integer()),
        sa.Column("rate_difference", sa.Numeric(18, 8)),
        sa.Column("explanation", sa.Text()),
        sa.Column("bootstrap", sa.JSON()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "product_id", "definition_hash", "timeframe", "condition", "horizon", name="uq_horizon_stat"
        ),
    )
    op.create_index("ix_horizon_stats_product_id", "horizon_stats", ["product_id"])
    op.create_table(
        "analysis_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("watermark_open_time", sa.DateTime(timezone=True)),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_analysis_snapshots_product_id", "analysis_snapshots", ["product_id"])


def downgrade() -> None:
    op.drop_table("analysis_snapshots")
    op.drop_table("horizon_stats")
    op.drop_table("condition_matches")
    op.drop_table("study_runs")
