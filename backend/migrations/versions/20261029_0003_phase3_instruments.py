"""phase3 instruments

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29 10:26:18.977942
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('instrument_syncs',
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('rejected_rows', sa.Integer(), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=True),
    sa.Column('started_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('finished_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_instrument_syncs'))
    )
    op.create_table('instruments',
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('segment', sa.String(length=40), nullable=True),
    sa.Column('security_id', sa.String(length=20), nullable=False),
    sa.Column('instrument_name', sa.String(length=20), nullable=True),
    sa.Column('trading_symbol', sa.String(length=60), nullable=True),
    sa.Column('symbol_name', sa.String(length=60), nullable=True),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('series', sa.String(length=10), nullable=True),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('strike_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('option_type', sa.String(length=2), nullable=True),
    sa.Column('lot_size', sa.Integer(), nullable=True),
    sa.Column('tick_size', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('freeze_qty', sa.Integer(), nullable=True),
    sa.Column('isin', sa.String(length=12), nullable=True),
    sa.Column('synced_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_instruments')),
    sa.UniqueConstraint('provider', 'source', 'exchange', 'security_id', name='uq_instruments_provider_source_exch_secid')
    )
    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.create_index('ix_instruments_symbol', ['symbol_name', 'instrument_name', 'expiry_date'], unique=False)
        batch_op.create_index('ix_instruments_trading_symbol', ['trading_symbol'], unique=False)


def downgrade() -> None:

    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.drop_index('ix_instruments_trading_symbol')
        batch_op.drop_index('ix_instruments_symbol')

    op.drop_table('instruments')
    op.drop_table('instrument_syncs')

