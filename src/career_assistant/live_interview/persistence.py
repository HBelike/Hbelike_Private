"""实时面试会话、final 话语和答案的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.live_interview.contracts import (
    AnswerStatus,
    AudioChannel,
    LiveInterviewStatus,
    QuestionIntent,
    TranscriptEvent,
)
from src.career_assistant.persistence.database import CareerDatabase


@dataclass(frozen=True)
class LiveInterviewSessionRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    candidate_profile_id: UUID | None
    target_role_profile_id: UUID | None
    interview_experience_ids: tuple[UUID, ...]
    asr_provider: str
    asr_model_profile_id: UUID | None
    answer_model_profile_id: UUID | None
    client_kind: str
    candidate_audio_enabled: bool
    status: LiveInterviewStatus
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LiveInterviewResumeState:
    """恢复游标、最近转写和一份有长度限制的上一题建议。"""

    recent_conversation: tuple[str, ...] = ()
    last_interviewer_final: str | None = None
    last_question: str | None = None
    last_question_intent: QuestionIntent = QuestionIntent.KNOWLEDGE
    last_answer_attempt: int = 0
    max_question_version: int = 0
    last_question_version: int | None = None
    max_sequence: dict[AudioChannel, int] = field(default_factory=dict)
    pending_answers: tuple["LiveInterviewQueuedAnswerState", ...] = ()
    previous_answer: tuple[str, str] | None = None


@dataclass(frozen=True)
class LiveInterviewQueuedAnswerState:
    question_version: int
    attempt: int
    question: str
    intent: QuestionIntent
    recent_conversation: tuple[str, ...]
    queued_at: datetime
    source_sequence: int | None = None
    queue_order: int = 0
    manual: bool = False
    emit_detected: bool = True
    previous_question: str | None = None


class LiveInterviewRepository:
    """所有会话读取均同时限制 organization_id 和 actor_id。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create_session(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        candidate_profile_id: UUID | None,
        target_role_profile_id: UUID | None,
        interview_experience_ids: tuple[UUID, ...] = (),
        asr_provider: str = "openai",
        asr_model_profile_id: UUID | None = None,
        answer_model_profile_id: UUID | None = None,
        client_kind: str = "desktop",
        candidate_audio_enabled: bool = True,
    ) -> LiveInterviewSessionRecord:
        session_id = uuid4()
        params = {
            "id": session_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "candidate_profile_id": candidate_profile_id,
            "target_role_profile_id": target_role_profile_id,
            "experience_ids": json.dumps([str(item) for item in interview_experience_ids]),
            "asr_provider": asr_provider.strip() or "openai",
            "asr_model_profile_id": asr_model_profile_id,
            "answer_model_profile_id": answer_model_profile_id,
            "client_kind": client_kind,
            "candidate_audio_enabled": candidate_audio_enabled,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_sessions
                        (id, organization_id, actor_id, candidate_profile_id,
                         target_role_profile_id, interview_experience_ids,
                         asr_provider, asr_model_profile_id, answer_model_profile_id,
                         client_kind, candidate_audio_enabled)
                    SELECT
                        :id, :organization_id, :actor_id, :candidate_profile_id,
                        :target_role_profile_id, CAST(:experience_ids AS JSONB),
                        :asr_provider, :asr_model_profile_id, :answer_model_profile_id,
                        :client_kind, :candidate_audio_enabled
                    WHERE
                        (CAST(:candidate_profile_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.candidate_profiles
                            WHERE id = CAST(:candidate_profile_id AS UUID)
                              AND organization_id = :organization_id
                              AND actor_id = :actor_id
                        ))
                        AND
                        (CAST(:target_role_profile_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.target_role_profiles
                            WHERE id = CAST(:target_role_profile_id AS UUID)
                              AND organization_id = :organization_id
                              AND actor_id = :actor_id
                        ))
                    RETURNING *
                    """
                ),
                params,
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("简历或目标岗位不存在，或无权访问")
        return _session(row)

    def get_session(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> LiveInterviewSessionRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.live_interview_sessions
                    WHERE id = :session_id
                      AND organization_id = :organization_id
                      AND actor_id = :actor_id
                    """
                ),
                {
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                },
            ).mappings().one_or_none()
        return _session(row) if row is not None else None

    def activate(self, organization_id: UUID, actor_id: UUID, session_id: UUID) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.live_interview_sessions
                    SET status = 'active', started_at = COALESCE(started_at, NOW()),
                        ended_at = NULL, updated_at = NOW()
                    WHERE id = :session_id
                      AND organization_id = :organization_id
                      AND actor_id = :actor_id
                      AND status IN ('preparing', 'failed')
                    """
                ),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            )
        return bool(result.rowcount)

    def resume_state(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> LiveInterviewResumeState | None:
        if self.get_session(organization_id, actor_id, session_id) is None:
            return None
        params = {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id}
        with self._database.transaction() as connection:
            # 每条查询保留归属条件；历史正文只取最近四条，避免长面试随时间膨胀。
            recent = connection.execute(
                text(
                    """
                    SELECT utterance.role, COALESCE(utterance.corrected_text, utterance.raw_text) AS text
                    FROM career_assistant.live_interview_utterances AS utterance
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = utterance.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ORDER BY utterance.created_at DESC, utterance.channel_sequence DESC
                    LIMIT 4
                    """
                ), params,
            ).mappings().all()
            interviewer = connection.execute(
                text(
                    """
                    SELECT COALESCE(utterance.corrected_text, utterance.raw_text) AS text
                    FROM career_assistant.live_interview_utterances AS utterance
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = utterance.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id AND utterance.role = 'interviewer'
                    ORDER BY utterance.created_at DESC, utterance.channel_sequence DESC LIMIT 1
                    """
                ), params,
            ).mappings().one_or_none()
            question = connection.execute(
                text(
                    """
                    SELECT answer.original_question, answer.intent, answer.question_version, answer.attempt
                    FROM career_assistant.live_interview_answers AS answer
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = answer.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                      AND NOT answer.evidence_json @> '[{"type":"live_answer_queue","phase":"queued"}]'::jsonb
                      AND NOT answer.evidence_json @> '[{"type":"live_answer_queue","phase":"cancelled"}]'::jsonb
                    ORDER BY answer.updated_at DESC, answer.question_version DESC, answer.attempt DESC LIMIT 1
                    """
                ), params,
            ).mappings().one_or_none()
            sequences = connection.execute(
                text(
                    """
                    SELECT utterance.role, MAX(utterance.channel_sequence) AS max_sequence
                    FROM career_assistant.live_interview_utterances AS utterance
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = utterance.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id GROUP BY utterance.role
                    """
                ), params,
            ).mappings().all()
            max_version = connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(answer.question_version), 0)
                    FROM career_assistant.live_interview_answers AS answer
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = answer.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    """
                ), params,
            ).scalar_one()
            completed = connection.execute(text("""
                SELECT answer.original_question, LEFT(answer.answer_text, 800) AS suggestion
                FROM career_assistant.live_interview_answers AS answer
                JOIN career_assistant.live_interview_sessions AS session ON session.id = answer.session_id
                WHERE session.id = :session_id AND session.organization_id = :organization_id
                  AND session.actor_id = :actor_id AND answer.status = 'completed'
                ORDER BY answer.updated_at DESC, answer.question_version DESC, answer.attempt DESC LIMIT 1
            """), params).mappings().one_or_none()
            pending = connection.execute(
                text(
                    """
                    SELECT answer.* FROM career_assistant.live_interview_answers AS answer
                    JOIN career_assistant.live_interview_sessions AS session ON session.id = answer.session_id
                    WHERE session.id = :session_id AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                      AND answer.evidence_json @> '[{"type":"live_answer_queue","phase":"queued"}]'::jsonb
                    ORDER BY answer.question_version, answer.attempt
                    """
                ), params,
            ).mappings().all()
        queued_answers = []
        for row in pending:
            metadata = next(item for item in row["evidence_json"] if (
                item.get("type") == "live_answer_queue" and item.get("phase") == "queued"
            ))
            queued_answers.append(LiveInterviewQueuedAnswerState(
                question_version=int(row["question_version"]),
                attempt=int(row["attempt"]),
                question=str(row["original_question"]),
                intent=QuestionIntent(str(row["intent"])),
                recent_conversation=tuple(str(item) for item in metadata["recent_conversation"][-4:]),
                queued_at=datetime.fromisoformat(metadata["queued_at"]),
                source_sequence=metadata.get("source_sequence"),
                queue_order=int(metadata.get("queue_order", row["question_version"])),
                manual=bool(metadata.get("manual", False)),
                emit_detected=bool(metadata.get("emit_detected", True)),
                previous_question=metadata.get("previous_question"),
            ))
        queued_answers.sort(key=lambda item: (item.queue_order, item.question_version, item.attempt))
        return LiveInterviewResumeState(
            recent_conversation=tuple(f"{row['role']}: {row['text']}" for row in reversed(recent)),
            last_interviewer_final=str(interviewer["text"]) if interviewer else None,
            last_question=str(question["original_question"]) if question else None,
            last_question_intent=QuestionIntent(str(question["intent"])) if question else QuestionIntent.KNOWLEDGE,
            last_answer_attempt=int(question["attempt"]) if question else 0,
            max_question_version=int(max_version),
            last_question_version=int(question["question_version"]) if question else 0,
            max_sequence={AudioChannel(str(row["role"])): int(row["max_sequence"]) for row in sequences},
            pending_answers=tuple(queued_answers),
            previous_answer=(completed["original_question"], completed["suggestion"]) if completed else None,
        )

    def save_queued_answer(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        queued: LiveInterviewQueuedAnswerState,
        phase: str,
    ) -> None:
        if phase not in {"queued", "started", "cancelled"}:
            raise ValueError("排队状态无效")
        metadata = {
            "type": "live_answer_queue", "phase": phase,
            "recent_conversation": list(queued.recent_conversation[-4:]),
            "queued_at": queued.queued_at.isoformat(), "source_sequence": queued.source_sequence,
            "queue_order": queued.queue_order, "manual": queued.manual, "emit_detected": queued.emit_detected,
            "previous_question": queued.previous_question,
        }
        self.upsert_answer(
            organization_id, actor_id, session_id,
            question_version=queued.question_version, attempt=queued.attempt,
            question=queued.question, intent=queued.intent,
            status=AnswerStatus.CANCELLED if phase == "cancelled" else AnswerStatus.GENERATING,
            evidence=() if phase == "started" else (metadata,),
        )

    def pause(self, organization_id: UUID, actor_id: UUID, session_id: UUID) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text("""
                    UPDATE career_assistant.live_interview_sessions
                    SET status = 'preparing', ended_at = NULL, updated_at = NOW()
                    WHERE id = :session_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status = 'active'
                """),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            )
        return bool(result.rowcount)

    def finish_inactive(self, organization_id: UUID, actor_id: UUID, session_id: UUID) -> bool:
        """仅结束已经离线的会话；与并行重新连接通过同一行的条件更新互斥。"""
        with self._database.transaction() as connection:
            result = connection.execute(
                text("""
                    UPDATE career_assistant.live_interview_sessions
                    SET status = 'completed', ended_at = COALESCE(ended_at, NOW()), updated_at = NOW()
                    WHERE id = :session_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status IN ('preparing', 'failed', 'completed')
                """),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            )
            if result.rowcount:
                # 离线用户主动结束后，尚未启动的持久化队列不能继续展示为生成中。
                connection.execute(text("""
                    UPDATE career_assistant.live_interview_answers
                    SET status = 'cancelled', updated_at = NOW(), evidence_json = CASE
                        WHEN evidence_json->0->>'type' = 'live_answer_queue'
                        THEN jsonb_set(evidence_json, '{0,phase}', '"cancelled"'::jsonb)
                        ELSE evidence_json END
                    WHERE session_id = :session_id AND status = 'generating'
                """), {"session_id": session_id})
        return bool(result.rowcount)

    def end(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        *,
        failed: bool = False,
    ) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.live_interview_sessions
                    SET status = :status, ended_at = COALESCE(ended_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :session_id
                      AND organization_id = :organization_id
                      AND actor_id = :actor_id
                      AND status NOT IN ('completed', 'failed')
                    """
                ),
                {
                    "status": LiveInterviewStatus.FAILED.value if failed else LiveInterviewStatus.COMPLETED.value,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                },
            )
        return bool(result.rowcount)

    def append_final_utterance(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        event: TranscriptEvent,
        *,
        corrected_text: str | None = None,
    ) -> UUID:
        if not event.is_final:
            raise ValueError("只允许持久化 final 转写")
        utterance_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_utterances AS stored
                        (id, session_id, role, channel_sequence, raw_text,
                         corrected_text, provider, confidence)
                    SELECT :id, session.id, :role, :sequence, :raw_text,
                           :corrected_text, :provider, :confidence
                    FROM career_assistant.live_interview_sessions AS session
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ON CONFLICT (session_id, role, channel_sequence)
                    DO UPDATE SET raw_text = stored.raw_text WHERE stored.raw_text = excluded.raw_text
                    RETURNING id
                    """
                ),
                {
                    "id": utterance_id,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "role": event.role.value,
                    "sequence": event.sequence,
                    "raw_text": event.text,
                    "corrected_text": corrected_text if corrected_text != event.text else None,
                    "provider": event.provider,
                    "confidence": event.confidence,
                },
            ).scalar_one_or_none()
        if row is None:
            raise LookupError("会话不存在、无权访问或同一转写编号已保存不同内容")
        return UUID(str(row))

    def upsert_answer(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        *,
        question_version: int,
        attempt: int,
        question: str,
        intent: QuestionIntent,
        status: AnswerStatus,
        answer_text: str = "",
        evidence: tuple[dict[str, Any], ...] = (),
        error_code: str | None = None,
    ) -> UUID:
        answer_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_answers
                        (id, session_id, question_version, attempt, original_question,
                         normalized_question, intent, status, answer_text,
                         evidence_json, error_code)
                    SELECT :id, session.id, :question_version, :attempt, :question,
                           :question, :intent, :status, :answer_text,
                           CAST(:evidence AS JSONB), :error_code
                    FROM career_assistant.live_interview_sessions AS session
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ON CONFLICT (session_id, question_version, attempt)
                    DO UPDATE SET status = EXCLUDED.status,
                                  answer_text = EXCLUDED.answer_text,
                                  evidence_json = EXCLUDED.evidence_json,
                                  error_code = EXCLUDED.error_code,
                                  updated_at = NOW()
                    RETURNING id
                    """
                ),
                {
                    "id": answer_id,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "question_version": question_version,
                    "attempt": attempt,
                    "question": question.strip(),
                    "intent": intent.value,
                    "status": status.value,
                    "answer_text": answer_text,
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                    "error_code": error_code,
                },
            ).scalar_one_or_none()
        if row is None:
            raise LookupError("会话不存在或无权访问")
        return UUID(str(row))

    def history(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> dict[str, object] | None:
        session = self.get_session(organization_id, actor_id, session_id)
        if session is None:
            return None
        with self._database.transaction() as connection:
            utterances = connection.execute(
                text(
                    """
                    SELECT utterance.*
                    FROM career_assistant.live_interview_utterances AS utterance
                    JOIN career_assistant.live_interview_sessions AS session
                      ON session.id = utterance.session_id
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ORDER BY utterance.created_at, utterance.channel_sequence
                    """
                ),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
            answers = connection.execute(
                text(
                    """
                    SELECT answer.*
                    FROM career_assistant.live_interview_answers AS answer
                    JOIN career_assistant.live_interview_sessions AS session
                      ON session.id = answer.session_id
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ORDER BY answer.question_version, answer.attempt
                    """
                ),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
        return {
            "session": _session_payload(session),
            "utterances": [dict(row) for row in utterances],
            "answers": [dict(row) for row in answers],
        }


def _session(row: RowMapping) -> LiveInterviewSessionRecord:
    return LiveInterviewSessionRecord(
        id=UUID(str(row["id"])),
        organization_id=UUID(str(row["organization_id"])),
        actor_id=UUID(str(row["actor_id"])),
        candidate_profile_id=UUID(str(row["candidate_profile_id"])) if row["candidate_profile_id"] else None,
        target_role_profile_id=UUID(str(row["target_role_profile_id"])) if row["target_role_profile_id"] else None,
        interview_experience_ids=tuple(UUID(str(item)) for item in row["interview_experience_ids"]),
        asr_provider=str(row["asr_provider"]),
        asr_model_profile_id=UUID(str(row["asr_model_profile_id"])) if row["asr_model_profile_id"] else None,
        answer_model_profile_id=UUID(str(row["answer_model_profile_id"])) if row["answer_model_profile_id"] else None,
        client_kind=str(row["client_kind"]),
        candidate_audio_enabled=bool(row["candidate_audio_enabled"]),
        status=LiveInterviewStatus(str(row["status"])),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _session_payload(record: LiveInterviewSessionRecord) -> dict[str, object]:
    return {
        "id": str(record.id),
        "status": record.status.value,
        "candidate_profile_id": str(record.candidate_profile_id) if record.candidate_profile_id else None,
        "target_role_profile_id": str(record.target_role_profile_id) if record.target_role_profile_id else None,
        "interview_experience_ids": [str(item) for item in record.interview_experience_ids],
        "asr_provider": record.asr_provider,
        "asr_model_profile_id": str(record.asr_model_profile_id) if record.asr_model_profile_id else None,
        "answer_model_profile_id": str(record.answer_model_profile_id) if record.answer_model_profile_id else None,
        "client_kind": record.client_kind,
        "candidate_audio_enabled": record.candidate_audio_enabled,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "created_at": record.created_at,
    }


session_payload = _session_payload
