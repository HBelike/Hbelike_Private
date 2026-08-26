"""按操作记录模型真实用量。"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text

from src.career_assistant.model_clients import CompletionUsage
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.model_profile_repository import ModelProfileRecord


MODEL_OPERATION_KINDS = frozenset(
    {"career_response", "conversation_memory_compaction", "career_memory_extraction"},
)


class CareerModelUsageRepository:
    """利用 `(turn_id, operation_kind)` 保证每种操作只记一行。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def start(
        self,
        turn_id: UUID,
        operation_kind: str,
        requested_profile_id: UUID | None,
        resolved_profile: ModelProfileRecord,
    ) -> UUID:
        if operation_kind not in MODEL_OPERATION_KINDS:
            raise ValueError("模型操作类型无效")
        usage_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_usage (
                      id, turn_id, operation_kind, requested_profile_id,
                      resolved_profile_id, resolved_provider_key,
                      resolved_model_id, status
                    ) VALUES (
                      :id, :turn_id, :operation_kind, :requested_profile_id,
                      :resolved_profile_id, :resolved_provider_key,
                      :resolved_model_id, 'started'
                    )
                    ON CONFLICT (turn_id, operation_kind) WHERE turn_id IS NOT NULL DO UPDATE
                    SET turn_id = EXCLUDED.turn_id
                    RETURNING id
                    """,
                ),
                {
                    "id": usage_id,
                    "turn_id": turn_id,
                    "operation_kind": operation_kind,
                    "requested_profile_id": requested_profile_id,
                    "resolved_profile_id": resolved_profile.id,
                    "resolved_provider_key": resolved_profile.provider_key,
                    "resolved_model_id": resolved_profile.model_id,
                },
            ).mappings().one()
        return row["id"]

    def finish(
        self,
        usage_id: UUID,
        *,
        status: str,
        usage: CompletionUsage,
        error_code: str | None = None,
    ) -> None:
        if status not in {"succeeded", "rate_limited", "failed"}:
            raise ValueError("模型用量终态无效")
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.model_usage
                    SET status = :status, input_tokens = :input_tokens,
                        output_tokens = :output_tokens, error_code = :error_code,
                        completed_at = NOW()
                    WHERE id = :usage_id
                    """,
                ),
                {
                    "usage_id": usage_id,
                    "status": status,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "error_code": error_code,
                },
            )

    def start_for_memory_job(
        self,
        memory_job_id: UUID,
        operation_kind: str,
        requested_profile_id: UUID | None,
        resolved_profile: ModelProfileRecord,
    ) -> UUID:
        if operation_kind != "career_memory_extraction":
            raise ValueError("长期记忆任务只允许使用抽取操作类型")
        usage_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_usage (
                      id, memory_job_id, operation_kind, requested_profile_id,
                      resolved_profile_id, resolved_provider_key,
                      resolved_model_id, status
                    ) VALUES (
                      :id, :memory_job_id, :operation_kind, :requested_profile_id,
                      :resolved_profile_id, :resolved_provider_key,
                      :resolved_model_id, 'started'
                    )
                    ON CONFLICT (memory_job_id, operation_kind)
                      WHERE memory_job_id IS NOT NULL DO UPDATE
                    SET memory_job_id = EXCLUDED.memory_job_id
                    RETURNING id
                    """,
                ),
                {
                    "id": usage_id,
                    "memory_job_id": memory_job_id,
                    "operation_kind": operation_kind,
                    "requested_profile_id": requested_profile_id,
                    "resolved_profile_id": resolved_profile.id,
                    "resolved_provider_key": resolved_profile.provider_key,
                    "resolved_model_id": resolved_profile.model_id,
                },
            ).mappings().one()
        return row["id"]
