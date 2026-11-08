"""initial schema: the 7 tables of DESIGN section 6 and their indexes

Revision ID: 0001
Revises:
Create Date: 2026-11-08

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trigram indexes need pg_trgm. The extension may already be installed (it usually
    # is on a shared cluster), so only create it when missing.
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public')
    op.create_table('t_inv_aws_account',
    sa.Column('account_id', sa.CHAR(length=12), nullable=False),
    sa.Column('account_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('account_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('eim_id', sa.CHAR(length=8), nullable=True),
    sa.Column('eim_app', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('ba_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('inactive_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('registry_fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('access_status', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'unknown'"), nullable=False),
    sa.Column('access_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('access_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_scan_ok_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('account_id')
    )
    op.create_index('ix_t_inv_aws_account_access_status', 't_inv_aws_account', ['access_status'], unique=False)
    op.create_index('ix_t_inv_aws_account_account_type', 't_inv_aws_account', ['account_type'], unique=False)
    op.create_index('ix_t_inv_aws_account_ba_id', 't_inv_aws_account', ['ba_id'], unique=False)
    op.create_index('ix_t_inv_aws_account_eim_id', 't_inv_aws_account', ['eim_id'], unique=False)
    op.create_table('t_inv_aws_scan_request',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('trigger', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('caller_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('priority', sa.SmallInteger(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('idempotency_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_table('t_inv_aws_scan_shard',
    sa.Column('account_id', sa.CHAR(length=12), nullable=False),
    sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('resource_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('lease_task_id', sa.BigInteger(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pending_task_id', sa.BigInteger(), nullable=True),
    sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_succeeded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_task_id', sa.BigInteger(), nullable=True),
    sa.Column('last_error_code', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('guard_state', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'normal'"), nullable=False),
    sa.Column('guard_detected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('guard_confirmed_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('account_id', 'region', 'resource_type')
    )
    op.create_table('t_inv_aws_account_region',
    sa.Column('account_id', sa.CHAR(length=12), nullable=False),
    sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('opt_in_status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('reachable', sa.Boolean(), nullable=False),
    sa.Column('config_recorder', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['t_inv_aws_account.account_id'], ),
    sa.PrimaryKeyConstraint('account_id', 'region')
    )
    op.create_table('t_inv_aws_resource',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('arn', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('account_id', sa.CHAR(length=12), nullable=False),
    sa.Column('aws_region', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('resource_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('resource_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('resource_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('configuration', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('relationships', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('live_raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('config_capture_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('live_observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('observed_source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('missing_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('absent_in_scan_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('missing_checks', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_task_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['t_inv_aws_account.account_id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'aws_region', 'resource_type', 'resource_id', name='uq_t_inv_aws_resource_identity')
    )
    op.create_index('ix_t_inv_aws_resource_arn', 't_inv_aws_resource', ['arn'], unique=False)
    op.create_index('ix_t_inv_aws_resource_arn_trgm', 't_inv_aws_resource', ['arn'], unique=False, postgresql_using='gin', postgresql_ops={'arn': 'gin_trgm_ops'})
    op.create_index('ix_t_inv_aws_resource_name_trgm', 't_inv_aws_resource', ['resource_name'], unique=False, postgresql_using='gin', postgresql_ops={'resource_name': 'gin_trgm_ops'})
    op.create_index('ix_t_inv_aws_resource_resource_id_trgm', 't_inv_aws_resource', ['resource_id'], unique=False, postgresql_using='gin', postgresql_ops={'resource_id': 'gin_trgm_ops'})
    op.create_index('ix_t_inv_aws_resource_scope_live', 't_inv_aws_resource', ['account_id', 'aws_region', 'resource_type'], unique=False, postgresql_where=sa.text("status <> 'deleted'"))
    op.create_index('ix_t_inv_aws_resource_tags', 't_inv_aws_resource', ['tags'], unique=False, postgresql_using='gin', postgresql_ops={'tags': 'jsonb_path_ops'})
    op.create_index('ix_t_inv_aws_resource_type_status', 't_inv_aws_resource', ['resource_type', 'status'], unique=False)
    op.create_table('t_inv_aws_scan_task',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('scan_request_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('account_id', sa.CHAR(length=12), nullable=False),
    sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('resource_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('resource_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('priority', sa.SmallInteger(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('not_before', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('attempt', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('max_attempts', sa.SmallInteger(), server_default=sa.text('3'), nullable=False),
    sa.Column('worker_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('coalesced_into', sa.BigInteger(), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['coalesced_into'], ['t_inv_aws_scan_task.id'], ),
    sa.ForeignKeyConstraint(['scan_request_id'], ['t_inv_aws_scan_request.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_t_inv_aws_scan_task_queue', 't_inv_aws_scan_task', ['priority', 'not_before'], unique=False, postgresql_where=sa.text("status = 'queued'"))
    op.create_index('ix_t_inv_aws_scan_task_scan_request_id', 't_inv_aws_scan_task', ['scan_request_id'], unique=False)
    op.create_index('uq_t_inv_aws_scan_task_resource_inflight', 't_inv_aws_scan_task', ['account_id', 'region', 'resource_type', 'resource_id'], unique=True, postgresql_where=sa.text("kind = 'resource' AND status IN ('queued', 'running')"))
    op.create_table('t_inv_aws_resource_relation',
    sa.Column('from_resource_id', sa.BigInteger(), nullable=False),
    sa.Column('to_resource_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('to_resource_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('to_resource_id', sa.BigInteger(), nullable=True),
    sa.Column('relation_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['from_resource_id'], ['t_inv_aws_resource.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_resource_id'], ['t_inv_aws_resource.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('from_resource_id', 'to_resource_type', 'to_resource_key', 'relation_name')
    )
    op.create_index('ix_t_inv_aws_resource_relation_to', 't_inv_aws_resource_relation', ['to_resource_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_t_inv_aws_resource_relation_to', table_name='t_inv_aws_resource_relation')
    op.drop_table('t_inv_aws_resource_relation')
    op.drop_index('uq_t_inv_aws_scan_task_resource_inflight', table_name='t_inv_aws_scan_task', postgresql_where=sa.text("kind = 'resource' AND status IN ('queued', 'running')"))
    op.drop_index('ix_t_inv_aws_scan_task_scan_request_id', table_name='t_inv_aws_scan_task')
    op.drop_index('ix_t_inv_aws_scan_task_queue', table_name='t_inv_aws_scan_task', postgresql_where=sa.text("status = 'queued'"))
    op.drop_table('t_inv_aws_scan_task')
    op.drop_index('ix_t_inv_aws_resource_type_status', table_name='t_inv_aws_resource')
    op.drop_index('ix_t_inv_aws_resource_tags', table_name='t_inv_aws_resource', postgresql_using='gin', postgresql_ops={'tags': 'jsonb_path_ops'})
    op.drop_index('ix_t_inv_aws_resource_scope_live', table_name='t_inv_aws_resource', postgresql_where=sa.text("status <> 'deleted'"))
    op.drop_index('ix_t_inv_aws_resource_resource_id_trgm', table_name='t_inv_aws_resource', postgresql_using='gin', postgresql_ops={'resource_id': 'gin_trgm_ops'})
    op.drop_index('ix_t_inv_aws_resource_name_trgm', table_name='t_inv_aws_resource', postgresql_using='gin', postgresql_ops={'resource_name': 'gin_trgm_ops'})
    op.drop_index('ix_t_inv_aws_resource_arn_trgm', table_name='t_inv_aws_resource', postgresql_using='gin', postgresql_ops={'arn': 'gin_trgm_ops'})
    op.drop_index('ix_t_inv_aws_resource_arn', table_name='t_inv_aws_resource')
    op.drop_table('t_inv_aws_resource')
    op.drop_table('t_inv_aws_account_region')
    op.drop_table('t_inv_aws_scan_shard')
    op.drop_table('t_inv_aws_scan_request')
    op.drop_index('ix_t_inv_aws_account_eim_id', table_name='t_inv_aws_account')
    op.drop_index('ix_t_inv_aws_account_ba_id', table_name='t_inv_aws_account')
    op.drop_index('ix_t_inv_aws_account_account_type', table_name='t_inv_aws_account')
    op.drop_index('ix_t_inv_aws_account_access_status', table_name='t_inv_aws_account')
    op.drop_table('t_inv_aws_account')
