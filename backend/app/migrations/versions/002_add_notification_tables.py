"""add notification tables

Revision ID: 002
Create Date: 2026-07-29 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 事件队列表
    op.create_table(
        'agent_event_queue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_event_queue_type', 'agent_event_queue', ['type'])
    op.create_index('idx_event_queue_status', 'agent_event_queue', ['status'])

    # 通知规则表
    op.create_table(
        'agent_notification_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('condition', sa.String(255), nullable=True),
        sa.Column('target', sa.String(128), nullable=False),
        sa.Column('channels', sa.String(255), nullable=False, server_default='["inapp"]'),
        sa.Column('title_template', sa.String(255), nullable=False),
        sa.Column('body_template', sa.String(512), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_rules_event_type', 'agent_notification_rules', ['event_type'])

    # 通知表
    op.create_table(
        'agent_notifications',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('recipient', sa.String(64), nullable=False),
        sa.Column('type', sa.String(32), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False, server_default='info'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False, server_default='inapp'),
        sa.Column('status', sa.String(16), nullable=False, server_default='unread'),
        sa.Column('source', sa.String(64), nullable=True),
        sa.Column('ref_conversation_id', sa.String(36), nullable=True),
        sa.Column('ref_chain_id', sa.String(128), nullable=True),
        sa.Column('ref_plan_id', sa.String(64), nullable=True),
        sa.Column('action_data', sa.Text(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_notif_recipient', 'agent_notifications', ['recipient'])
    op.create_index('idx_notif_status', 'agent_notifications', ['status'])

    # 用户订阅表
    op.create_table(
        'agent_user_subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(64), nullable=False),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('channel', sa.String(16), nullable=False, server_default='inapp'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_sub_user_id', 'agent_user_subscriptions', ['user_id'])


def downgrade() -> None:
    op.drop_table('agent_user_subscriptions')
    op.drop_table('agent_notifications')
    op.drop_table('agent_notification_rules')
    op.drop_table('agent_event_queue')
