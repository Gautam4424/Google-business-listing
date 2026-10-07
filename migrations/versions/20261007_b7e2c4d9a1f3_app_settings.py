"""settings editable in the app (app_settings + setting_changes)

Revision ID: b7e2c4d9a1f3
Revises: a3c1f0e2b7d4
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c4d9a1f3"
down_revision: str | None = "a3c1f0e2b7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "setting_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_setting_changes_key"), "setting_changes", ["key"], unique=False)
    op.create_index(op.f("ix_setting_changes_changed_at"), "setting_changes", ["changed_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_setting_changes_changed_at"), table_name="setting_changes")
    op.drop_index(op.f("ix_setting_changes_key"), table_name="setting_changes")
    op.drop_table("setting_changes")
    op.drop_table("app_settings")
