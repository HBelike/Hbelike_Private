"""为对话消息保存职位搜索卡片。"""

from alembic import op

revision = "20261104_34"
down_revision = "20260901_33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE career_assistant.messages ADD COLUMN ui_payload JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE career_assistant.messages DROP COLUMN ui_payload")
