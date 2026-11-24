"""Revocable browser sessions and independent student profiles."""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth"
down_revision = "0002_private_ownership"


def upgrade() -> None:
    op.create_table(
        "web_session",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_session_expiry", "web_session", ["expires_at"])
    op.create_index("ix_web_session_owner", "web_session", ["owner_user_id"])
    op.create_table(
        "student_profile",
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    predicate = "owner_user_id = NULLIF(current_setting('internradar.owner_id', true), '')::uuid"
    op.execute("ALTER TABLE student_profile ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE student_profile FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY own_rows ON student_profile USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    op.drop_table("student_profile")
    op.drop_table("web_session")
