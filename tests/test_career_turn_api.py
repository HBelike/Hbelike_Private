"""PostgreSQL 持久 Turn API 的入队和 SSE 恢复测试。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.attachments import ParsedAttachment
from src.career_assistant.contracts import (
    AttachmentKind,
    CareerInboundMessage,
    ModelSelectionMode,
)
from src.career_assistant.persistence.records import AgentTurnRecord, AgentTurnStatus
from src.career_assistant.persistence.turn_job_repository import (
    TurnEventRecord,
    TurnQueueStatusRecord,
)


def _queue_status(*, actor_id, conversation_id, turn_id, status="queued"):
    now = datetime.now(UTC)
    turn = AgentTurnRecord(
        id=turn_id,
        conversation_id=conversation_id,
        actor_id=actor_id,
        requested_selection_mode=ModelSelectionMode.FREE_QUOTA_FIRST,
        requested_model_profile_id=None,
        input_kind_codes=("text",),
        status=AgentTurnStatus(status),
        error_code=None,
        error_message=None,
        started_at=now if status != "queued" else None,
        completed_at=now if status in {"succeeded", "failed", "cancelled"} else None,
        created_at=now,
        updated_at=now,
    )
    return TurnQueueStatusRecord(
        turn=turn,
        queue_sequence=5,
        attempt_count=0,
        cancel_requested_at=None,
        conversation_position=1,
    )


class FakeTurnJobRepository:
    def __init__(self, queue_status) -> None:
        self.queue_status = queue_status
        self.enqueued = None
        self.events = []

    def enqueue(self, **kwargs):
        self.enqueued = kwargs
        return self.queue_status.turn

    def get_turn_status(self, actor_id, turn_id):
        if actor_id == self.queue_status.turn.actor_id and turn_id == self.queue_status.turn.id:
            return self.queue_status
        return None

    def list_events(self, actor_id, turn_id, *, after_id=0, limit=200):
        return tuple(event for event in self.events if event.id > after_id)


class CareerTurnApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_inbound_persists_complete_attachment_text(self) -> None:
        from src.career_assistant.web.turn_router import _enqueue_inbound

        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        status_record = _queue_status(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        repository = FakeTurnJobRepository(status_record)
        services = SimpleNamespace(turn_job_repository=repository)
        inbound = CareerInboundMessage(
            turn_id=turn_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            text="请分析完整经历",
        )
        full_text = "张三在某公司负责支付平台，手机号 13800138000"
        parsed = ParsedAttachment(
            kind=AttachmentKind.RESUME_PDF,
            media_type="application/pdf",
            page_count=2,
            image_width=None,
            image_height=None,
            extracted_text=full_text,
            requires_vision_model=False,
            image_bytes=None,
        )

        result = await _enqueue_inbound(
            services,
            inbound,
            parsed_attachments=(parsed,),
        )

        self.assertEqual(result.queue_sequence, 5)
        payload = repository.enqueued["payload"]
        self.assertEqual(payload.attachment_payloads[0]["extracted_text"], full_text)
        self.assertIn("13800138000", payload.attachment_payloads[0]["extracted_text"])
        self.assertIn("resume_pdf", repository.enqueued["input_kind_codes"])

    async def test_persistent_stream_resumes_after_last_event_id(self) -> None:
        from src.career_assistant.web.turn_router import _persistent_event_stream

        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        status_record = _queue_status(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            status="succeeded",
        )
        repository = FakeTurnJobRepository(status_record)
        repository.events = [
            TurnEventRecord(
                id=11,
                turn_id=turn_id,
                event_type="done",
                payload={"state": "succeeded"},
                created_at=datetime.now(UTC),
            ),
        ]
        services = SimpleNamespace(turn_job_repository=repository)
        actor = SimpleNamespace(actor_id=actor_id)

        chunks = [
            chunk
            async for chunk in _persistent_event_stream(
                services,
                actor,
                turn_id,
                after_id=10,
            )
        ]

        self.assertEqual(len(chunks), 1)
        self.assertIn("id: 11", chunks[0])
        self.assertIn("event: done", chunks[0])


class CareerTurnApiRouteTests(unittest.TestCase):
    def test_text_turn_endpoint_returns_202_and_server_queue_status(self) -> None:
        from src.career_assistant.web import turn_router

        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        status_record = _queue_status(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        repository = FakeTurnJobRepository(status_record)
        services = SimpleNamespace(turn_job_repository=repository)
        inbound = CareerInboundMessage(
            turn_id=turn_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            text="继续优化项目经历",
        )
        app = FastAPI()
        app.include_router(turn_router.router)

        with (
            patch.object(turn_router, "get_career_services", return_value=services),
            patch.object(
                turn_router,
                "get_request_actor",
                return_value=SimpleNamespace(actor_id=actor_id),
            ),
            patch.object(
                turn_router,
                "_build_enriched_inbound",
                new=AsyncMock(return_value=inbound),
            ),
        ):
            response = TestClient(app).post(
                f"/api/career/conversations/{conversation_id}/turns",
                json={"text": "继续优化项目经历"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["turn"]["status"], "queued")
        self.assertEqual(response.json()["turn"]["conversation_position"], 1)


if __name__ == "__main__":
    unittest.main()
