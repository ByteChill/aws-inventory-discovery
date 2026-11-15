"""account scan policy

Revision ID: 87b6f3ba8251
Revises: 0007
Create Date: 2026-11-16 02:04:59.685167

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "87b6f3ba8251"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_inv_aws_account_scan_region",
        sa.Column("account_id", sa.CHAR(length=12), nullable=False),
        sa.Column("region", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["t_inv_aws_account.account_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", "region"),
    )
    op.add_column(
        "t_inv_aws_account",
        sa.Column(
            "source",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("'legacy'"),
            nullable=False,
        ),
    )
    op.add_column(
        "t_inv_aws_account",
        sa.Column(
            "scan_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "t_inv_aws_account",
        sa.Column(
            "scan_region_mode",
            sqlmodel.sql.sqltypes.AutoString(),
            server_default=sa.text("'all'"),
            nullable=False,
        ),
    )
    op.add_column(
        "t_inv_aws_account",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Existing rows came from the old registry-driven snapshot.
    op.execute(
        "UPDATE t_inv_aws_account"
        " SET source = 'legacy',"
        "     updated_at = coalesce(registry_fetched_at, now())"
    )
    op.alter_column(
        "t_inv_aws_account",
        "source",
        server_default=sa.text("'manual'"),
        existing_type=sqlmodel.sql.sqltypes.AutoString(),
        existing_nullable=False,
    )
    # Manual/imported accounts do not have a registry fetch time.
    op.alter_column(
        "t_inv_aws_account",
        "registry_fetched_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.create_index(
        "ix_t_inv_aws_account_scan_enabled",
        "t_inv_aws_account",
        ["scan_enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE t_inv_aws_account SET registry_fetched_at = updated_at"
        " WHERE registry_fetched_at IS NULL"
    )
    op.alter_column(
        "t_inv_aws_account",
        "registry_fetched_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.drop_index("ix_t_inv_aws_account_scan_enabled", table_name="t_inv_aws_account")
    op.drop_column("t_inv_aws_account", "updated_at")
    op.drop_column("t_inv_aws_account", "scan_region_mode")
    op.drop_column("t_inv_aws_account", "scan_enabled")
    op.drop_column("t_inv_aws_account", "source")
    op.drop_table("t_inv_aws_account_scan_region")
