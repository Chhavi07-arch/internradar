"""Track onboarding independent of the student's editable profile."""

import sqlalchemy as sa
from alembic import op

revision = "0005_profile"
down_revision = "0004_scheduler"


def upgrade() -> None:
    op.add_column(
        "student_profile",
        sa.Column("onboarding_state", sa.String(12), nullable=False, server_default="pending"),
    )
    op.create_check_constraint(
        "ck_student_profile_onboarding",
        "student_profile",
        "onboarding_state IN ('pending', 'skipped', 'completed')",
    )
    # Migration-only policy lets the table owner backfill despite FORCE RLS.
    op.execute("CREATE POLICY onboarding_backfill ON student_profile TO CURRENT_USER USING (true)")
    op.execute("""UPDATE student_profile SET onboarding_state = 'completed'
        WHERE COALESCE(data->>'name', '') <> '' OR COALESCE(data->>'college', '') <> ''
        OR COALESCE(data->>'branch', '') <> '' OR data->>'batch' IS NOT NULL
        OR COALESCE(data->>'roles', '[]') <> '[]'""")
    op.execute("DROP POLICY onboarding_backfill ON student_profile")


def downgrade() -> None:
    op.drop_constraint("ck_student_profile_onboarding", "student_profile", type_="check")
    op.drop_column("student_profile", "onboarding_state")
