"""Создать таблицы Job и истории переходов."""

from alembic import op
import sqlalchemy as sa

revision = "0001_jobs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("id", sa.String(36), nullable=False, unique=True),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("result", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_jobs_queue", "jobs", ["state", "created_at", "sequence"])
    op.create_table(
        "job_transitions",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("at", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("correlation_id", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_transitions")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_table("jobs")
