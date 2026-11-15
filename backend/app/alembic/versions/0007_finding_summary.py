"""findings summary: current severity counts per resource (v2)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_inv_aws_finding_summary",
        sa.Column("account_id", sa.CHAR(length=12), nullable=False),
        sa.Column("aws_region", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("critical_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("high_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("medium_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("low_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "account_id", "aws_region", "resource_type", "resource_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("t_inv_aws_finding_summary")
