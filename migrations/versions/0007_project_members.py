"""Project members (team per project)

Revision ID: 0007
Revises: 0006

Non-admin employees now only see the projects they are linked to. So that
nobody loses access to their own work after the update, every existing
project gets as members everyone who already has a task (assignee) or a
planning entry on it. The project manager always counts as linked and is
not stored as a member.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_project_members_project_id_projects"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_project_members_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "user_id", name=op.f("pk_project_members")),
    )
    op.execute("""
        INSERT INTO project_members (project_id, user_id)
        SELECT DISTINCT project_id, user_id FROM (
            SELECT project_id, assignee_id AS user_id FROM tasks
            WHERE project_id IS NOT NULL AND assignee_id IS NOT NULL
            UNION
            SELECT project_id, user_id FROM assignments WHERE project_id IS NOT NULL
        ) AS people
    """)


def downgrade():
    op.drop_table("project_members")
