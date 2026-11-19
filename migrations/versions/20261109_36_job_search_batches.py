"""将对话求职结果保存为独立批次及不可覆盖的岗位版本。"""
from alembic import op

revision = "20261109_36"
down_revision = "20261109_35"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE career_assistant.job_search_batches (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
            source_message_id UUID NOT NULL REFERENCES career_assistant.messages(id) ON DELETE CASCADE,
            source_turn_id UUID REFERENCES career_assistant.agent_turns(id) ON DELETE SET NULL,
            batch_no INTEGER NOT NULL,
            conditions JSONB NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            search_status TEXT NOT NULL DEFAULT 'created',
            loaded_count INTEGER NOT NULL DEFAULT 0,
            has_more BOOLEAN NOT NULL DEFAULT FALSE,
            focused_job_ref TEXT,
            last_focused_at TIMESTAMPTZ,
            analysis_requested BOOLEAN NOT NULL DEFAULT FALSE,
            analysis_context JSONB NOT NULL DEFAULT '{}',
            analysis_turn_id UUID REFERENCES career_assistant.agent_turns(id) ON DELETE SET NULL,
            operation_receipts JSONB NOT NULL DEFAULT '{}',
            legacy_imported BOOLEAN NOT NULL DEFAULT FALSE,
            legacy_total_count INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(conversation_id, batch_no)
        );
        CREATE INDEX ix_job_search_source ON career_assistant.job_search_batches(source_message_id);
        CREATE TABLE career_assistant.job_search_job_versions (
            batch_id UUID NOT NULL REFERENCES career_assistant.job_search_batches(id) ON DELETE CASCADE,
            job_ref TEXT NOT NULL,
            revision INTEGER NOT NULL,
            display_index INTEGER NOT NULL,
            bridge_key TEXT NOT NULL,
            payload JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(batch_id,job_ref,revision)
        );
        CREATE INDEX ix_job_search_version ON career_assistant.job_search_job_versions(batch_id,revision);
    """)


def downgrade():
    op.execute("DROP TABLE career_assistant.job_search_job_versions; DROP TABLE career_assistant.job_search_batches;")
