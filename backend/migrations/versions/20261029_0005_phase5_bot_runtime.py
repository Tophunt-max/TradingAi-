"""phase5 bot runtime

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29 12:22:19.903713
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('worker_heartbeats',
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('instance', sa.String(length=80), nullable=False),
    sa.Column('last_seen', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('info', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_worker_heartbeats'))
    )
    op.create_table('bot_runs',
    sa.Column('bot_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('config_version', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('started_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('ended_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('end_reason', sa.String(length=200), nullable=True),
    sa.Column('last_heartbeat_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_bar_ts', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('last_error_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('consecutive_errors', sa.Integer(), nullable=False),
    sa.Column('signals_count', sa.Integer(), nullable=False),
    sa.Column('lease_owner', sa.String(length=80), nullable=True),
    sa.Column('lease_until', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_bot_runs_bot_id_bots'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_bot_runs_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bot_runs'))
    )
    with op.batch_alter_table('bot_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_runs_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index('ix_bot_runs_status', ['status', 'lease_until'], unique=False)

    op.create_table('bot_events',
    sa.Column('bot_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_bot_events_bot_id_bots'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['bot_runs.id'], name=op.f('fk_bot_events_run_id_bot_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bot_events'))
    )
    with op.batch_alter_table('bot_events', schema=None) as batch_op:
        batch_op.create_index('ix_bot_events_bot_ts', ['bot_id', 'ts'], unique=False)

    op.create_table('bot_signals',
    sa.Column('bot_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('config_version', sa.Integer(), nullable=False),
    sa.Column('bar_ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('side', sa.String(length=2), nullable=False),
    sa.Column('underlying', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('regime', sa.String(length=20), nullable=True),
    sa.Column('conditions', sa.JSON(), nullable=False),
    sa.Column('data_source', sa.String(length=16), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_bot_signals_bot_id_bots'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['bot_runs.id'], name=op.f('fk_bot_signals_run_id_bot_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_bot_signals_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bot_signals')),
    sa.UniqueConstraint('run_id', 'bar_ts', name='uq_bot_signals_run_bar')
    )
    with op.batch_alter_table('bot_signals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bot_signals_bot_id'), ['bot_id'], unique=False)
        batch_op.create_index('ix_bot_signals_user_bar', ['user_id', 'bar_ts'], unique=False)

    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status_reason', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('status_changed_at', app.db.base.UTCDateTime(timezone=True), nullable=True))


def downgrade() -> None:

    with op.batch_alter_table('bots', schema=None) as batch_op:
        batch_op.drop_column('status_changed_at')
        batch_op.drop_column('status_reason')

    with op.batch_alter_table('bot_signals', schema=None) as batch_op:
        batch_op.drop_index('ix_bot_signals_user_bar')
        batch_op.drop_index(batch_op.f('ix_bot_signals_bot_id'))

    op.drop_table('bot_signals')
    with op.batch_alter_table('bot_events', schema=None) as batch_op:
        batch_op.drop_index('ix_bot_events_bot_ts')

    op.drop_table('bot_events')
    with op.batch_alter_table('bot_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_bot_runs_status')
        batch_op.drop_index(batch_op.f('ix_bot_runs_bot_id'))

    op.drop_table('bot_runs')
    op.drop_table('worker_heartbeats')

