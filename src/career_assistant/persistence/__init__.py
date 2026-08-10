"""求职助手专用的 PostgreSQL 持久化边界。

这里不复用现有的 SQLite DatabaseManager，避免把求职对话和公众号工作流耦合在一起。
"""

from src.career_assistant.persistence.conversation_repository import (
    CareerConversationRepository,
)
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
    ModelProfileRecord,
)
from src.career_assistant.persistence.records import (
    AgentTurnRecord,
    AgentTurnStatus,
    ConversationRecord,
    MessageRecord,
    MessageRole,
    SessionSummaryRecord,
)

__all__ = [
    "CareerConversationRepository",
    "CareerDatabase",
    "CareerModelProfileRepository",
    "ModelCostTier",
    "ModelProfileDraft",
    "ModelProfileRecord",
    "AgentTurnRecord",
    "AgentTurnStatus",
    "ConversationRecord",
    "MessageRecord",
    "MessageRole",
    "SessionSummaryRecord",
]
