"""ranking runs: the exact Google URL SerpApi opened + SerpApi's saved copy (backfilled from raw responses)

Revision ID: d5e9f3a7b2c1
Revises: c4d8e1f2a6b5
Create Date: 2026-10-09 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9f3a7b2c1"
down_revision: str | None = "c4d8e1f2a6b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ranking_runs", sa.Column("google_url", sa.Text(), nullable=True))
    op.add_column("ranking_runs", sa.Column("snapshot_url", sa.Text(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":  # fill in checks made before (raw responses kept 30 days)
        op.execute(
            """
            UPDATE ranking_runs r SET
              google_url = COALESCE(ds.response->'search_metadata'->>'google_url',
                                    ds.response->'search_metadata'->>'google_maps_url',
                                    ds.response->'search_metadata'->>'google_local_url'),
              snapshot_url = ds.response->'search_metadata'->>'raw_html_file'
            FROM data_sources ds
            WHERE ds.id = r.raw_response_id
            """
        )


def downgrade() -> None:
    op.drop_column("ranking_runs", "snapshot_url")
    op.drop_column("ranking_runs", "google_url")
