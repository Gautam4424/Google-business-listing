"""ranking checks: choose where to search from (city / country / business)

Revision ID: c4d8e1f2a6b5
Revises: b7e2c4d9a1f3
Create Date: 2026-10-09 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e1f2a6b5"
down_revision: str | None = "b7e2c4d9a1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("search_from", sa.String(length=10), nullable=False, server_default="city")
    )
    op.add_column("ranking_runs", sa.Column("search_scope", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("ranking_runs", "search_scope")
    op.drop_column("projects", "search_from")
