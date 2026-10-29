"""phase4 models predictions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29 11:57:24.962102
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('model_versions',
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('horizon_bars', sa.Integer(), nullable=False),
    sa.Column('algorithm', sa.String(length=40), nullable=False),
    sa.Column('feature_version', sa.String(length=16), nullable=False),
    sa.Column('hyperparams', sa.JSON(), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('gate', sa.JSON(), nullable=False),
    sa.Column('data_source', sa.String(length=16), nullable=False),
    sa.Column('data_provider', sa.String(length=20), nullable=False),
    sa.Column('train_rows', sa.Integer(), nullable=False),
    sa.Column('data_from', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('data_to', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('promoted_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('retired_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_model_versions_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_versions'))
    )
    with op.batch_alter_table('model_versions', schema=None) as batch_op:
        batch_op.create_index('ix_model_versions_target', ['symbol', 'timeframe', 'horizon_bars', 'status'], unique=False)

    op.create_table('predictions',
    sa.Column('model_version_id', sa.Uuid(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('timeframe', sa.String(length=8), nullable=False),
    sa.Column('horizon_bars', sa.Integer(), nullable=False),
    sa.Column('bar_ts', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('close', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('p_down', sa.Double(), nullable=False),
    sa.Column('p_sideways', sa.Double(), nullable=False),
    sa.Column('p_up', sa.Double(), nullable=False),
    sa.Column('regime', sa.String(length=20), nullable=True),
    sa.Column('data_source', sa.String(length=16), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=True),
    sa.Column('resolved_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], name=op.f('fk_predictions_model_version_id_model_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_predictions')),
    sa.UniqueConstraint('model_version_id', 'bar_ts', name='uq_predictions_model_bar')
    )
    with op.batch_alter_table('predictions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_predictions_model_version_id'), ['model_version_id'], unique=False)


def downgrade() -> None:

    with op.batch_alter_table('predictions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_predictions_model_version_id'))

    op.drop_table('predictions')
    with op.batch_alter_table('model_versions', schema=None) as batch_op:
        batch_op.drop_index('ix_model_versions_target')

    op.drop_table('model_versions')

