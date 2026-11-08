"""rename default owner to lore

Keeps existing content attached to the same owner after the InstaBrain → Lore rename.

Revision ID: c1d2e3f4a5b6
Revises: b3809fe6d4d4
Create Date: 2026-11-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b3809fe6d4d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET email = 'owner@lore.local' "
               "WHERE email = 'owner@' || 'instabrain.local'")


def downgrade() -> None:
    op.execute("UPDATE users SET email = 'owner@' || 'instabrain.local' "
               "WHERE email = 'owner@lore.local'")
