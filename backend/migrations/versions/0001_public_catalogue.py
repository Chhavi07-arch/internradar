"""Build a clean catalogue; never copy legacy mixed listings in this revision."""

import sqlalchemy as sa
from alembic import op

revision = "0001_public_catalogue"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "fundingevent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("company_norm", sa.String(), nullable=False),
        sa.Column("round", sa.String(), nullable=True),
        sa.Column("amount_text", sa.String(), nullable=True),
        sa.Column("amount_usd", sa.Float(), nullable=True),
        sa.Column("lead_investors", sa.String(), nullable=True),
        sa.Column("is_tech", sa.Boolean(), nullable=False),
        sa.Column("headline", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("board_ats", sa.String(), nullable=True),
        sa.Column("board_slug", sa.String(), nullable=True),
        sa.Column("board_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fundingevent_company_norm"), "fundingevent", ["company_norm"], unique=False
    )
    op.create_index(
        op.f("ix_fundingevent_published_at"), "fundingevent", ["published_at"], unique=False
    )
    op.create_table(
        "opportunity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(), nullable=False),
        sa.Column("kind", sa.Enum("INTERNSHIP", "JOB", name="kind"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("company_norm", sa.String(), nullable=False),
        sa.Column("title_norm", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("apply_url", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stipend_min", sa.Float(), nullable=True),
        sa.Column("stipend_max", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("pay_period", sa.String(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligibility", sa.JSON(), nullable=True),
        sa.Column(
            "primary_origin",
            sa.Enum("ATS", "LIST", "BOARD", "EMAIL", name="origin"),
            nullable=False,
        ),
        sa.Column("status", sa.Enum("NEW", "ACTIVE", "CLOSED", name="lifecycle"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("primary_origin <> 'EMAIL'", name="ck_opportunity_public"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_opportunity_city"), "opportunity", ["city"], unique=False)
    op.create_index(
        op.f("ix_opportunity_company_norm"), "opportunity", ["company_norm"], unique=False
    )
    op.create_index(
        op.f("ix_opportunity_first_seen_at"), "opportunity", ["first_seen_at"], unique=False
    )
    op.create_index(op.f("ix_opportunity_status"), "opportunity", ["status"], unique=False)
    op.create_index(op.f("ix_opportunity_uid"), "opportunity", ["uid"], unique=True)
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fundingmention",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("feed", sa.String(), nullable=False),
        sa.Column("headline", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["fundingevent.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index(
        op.f("ix_fundingmention_event_id"), "fundingmention", ["event_id"], unique=False
    )
    op.create_table(
        "sighting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "origin", sa.Enum("ATS", "LIST", "BOARD", "EMAIL", name="origin"), nullable=False
        ),
        sa.Column("native_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("missed_runs", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "origin <> 'EMAIL' AND source NOT LIKE 'gmail:%'", name="ck_sighting_public"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunity.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "native_id"),
    )
    op.create_index(
        op.f("ix_sighting_opportunity_id"), "sighting", ["opportunity_id"], unique=False
    )
    op.create_index(op.f("ix_sighting_url"), "sighting", ["url"], unique=False)
    op.create_table(
        "sourcerun",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("exhaustive", sa.Boolean(), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("kept", sa.Integer(), nullable=False),
        sa.Column("new", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.CheckConstraint("source NOT LIKE 'gmail:%'", name="ck_sourcerun_public"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sourcerun_run_id"), "sourcerun", ["run_id"], unique=False)


def downgrade() -> None:
    for name in ("sourcerun", "sighting", "fundingmention", "run", "opportunity", "fundingevent"):
        op.drop_table(name)
    for name in ("kind", "origin", "lifecycle"):
        op.execute(f"DROP TYPE {name}")
