"""add conversation tables

Revision ID: 001
Create Date: 2024-01-01 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建conversations表
    op.create_table(
        'agent_conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('message_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('metadata', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )

    # 创建conversations表索引
    op.create_index('ix_conversations_user_id', 'agent_conversations', ['user_id'])
    op.create_index('ix_conversations_created_at', 'agent_conversations', ['created_at'])
    op.create_index('ix_conversations_is_active', 'agent_conversations', ['is_active'])

    # 创建messages表
    op.create_table(
        'agent_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.Enum('USER', 'ASSISTANT', 'SYSTEM', name='messagerole'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['conversation_id'], ['agent_conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 创建messages表索引
    op.create_index('ix_messages_conversation_id', 'agent_messages', ['conversation_id'])
    op.create_index('ix_messages_created_at', 'agent_messages', ['created_at'])
    op.create_index('ix_messages_role', 'agent_messages', ['role'])


def downgrade() -> None:
    # 删除messages表
    op.drop_index('ix_messages_role', 'agent_messages')
    op.drop_index('ix_messages_created_at', 'agent_messages')
    op.drop_index('ix_messages_conversation_id', 'agent_messages')
    op.drop_table('agent_messages')

    # 删除conversations表
    op.drop_index('ix_conversations_is_active', 'agent_conversations')
    op.drop_index('ix_conversations_created_at', 'agent_conversations')
    op.drop_index('ix_conversations_user_id', 'agent_conversations')
    op.drop_table('agent_conversations')

    # 删除枚举类型
    op.execute("DROP TYPE IF EXISTS messagerole")
