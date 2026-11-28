"""Добавить ключ идемпотентности и хеш запроса к Job."""

from alembic import op
import sqlalchemy as sa

revision = "0002_job_idempotency"
down_revision = "0001_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("idempotency_key", sa.String(255)))
    op.add_column("jobs", sa.Column("request_hash", sa.String(64)))
    op.create_index("uq_jobs_idempotency_key", "jobs", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_jobs_idempotency_key", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("request_hash")
        batch.drop_column("idempotency_key")
