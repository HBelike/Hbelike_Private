"""保存外部聊天执行资格、共享暂停与独立发送操作。"""

from alembic import op

revision = "20261109_37"
down_revision = "20261109_36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE career_assistant.external_chat_accounts
            ADD COLUMN executor_id TEXT,
            ADD COLUMN execution_revision BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN execution_expires_at TIMESTAMPTZ,
            ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN pause_code TEXT NOT NULL DEFAULT '',
            ADD COLUMN pause_reason TEXT NOT NULL DEFAULT ''
    """)
    op.execute("""
        CREATE TABLE career_assistant.external_chat_operations (
            client_operation_id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES career_assistant.external_chat_accounts(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES career_assistant.external_chat_conversations(id) ON DELETE CASCADE,
            executor_id TEXT NOT NULL,
            revision BIGINT NOT NULL,
            content_text TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending','executing','submitting','submitted','accepted','failed','unknown','cancelled','expired')),
            cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
            submission_started BOOLEAN NOT NULL DEFAULT FALSE,
            execution_settled BOOLEAN NOT NULL DEFAULT FALSE,
            platform_message_id TEXT,
            error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            retain_until TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_external_chat_operations_account ON career_assistant.external_chat_operations (account_id, created_at DESC)")
    op.execute("CREATE INDEX idx_external_chat_operations_retention ON career_assistant.external_chat_operations (retain_until)")


def downgrade() -> None:
    op.execute("DROP TABLE career_assistant.external_chat_operations")
    op.execute("""
        ALTER TABLE career_assistant.external_chat_accounts
            DROP COLUMN executor_id, DROP COLUMN execution_revision,
            DROP COLUMN execution_expires_at, DROP COLUMN paused,
            DROP COLUMN pause_code, DROP COLUMN pause_reason
    """)
