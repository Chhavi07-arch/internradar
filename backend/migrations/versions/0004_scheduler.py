"""Durable public and owner-scoped worker scheduling state."""

import sqlalchemy as sa
from alembic import op

revision = "0004_scheduler"
down_revision = "0003_auth"


def _state(*names: str) -> tuple:
    return (
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"name IN {names}"),
        sa.CheckConstraint("(claim_token IS NULL) = (lease_until IS NULL)"),
        sa.CheckConstraint("failures >= 0"),
    )


def upgrade() -> None:
    op.create_table(
        "public_worker_job",
        *_state("public_ats", "public_boards", "public_funding"),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "private_worker_job",
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_state("private_purge", "private_revocation"),
        sa.PrimaryKeyConstraint("owner_user_id", "name"),
    )
    owner = "owner_user_id = NULLIF(current_setting('internradar.owner_id', true), '')::uuid"
    op.execute("ALTER TABLE private_worker_job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE private_worker_job FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY own_rows ON private_worker_job USING ({owner}) WITH CHECK ({owner})")


def downgrade() -> None:
    op.drop_table("private_worker_job")
    op.drop_table("public_worker_job")
