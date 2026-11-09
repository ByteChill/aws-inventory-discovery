"""candidate count of an abnormal scope (shown by GET /scan-scopes)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('t_inv_aws_scan_shard', sa.Column('candidate_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('t_inv_aws_scan_shard', 'candidate_count')
