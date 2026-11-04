"""Sampling counts on horizon statistics. Existing rows stay in place.

Revision ID: 0003_sampling
Revises: 0002_analysis
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_sampling"
down_revision = "0002_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("horizon_stats", sa.Column("raw_match_count", sa.Integer(), nullable=True))
    op.add_column("horizon_stats", sa.Column("independent_count", sa.Integer(), nullable=True))
    op.add_column("horizon_stats", sa.Column("clustered_out", sa.Integer(), nullable=True))
    op.add_column("horizon_stats", sa.Column("comparison_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("horizon_stats", "comparison_status")
    op.drop_column("horizon_stats", "clustered_out")
    op.drop_column("horizon_stats", "independent_count")
    op.drop_column("horizon_stats", "raw_match_count")
