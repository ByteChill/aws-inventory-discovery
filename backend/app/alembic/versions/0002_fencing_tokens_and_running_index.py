"""fencing tokens for task claims and scope leases; running-task index for the claim query

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('t_inv_aws_scan_task', sa.Column('claim_token', sa.String(), nullable=True))
    op.add_column('t_inv_aws_scan_shard', sa.Column('lease_token', sa.String(), nullable=True))
    op.create_index('ix_t_inv_aws_scan_task_running_account', 't_inv_aws_scan_task', ['account_id'], unique=False, postgresql_where=sa.text("status = 'running'"))


def downgrade() -> None:
    op.drop_index('ix_t_inv_aws_scan_task_running_account', table_name='t_inv_aws_scan_task', postgresql_where=sa.text("status = 'running'"))
    op.drop_column('t_inv_aws_scan_shard', 'lease_token')
    op.drop_column('t_inv_aws_scan_task', 'claim_token')
