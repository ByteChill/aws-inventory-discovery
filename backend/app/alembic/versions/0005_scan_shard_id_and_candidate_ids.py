"""scan shard id (named by the API) and the candidate ids kept for the review page

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('t_inv_aws_scan_shard', sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False))
    op.add_column('t_inv_aws_scan_shard', sa.Column('candidate_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index('uq_t_inv_aws_scan_shard_id', 't_inv_aws_scan_shard', ['id'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_t_inv_aws_scan_shard_id', table_name='t_inv_aws_scan_shard')
    op.drop_column('t_inv_aws_scan_shard', 'candidate_ids')
    op.drop_column('t_inv_aws_scan_shard', 'id')
