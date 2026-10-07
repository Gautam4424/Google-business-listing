"""competitors and gaps (Phase 8)

Revision ID: a3c1f0e2b7d4
Revises: ee79cd8562c2
Create Date: 2026-10-07 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c1f0e2b7d4"
down_revision: str | None = "ee79cd8562c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("businesses", sa.Column("cid", sa.String(length=40), nullable=True))
    op.create_index(op.f("ix_businesses_cid"), "businesses", ["cid"], unique=False)
    op.add_column("ranking_results", sa.Column("categories", JSON, nullable=True))
    op.add_column("competitor_metrics", sa.Column("audit_job_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_competitor_metrics_audit_job_id"), "competitor_metrics", ["audit_job_id"], unique=False
    )
    op.create_foreign_key(
        "fk_competitor_metrics_audit_job_id",
        "competitor_metrics",
        "audit_jobs",
        ["audit_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("competitor_metrics", sa.Column("primary_category", sa.String(length=200), nullable=True))
    op.add_column("competitor_metrics", sa.Column("keywords", JSON, nullable=True))
    op.add_column("competitor_metrics", sa.Column("review_topics", JSON, nullable=True))
    op.add_column("competitor_metrics", sa.Column("review_sample_size", sa.Integer(), nullable=True))
    op.add_column("competitor_metrics", sa.Column("profile_source", sa.String(length=40), nullable=True))
    op.add_column("gap_recommendations", sa.Column("title", sa.String(length=300), nullable=True))
    op.add_column(
        "gap_recommendations",
        sa.Column("priority", sa.String(length=10), nullable=False, server_default="medium"),
    )


def downgrade() -> None:
    op.drop_column("gap_recommendations", "priority")
    op.drop_column("gap_recommendations", "title")
    op.drop_column("competitor_metrics", "profile_source")
    op.drop_column("competitor_metrics", "review_sample_size")
    op.drop_column("competitor_metrics", "review_topics")
    op.drop_column("competitor_metrics", "keywords")
    op.drop_column("competitor_metrics", "primary_category")
    op.drop_constraint("fk_competitor_metrics_audit_job_id", "competitor_metrics", type_="foreignkey")
    op.drop_index(op.f("ix_competitor_metrics_audit_job_id"), table_name="competitor_metrics")
    op.drop_column("competitor_metrics", "audit_job_id")
    op.drop_column("ranking_results", "categories")
    op.drop_index(op.f("ix_businesses_cid"), table_name="businesses")
    op.drop_column("businesses", "cid")
