"""index of the queued tasks per account (enqueue-time dedupe of scheduled children)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_t_inv_aws_scan_task_queued_account', 't_inv_aws_scan_task', ['account_id'], unique=False, postgresql_where=sa.text("status = 'queued'"))


def downgrade() -> None:
    op.drop_index('ix_t_inv_aws_scan_task_queued_account', table_name='t_inv_aws_scan_task', postgresql_where=sa.text("status = 'queued'"))
