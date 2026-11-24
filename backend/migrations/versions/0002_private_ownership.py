"""Separate account-owned data from the public catalogue."""

import sqlalchemy as sa
from alembic import op

revision = "0002_private_ownership"
down_revision = "0001_public_catalogue"

PRIVATE_TABLES = (
    "account",
    "user_opportunity",
    "mail_connection",
    "mail_delivery",
    "private_opportunity",
)
OWNER = "NULLIF(current_setting('internradar.owner_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("identity_issuer", sa.Text(), nullable=False),
        sa.Column("identity_subject", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="customer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("identity_issuer", "identity_subject"),
        sa.CheckConstraint("role IN ('admin', 'customer')"),
    )
    op.create_table(
        "user_opportunity",
        _owner(),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seen_at", sa.DateTime(timezone=True)),
        sa.Column("bookmarked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text()),
        sa.PrimaryKeyConstraint("owner_user_id", "opportunity_id"),
    )
    op.create_table(
        "mail_connection",
        _owner(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("encrypted_credentials", sa.LargeBinary()),
        sa.Column("key_version", sa.Text()),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("owner_user_id", "id"),
        sa.UniqueConstraint("owner_user_id", "provider", "external_id"),
        sa.CheckConstraint("provider IN ('gmail', 'forwarding')"),
        sa.CheckConstraint("(encrypted_credentials IS NULL) = (key_version IS NULL)"),
    )
    op.create_table(
        "mail_delivery",
        _owner(),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("kind", sa.Text()),
        sa.Column("raw_ciphertext", sa.LargeBinary()),
        sa.Column("raw_key_version", sa.Text()),
        sa.Column("raw_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("owner_user_id", "connection_id", "delivery_id"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "connection_id"],
            ["mail_connection.owner_user_id", "mail_connection.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(raw_ciphertext IS NULL AND raw_key_version IS NULL AND raw_expires_at IS NULL) OR "
            "(raw_ciphertext IS NOT NULL AND raw_key_version IS NOT NULL AND "
            "raw_expires_at IS NOT NULL AND processed_at IS NULL)"
        ),
    )
    op.create_table(
        "private_opportunity",
        _owner(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "public_opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunity.id", ondelete="SET NULL"),
        ),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True)),
        sa.Column("bookmarked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("owner_user_id", "connection_id", "delivery_id", "position"),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "connection_id", "delivery_id"],
            [
                "mail_delivery.owner_user_id",
                "mail_delivery.connection_id",
                "mail_delivery.delivery_id",
            ],
            ondelete="CASCADE",
        ),
    )
    for table in PRIVATE_TABLES:
        column = "id" if table == "account" else "owner_user_id"
        predicate = f"{column} = {OWNER}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY own_rows ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )


def _owner() -> sa.Column:
    return sa.Column(
        "owner_user_id", sa.Uuid(), sa.ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )


def downgrade() -> None:
    for name in reversed(PRIVATE_TABLES):
        op.drop_table(name)
