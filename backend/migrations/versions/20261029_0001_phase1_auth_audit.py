"""phase1 auth audit

Revision ID: 0001
Revises: 
Create Date: 2026-10-29 08:37:06.993767
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('user', 'admin', name='user_role'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('email_verified_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('phone_verified_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('failed_login_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('email IS NOT NULL OR phone IS NOT NULL', name=op.f('ck_users_email_or_phone')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_phone'), ['phone'], unique=True)

    op.create_table('audit_logs',
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_logs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index('ix_audit_logs_user_created', ['user_id', 'created_at'], unique=False)

    op.create_table('user_preferences',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('theme', sa.String(length=16), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('reduced_motion', sa.Boolean(), nullable=False),
    sa.Column('haptics_enabled', sa.Boolean(), nullable=False),
    sa.Column('notifications', sa.JSON(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_preferences'))
    )
    op.create_table('user_sessions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('device_name', sa.String(length=120), nullable=True),
    sa.Column('expires_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_sessions'))
    )
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('verification_codes',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.Enum('VERIFY_ACCOUNT', 'PASSWORD_RESET', name='verification_purpose'), nullable=False),
    sa.Column('channel', sa.Enum('EMAIL', 'SMS', name='verification_channel'), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('consumed_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_verification_codes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_verification_codes'))
    )
    with op.batch_alter_table('verification_codes', schema=None) as batch_op:
        batch_op.create_index('ix_verification_codes_user_purpose', ['user_id', 'purpose'], unique=False)

    op.create_table('refresh_tokens',
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('used_at', app.db.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['user_sessions.id'], name=op.f('fk_refresh_tokens_session_id_user_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_session_id'), ['session_id'], unique=False)


def downgrade() -> None:

    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_session_id'))

    op.drop_table('refresh_tokens')
    with op.batch_alter_table('verification_codes', schema=None) as batch_op:
        batch_op.drop_index('ix_verification_codes_user_purpose')

    op.drop_table('verification_codes')
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_sessions_user_id'))

    op.drop_table('user_sessions')
    op.drop_table('user_preferences')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_logs_user_created')
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_phone'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    # PostgreSQL keeps ENUM types after dropping tables.
    for enum_name in ("verification_channel", "verification_purpose", "user_role"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
