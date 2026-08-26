"""职业空间与长期求职记忆的 PostgreSQL 作用域仓储。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.career_memory import (
    CareerMemoryDraft,
    CareerMemoryStatus,
    CareerMemoryType,
)
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.records import CareerMemoryItemRecord, CareerSpaceRecord


class CareerMemoryRepository:
    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def ensure_default_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
    ) -> CareerSpaceRecord:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.career_spaces
                      (id, organization_id, actor_id, name, normalized_name, is_default)
                    VALUES (:id, :organization_id, :actor_id, '默认求职方向', '默认求职方向', TRUE)
                    ON CONFLICT (organization_id, actor_id, normalized_name) DO UPDATE
                    SET updated_at = career_assistant.career_spaces.updated_at
                    RETURNING id, organization_id, actor_id, name, normalized_name,
                              is_default, created_at, updated_at
                    """,
                ),
                {"id": uuid4(), "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one()
        return self._space_record(row)

    def create_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
        name: str,
    ) -> CareerSpaceRecord:
        normalized_name = " ".join(name.split())
        if not normalized_name or len(normalized_name) > 120:
            raise ValueError("职业空间名称长度必须在 1 到 120 字符之间")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.career_spaces
                      (id, organization_id, actor_id, name, normalized_name, is_default)
                    VALUES (:id, :organization_id, :actor_id, :name, :normalized_name, FALSE)
                    RETURNING id, organization_id, actor_id, name, normalized_name,
                              is_default, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "name": normalized_name,
                    "normalized_name": normalized_name.casefold(),
                },
            ).mappings().one()
        return self._space_record(row)

    def list_spaces(self, organization_id: UUID, actor_id: UUID) -> tuple[CareerSpaceRecord, ...]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, actor_id, name, normalized_name,
                           is_default, created_at, updated_at
                    FROM career_assistant.career_spaces
                    WHERE organization_id = :organization_id AND actor_id = :actor_id
                    ORDER BY is_default DESC, updated_at DESC
                    """,
                ),
                {"organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
        return tuple(self._space_record(row) for row in rows)

    def bind_conversation_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        career_space_id: UUID,
    ) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversations AS conversation
                    SET career_space_id = space.id, updated_at = NOW()
                    FROM career_assistant.career_spaces AS space
                    WHERE conversation.id = :conversation_id
                      AND conversation.organization_id = :organization_id
                      AND conversation.actor_id = :actor_id
                      AND space.id = :career_space_id
                      AND space.organization_id = :organization_id
                      AND space.actor_id = :actor_id
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "career_space_id": career_space_id,
                },
            )
        return result.rowcount == 1

    def create_memory(
        self,
        organization_id: UUID,
        actor_id: UUID,
        draft: CareerMemoryDraft,
        status: CareerMemoryStatus = CareerMemoryStatus.CANDIDATE,
    ) -> CareerMemoryItemRecord:
        normalized = draft.validate()
        with self._database.transaction() as connection:
            row = self._insert_memory(connection, organization_id, actor_id, normalized, status)
        return self._memory_record(row)

    def supersede_active(
        self,
        organization_id: UUID,
        actor_id: UUID,
        old_id: UUID,
        replacement: CareerMemoryDraft,
    ) -> CareerMemoryItemRecord:
        normalized = replacement.validate()
        with self._database.transaction() as connection:
            old = connection.execute(
                text(
                    """
                    SELECT id FROM career_assistant.career_memory_items
                    WHERE id = :old_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status = 'active'
                    FOR UPDATE
                    """,
                ),
                {"old_id": old_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
            if old is None:
                raise LookupError("求职记忆不存在或已经失效")
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.career_memory_items
                    SET status = 'superseded', valid_to = NOW(), updated_at = NOW()
                    WHERE id = :old_id AND organization_id = :organization_id AND actor_id = :actor_id
                    """,
                ),
                {"old_id": old_id, "organization_id": organization_id, "actor_id": actor_id},
            )
            row = self._insert_memory(
                connection,
                organization_id,
                actor_id,
                normalized,
                CareerMemoryStatus.ACTIVE,
                supersedes_memory_id=old_id,
            )
        return self._memory_record(row)

    def list_active_for_prompt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        career_space_id: UUID,
        *,
        memory_types: Sequence[CareerMemoryType],
        candidate_profile_id: UUID | None = None,
        candidate_profile_version: int | None = None,
        query: str = "",
        limit: int = 5,
    ) -> tuple[CareerMemoryItemRecord, ...]:
        del query
        if limit < 1 or limit > 50:
            raise ValueError("求职记忆读取数量无效")
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.career_memory_items
                    WHERE organization_id = :organization_id
                      AND actor_id = :actor_id
                      AND status = 'active'
                      AND memory_type = ANY(:memory_types)
                      AND (career_space_id = :career_space_id OR career_space_id IS NULL)
                      AND (:candidate_profile_id IS NULL OR candidate_profile_id IS NULL
                           OR (candidate_profile_id = :candidate_profile_id
                               AND candidate_profile_version = :candidate_profile_version))
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "career_space_id": career_space_id,
                    "memory_types": [item.value for item in memory_types],
                    "candidate_profile_id": candidate_profile_id,
                    "candidate_profile_version": candidate_profile_version,
                    "limit": limit,
                },
            ).mappings().all()
        return tuple(self._memory_record(row) for row in rows)

    @staticmethod
    def _insert_memory(
        connection,
        organization_id: UUID,
        actor_id: UUID,
        draft: CareerMemoryDraft,
        status: CareerMemoryStatus,
        *,
        supersedes_memory_id: UUID | None = None,
    ) -> RowMapping:
        return connection.execute(
            text(
                """
                INSERT INTO career_assistant.career_memory_items (
                  id, organization_id, actor_id, career_space_id, memory_type,
                  normalized_value_json, display_text, source_kind, source_message_id,
                  source_conversation_id, candidate_profile_id, candidate_profile_version,
                  status, supersedes_memory_id
                ) VALUES (
                  :id, :organization_id, :actor_id, :career_space_id, :memory_type,
                  CAST(:normalized_value_json AS JSONB), :display_text, :source_kind, :source_message_id,
                  :source_conversation_id, :candidate_profile_id, :candidate_profile_version,
                  :status, :supersedes_memory_id
                ) RETURNING *
                """,
            ),
            {
                "id": uuid4(),
                "organization_id": organization_id,
                "actor_id": actor_id,
                "career_space_id": draft.career_space_id,
                "memory_type": draft.memory_type.value,
                "normalized_value_json": json.dumps(draft.normalized_value, ensure_ascii=False),
                "display_text": draft.display_text,
                "source_kind": draft.source_kind,
                "source_message_id": draft.source_message_id,
                "source_conversation_id": draft.source_conversation_id,
                "candidate_profile_id": draft.candidate_profile_id,
                "candidate_profile_version": draft.candidate_profile_version,
                "status": status.value,
                "supersedes_memory_id": supersedes_memory_id,
            },
        ).mappings().one()

    @staticmethod
    def _space_record(row: RowMapping) -> CareerSpaceRecord:
        return CareerSpaceRecord(**{key: row[key] for key in CareerSpaceRecord.__dataclass_fields__})

    @staticmethod
    def _memory_record(row: RowMapping) -> CareerMemoryItemRecord:
        normalized = row["normalized_value_json"]
        if isinstance(normalized, str):
            normalized = json.loads(normalized)
        return CareerMemoryItemRecord(
            id=row["id"], organization_id=row["organization_id"], actor_id=row["actor_id"],
            career_space_id=row["career_space_id"], memory_type=row["memory_type"],
            normalized_value=dict(normalized), display_text=row["display_text"],
            source_kind=row["source_kind"], source_message_id=row["source_message_id"],
            source_conversation_id=row["source_conversation_id"],
            candidate_profile_id=row["candidate_profile_id"],
            candidate_profile_version=row["candidate_profile_version"], status=row["status"],
            supersedes_memory_id=row["supersedes_memory_id"], valid_from=row["valid_from"],
            valid_to=row["valid_to"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
