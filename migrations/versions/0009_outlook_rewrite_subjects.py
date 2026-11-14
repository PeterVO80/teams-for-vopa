"""Rewrite all Outlook appointments once (title now includes the remark).

Data-only migration: marks every planning entry as "to be written to
Outlook" again, so the outlook-sync container replaces the existing
appointments with ones whose title contains the planning remark. The
modification time (updated_at) is left untouched.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE assignments SET outlook_synced_at = NULL")


def downgrade():
    pass  # nothing to undo: the appointments are simply rewritten once
