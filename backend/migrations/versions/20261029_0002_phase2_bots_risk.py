"""phase2 bots risk

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 10:00:55.897246
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('bots',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('strategy_id', sa.String(length=40), nullable=False),
    sa.Column('underlying', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('creation_method', sa.Enum('AI', 'MANUAL', 'TEMPLATE', 'CLONE', name='bot_creation_method'), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'ACTIVE', 'PAUSED', 'STOPPED', name='bot_status'), nullable=False),
    sa.Column('mode', sa.Enum('PAPER', 'LIVE', name='trading_mode'), nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_bots_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bots'))
    )
    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.create_index('ix_bots_user_status', ['user_id', 'status'], unique=False)

    op.create_table('risk_settings',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('capital', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('max_risk_per_trade', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('daily_loss_limit', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('daily_profit_lock', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('max_entries_per_day', sa.Integer(), nullable=False),
    sa.Column('max_open_positions', sa.Integer(), nullable=False),
    sa.Column('max_lots_per_trade', sa.Integer(), nullable=False),
    sa.Column('re_entry_after_stop_loss', sa.Boolean(), nullable=False),
    sa.Column('square_off_time_ist', sa.String(length=5), nullable=False),
    sa.Column('kill_switch_engaged', sa.Boolean(), nullable=False),
    sa.Column('kill_switch_changed_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_risk_settings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_risk_settings'))
    )
    op.create_table('bot_config_versions',
    sa.Column('bot_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_bot_config_versions_bot_id_bots'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bot_config_versions')),
    sa.UniqueConstraint('bot_id', 'version', name='uq_bot_config_versions_bot_version')
    )
    with op.batch_alter_table('bot_config_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_config_versions_bot_id'), ['bot_id'], unique=False)


def downgrade() -> None:

    with op.batch_alter_table('bot_config_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bot_config_versions_bot_id'))

    op.drop_table('bot_config_versions')
    op.drop_table('risk_settings')
    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.drop_index('ix_bots_user_status')

    op.drop_table('bots')
    for enum_name in ("bot_creation_method", "bot_status", "trading_mode"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
