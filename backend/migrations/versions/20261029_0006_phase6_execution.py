"""phase6 execution

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-29 12:49:01.310548
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('worker_leases',
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('owner', sa.String(length=80), nullable=False),
    sa.Column('lease_until', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_worker_leases'))
    )
    op.create_table('daily_risk_state',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('trading_day', sa.Date(), nullable=False),
    sa.Column('realized_gross', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('charges', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('entries', sa.Integer(), nullable=False),
    sa.Column('stop_loss_exits', sa.Integer(), nullable=False),
    sa.Column('loss_limit_hit_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('profit_lock_hit_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_daily_risk_state_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_daily_risk_state')),
    sa.UniqueConstraint('user_id', 'trading_day', name='uq_daily_risk_user_day')
    )
    op.create_table('paper_broker_orders',
    sa.Column('broker_order_id', sa.String(length=64), nullable=False),
    sa.Column('client_order_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('order_type', sa.String(length=8), nullable=False),
    sa.Column('limit_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('contract', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('filled_qty', sa.Integer(), nullable=False),
    sa.Column('avg_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('reject_reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_paper_broker_orders_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('broker_order_id', name=op.f('pk_paper_broker_orders')),
    sa.UniqueConstraint('client_order_id', name='uq_paper_orders_client_id')
    )
    op.create_table('paper_broker_fills',
    sa.Column('trade_id', sa.String(length=64), nullable=False),
    sa.Column('broker_order_id', sa.String(length=64), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['broker_order_id'], ['paper_broker_orders.broker_order_id'], name=op.f('fk_paper_broker_fills_broker_order_id_paper_broker_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('trade_id', name=op.f('pk_paper_broker_fills'))
    )
    with op.batch_alter_table('paper_broker_fills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_paper_broker_fills_broker_order_id'), ['broker_order_id'], unique=False)

    op.create_table('orders',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('bot_id', sa.Uuid(), nullable=True),
    sa.Column('signal_id', sa.Uuid(), nullable=True),
    sa.Column('position_id', sa.Uuid(), nullable=True),
    sa.Column('client_order_id', sa.String(length=64), nullable=False),
    sa.Column('broker', sa.String(length=20), nullable=False),
    sa.Column('broker_order_id', sa.String(length=64), nullable=True),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('purpose', sa.String(length=8), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('underlying', sa.String(length=32), nullable=False),
    sa.Column('trading_symbol', sa.String(length=64), nullable=False),
    sa.Column('security_id', sa.String(length=32), nullable=True),
    sa.Column('expiry', sa.Date(), nullable=False),
    sa.Column('strike', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('option_type', sa.String(length=2), nullable=False),
    sa.Column('lot_size', sa.Integer(), nullable=False),
    sa.Column('lot_size_source', sa.String(length=24), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('order_type', sa.String(length=8), nullable=False),
    sa.Column('limit_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('stop_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('trailing_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('filled_qty', sa.Integer(), nullable=False),
    sa.Column('avg_fill_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('reject_reason', sa.String(length=500), nullable=True),
    sa.Column('exit_reason', sa.String(length=24), nullable=True),
    sa.Column('data_source', sa.String(length=16), nullable=False),
    sa.Column('risk_snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('submitted_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('terminal_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_orders_bot_id_bots'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['signal_id'], ['bot_signals.id'], name=op.f('fk_orders_signal_id_bot_signals'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_orders_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('client_order_id', name='uq_orders_client_order_id')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index('ix_orders_status', ['status'], unique=False)
        batch_op.create_index('ix_orders_user_created', ['user_id', 'created_at'], unique=False)

    op.create_table('fills',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('broker', sa.String(length=20), nullable=False),
    sa.Column('broker_fill_id', sa.String(length=64), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('charges', sa.JSON(), nullable=False),
    sa.Column('charges_total', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_fills_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fills_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fills')),
    sa.UniqueConstraint('broker', 'broker_fill_id', name='uq_fills_broker_fill')
    )
    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fills_order_id'), ['order_id'], unique=False)

    op.create_table('order_events',
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('from_status', sa.String(length=24), nullable=True),
    sa.Column('to_status', sa.String(length=24), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_events_order_id_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_events'))
    )
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_order_events_order_id'), ['order_id'], unique=False)

    op.create_table('positions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('bot_id', sa.Uuid(), nullable=True),
    sa.Column('entry_order_id', sa.Uuid(), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('underlying', sa.String(length=32), nullable=False),
    sa.Column('trading_symbol', sa.String(length=64), nullable=False),
    sa.Column('security_id', sa.String(length=32), nullable=True),
    sa.Column('expiry', sa.Date(), nullable=False),
    sa.Column('strike', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('option_type', sa.String(length=2), nullable=False),
    sa.Column('lot_size', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('entry_qty', sa.Integer(), nullable=False),
    sa.Column('exit_qty', sa.Integer(), nullable=False),
    sa.Column('avg_entry', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('avg_exit', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('stop_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('trailing_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('high_water', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('last_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('last_price_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('exit_reason', sa.String(length=24), nullable=True),
    sa.Column('realized_gross', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('charges_total', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('data_source', sa.String(length=16), nullable=False),
    sa.Column('opened_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('closed_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], name=op.f('fk_positions_bot_id_bots'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['entry_order_id'], ['orders.id'], name=op.f('fk_positions_entry_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_positions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_positions'))
    )
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.create_index('ix_positions_user_status', ['user_id', 'status'], unique=False)

    with op.batch_alter_table('risk_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exit_on_loss_limit', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('square_off_on_kill_switch', sa.Boolean(), nullable=False, server_default=sa.true()))

    # orders <-> positions reference each other: add this FK once both exist.
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_orders_position_id_positions', 'positions',
                                    ['position_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_orders_position_id_positions', type_='foreignkey')

    with op.batch_alter_table('risk_settings', schema=None) as batch_op:
        batch_op.drop_column('square_off_on_kill_switch')
        batch_op.drop_column('exit_on_loss_limit')

    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_index('ix_positions_user_status')

    op.drop_table('positions')
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_order_events_order_id'))

    op.drop_table('order_events')
    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fills_order_id'))

    op.drop_table('fills')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_user_created')
        batch_op.drop_index('ix_orders_status')

    op.drop_table('orders')
    with op.batch_alter_table('paper_broker_fills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_paper_broker_fills_broker_order_id'))

    op.drop_table('paper_broker_fills')
    op.drop_table('paper_broker_orders')
    op.drop_table('daily_risk_state')
    op.drop_table('worker_leases')

