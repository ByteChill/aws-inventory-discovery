"""custom reports: saved condition trees over the current inventory (v2)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_inv_aws_report",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subject_type", sa.Text(), nullable=True),
        sa.Column("definition_version", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_t_inv_aws_report_name", "t_inv_aws_report", ["name"], unique=True)
    op.create_index("ix_t_inv_aws_report_enabled", "t_inv_aws_report", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_t_inv_aws_report_enabled", table_name="t_inv_aws_report")
    op.drop_index("uq_t_inv_aws_report_name", table_name="t_inv_aws_report")
    op.drop_table("t_inv_aws_report")
