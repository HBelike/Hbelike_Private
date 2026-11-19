"""保存独立的外部招聘账号、会话与近三十天消息。"""

from alembic import op

revision = "20261109_35"
down_revision = "20261104_34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE career_assistant.external_chat_accounts (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            platform TEXT NOT NULL CHECK (platform = 'boss'),
            platform_account_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            last_synced_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, actor_id, platform, platform_account_id)
        )
    """)
    op.execute("""
        CREATE TABLE career_assistant.external_chat_conversations (
            id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES career_assistant.external_chat_accounts(id) ON DELETE CASCADE,
            platform_conversation_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            peer_name TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            job_title TEXT NOT NULL DEFAULT '',
            unread_count INTEGER CHECK (unread_count >= 0),
            last_message_at TIMESTAMPTZ,
            last_message_preview TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            last_synced_at TIMESTAMPTZ NOT NULL,
            UNIQUE (account_id, platform_conversation_id)
        )
    """)
    op.execute("""
        CREATE TABLE career_assistant.external_chat_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES career_assistant.external_chat_conversations(id) ON DELETE CASCADE,
            platform_message_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
            message_type TEXT NOT NULL CHECK (message_type IN ('text', 'image', 'file', 'audio', 'unknown')),
            content_text TEXT NOT NULL,
            sent_at TIMESTAMPTZ NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            received_at TIMESTAMPTZ NOT NULL,
            UNIQUE (conversation_id, platform_message_id)
        )
    """)
    op.execute("CREATE INDEX idx_external_chat_messages_time ON career_assistant.external_chat_messages (sent_at)")
    op.execute("CREATE INDEX idx_external_chat_messages_conversation_time ON career_assistant.external_chat_messages (conversation_id, sent_at DESC, id DESC)")
    op.execute("CREATE INDEX idx_external_chat_conversations_account_time ON career_assistant.external_chat_conversations (account_id, last_message_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE career_assistant.external_chat_messages")
    op.execute("DROP TABLE career_assistant.external_chat_conversations")
    op.execute("DROP TABLE career_assistant.external_chat_accounts")
