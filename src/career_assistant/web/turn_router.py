"""求职助手 PostgreSQL 持久 Turn 的提交、状态、事件与取消 API。"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.career_assistant.contracts import (
    CareerInboundMessage,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.persistence.records import AgentTurnStatus
from src.career_assistant.turn_payloads import (
    collect_input_kind_codes,
    prepare_turn_payload,
)
from src.career_assistant.web.router import (
    CareerAssistantServices,
    CareerRequestActor,
    _attach_conversation_context,
    _build_interview_evidence,
    _resolve_conversation_skill_activation,
    _upload_kind,
    get_career_services,
    get_request_actor,
)


router = APIRouter(prefix="/api/career", tags=["career-turns"])
TERMINAL_STATUSES = {
    AgentTurnStatus.SUCCEEDED,
    AgentTurnStatus.FAILED,
    AgentTurnStatus.CANCELLED,
}


class EnqueueTurnRequest(BaseModel):
    """无需附件的一轮持久 Turn 提交。"""

    text: str = Field(default="", max_length=30_000)
    job_url: str | None = Field(default=None, max_length=2_000)
    selection_mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    model_profile_id: UUID | None = None
    interview_experience_ids: list[UUID] = Field(default_factory=list, max_length=5)
    selected_skill_ids: list[str] = Field(default_factory=list, max_length=3)


@router.post(
    "/conversations/{conversation_id}/turns",
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_turn(
    conversation_id: UUID,
    request_body: EnqueueTurnRequest,
    request: Request,
) -> dict[str, object]:
    """立即持久化文本或职位链接 Turn，不等待 LLM/Tool 执行。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        inbound_message = await _build_enriched_inbound(
            services,
            actor,
            conversation_id,
            text=request_body.text,
            job_url=request_body.job_url,
            selection_mode=request_body.selection_mode,
            model_profile_id=request_body.model_profile_id,
            interview_experience_ids=request_body.interview_experience_ids,
            selected_skill_ids=request_body.selected_skill_ids,
        )
        queue_status = await _enqueue_inbound(
            services,
            inbound_message,
            parsed_attachments=(),
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"turn": _queue_status_payload(queue_status)}


@router.post(
    "/conversations/{conversation_id}/turns-with-materials",
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_turn_with_materials(
    conversation_id: UUID,
    request: Request,
    text: str = Form(default=""),
    job_url: str | None = Form(default=None),
    selection_mode: ModelSelectionMode = Form(
        default=ModelSelectionMode.FREE_QUOTA_FIRST,
    ),
    model_profile_id: UUID | None = Form(default=None),
    interview_experience_ids: list[UUID] = Form(default_factory=list),
    selected_skill_ids: list[str] = Form(default_factory=list),
    resume_file: UploadFile | None = File(default=None),
    job_description_file: UploadFile | None = File(default=None),
) -> dict[str, object]:
    """在 API 内解析附件，完整文本入库后删除原始文件并返回 202。"""

    if len(text) > 30_000:
        raise HTTPException(status_code=422, detail="咨询文本不能超过 30000 个字符")
    if job_url is not None and len(job_url) > 2_000:
        raise HTTPException(status_code=422, detail="职位链接不能超过 2000 个字符")
    if resume_file is None and job_description_file is None:
        raise HTTPException(status_code=422, detail="请至少上传一份简历或职位材料")

    actor = get_request_actor()
    services = get_career_services(request)
    attachments = []
    try:
        if resume_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    resume_file,
                    _upload_kind(resume_file, is_resume=True),
                ),
            )
        if job_description_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    job_description_file,
                    _upload_kind(job_description_file, is_resume=False),
                ),
            )
        parsed_items = []
        for attachment in attachments:
            parsed_items.append(
                await asyncio.to_thread(services.attachment_parser.parse, attachment),
            )
        parsed_attachments = tuple(parsed_items)
        inbound_message = await _build_enriched_inbound(
            services,
            actor,
            conversation_id,
            text=text,
            job_url=job_url,
            selection_mode=selection_mode,
            model_profile_id=model_profile_id,
            interview_experience_ids=interview_experience_ids,
            selected_skill_ids=selected_skill_ids,
        )
        queue_status = await _enqueue_inbound(
            services,
            inbound_message,
            parsed_attachments=parsed_attachments,
        )
    except OSError as exc:
        raise HTTPException(status_code=503, detail="附件临时存储或解析服务不可用") from exc
    except (LookupError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))

    return {
        "turn": _queue_status_payload(queue_status),
        "attachment_processing": {
            "count": len(parsed_attachments),
            "text_characters": sum(
                len(item.extracted_text) for item in parsed_attachments
            ),
            "full_text_persisted": True,
            "redacted": False,
            "original_files_deleted": True,
        },
    }


@router.get("/turns/{turn_id}")
async def get_turn_status(turn_id: UUID, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    services = get_career_services(request)
    queue_status = await asyncio.to_thread(
        services.turn_job_repository.get_turn_status,
        actor.actor_id,
        turn_id,
    )
    if queue_status is None:
        raise HTTPException(status_code=404, detail="Turn 不存在或无访问权限")
    return {"turn": _queue_status_payload(queue_status)}


@router.get("/conversations/{conversation_id}/active-turns")
async def list_active_turns(
    conversation_id: UUID,
    request: Request,
) -> dict[str, object]:
    actor = get_request_actor()
    services = get_career_services(request)
    items = await asyncio.to_thread(
        services.turn_job_repository.list_active_turns,
        actor.actor_id,
        conversation_id,
    )
    return {"items": [_queue_status_payload(item) for item in items]}


@router.get("/turns/{turn_id}/events")
async def stream_turn_events(turn_id: UUID, request: Request) -> StreamingResponse:
    """从持久事件 ID 继续 SSE；客户端断线不会取消 Worker。"""

    actor = get_request_actor()
    services = get_career_services(request)
    queue_status = await asyncio.to_thread(
        services.turn_job_repository.get_turn_status,
        actor.actor_id,
        turn_id,
    )
    if queue_status is None:
        raise HTTPException(status_code=404, detail="Turn 不存在或无访问权限")
    try:
        after_id = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Last-Event-ID 必须是整数") from exc
    if after_id < 0:
        raise HTTPException(status_code=422, detail="Last-Event-ID 不能为负数")

    return StreamingResponse(
        _persistent_event_stream(
            services,
            actor,
            turn_id,
            after_id=after_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: UUID, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    services = get_career_services(request)
    turn_status = await asyncio.to_thread(
        services.turn_job_repository.request_cancel,
        actor.actor_id,
        turn_id,
    )
    if turn_status is None:
        raise HTTPException(status_code=409, detail="Turn 不存在、无权限或已经结束")
    event_type = "cancelled" if turn_status is AgentTurnStatus.CANCELLED else "cancel_requested"
    await asyncio.to_thread(
        services.turn_job_repository.append_event,
        turn_id,
        event_type,
        {"state": turn_status.value},
    )
    return {"turn_id": str(turn_id), "status": turn_status.value}


async def _build_enriched_inbound(
    services: CareerAssistantServices,
    actor: CareerRequestActor,
    conversation_id: UUID,
    *,
    text: str,
    job_url: str | None,
    selection_mode: ModelSelectionMode,
    model_profile_id: UUID | None,
    interview_experience_ids: list[UUID],
    selected_skill_ids: list[str],
) -> CareerInboundMessage:
    interview_evidence = await asyncio.to_thread(
        _build_interview_evidence,
        services,
        actor,
        interview_experience_ids,
        text,
        conversation_id=conversation_id,
    )
    skill_activation = await asyncio.to_thread(
        _resolve_conversation_skill_activation,
        services,
        actor.actor_id,
        conversation_id,
        selected_skill_ids,
        text,
    )
    inbound_message = CareerInboundMessage(
        turn_id=uuid4(),
        conversation_id=conversation_id,
        actor_id=actor.actor_id,
        text=text,
        effective_text=skill_activation.user_task_text,
        job_url=job_url,
        model_selection=ModelSelectionRequest(
            mode=selection_mode,
            profile_id=model_profile_id,
        ),
        interview_evidence=interview_evidence,
        activated_skills=skill_activation.skills,
    )
    return await asyncio.to_thread(
        _attach_conversation_context,
        services,
        inbound_message,
    )


async def _enqueue_inbound(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    parsed_attachments,
):
    payload = prepare_turn_payload(
        inbound_message,
        parsed_attachments,
        source="career_api",
    )
    input_kind_codes = collect_input_kind_codes(
        inbound_message,
        parsed_attachments,
    )
    if not input_kind_codes:
        raise ValueError("至少需要提供文本、职位链接或附件中的一种输入")
    await asyncio.to_thread(
        services.turn_job_repository.enqueue,
        turn_id=inbound_message.turn_id,
        conversation_id=inbound_message.conversation_id,
        actor_id=inbound_message.actor_id,
        model_selection=inbound_message.model_selection,
        input_kind_codes=input_kind_codes,
        payload=payload,
    )
    queue_status = await asyncio.to_thread(
        services.turn_job_repository.get_turn_status,
        inbound_message.actor_id,
        inbound_message.turn_id,
    )
    if queue_status is None:
        raise RuntimeError("Turn 已入队但无法读取状态")
    return queue_status


async def _persistent_event_stream(
    services: CareerAssistantServices,
    actor: CareerRequestActor,
    turn_id: UUID,
    *,
    after_id: int,
) -> AsyncIterator[str]:
    poll_seconds = max(
        0.05,
        float(os.getenv("CAREER_AGENT_EVENT_POLL_MILLISECONDS", "300")) / 1000,
    )
    cursor = after_id
    while True:
        events = await asyncio.to_thread(
            services.turn_job_repository.list_events,
            actor.actor_id,
            turn_id,
            after_id=cursor,
        )
        for event in events:
            cursor = event.id
            yield _sse_event(event.event_type, event.payload, event_id=event.id)

        queue_status = await asyncio.to_thread(
            services.turn_job_repository.get_turn_status,
            actor.actor_id,
            turn_id,
        )
        if queue_status is None:
            yield _sse_event("error", {"detail": "Turn 已不存在"})
            return
        if queue_status.turn.status in TERMINAL_STATUSES and not events:
            return
        if not events:
            yield ": heartbeat\n\n"
        await asyncio.sleep(poll_seconds)


def _queue_status_payload(queue_status) -> dict[str, object]:
    turn = queue_status.turn
    return {
        "id": str(turn.id),
        "conversation_id": str(turn.conversation_id),
        "status": turn.status.value,
        "input_kind_codes": list(turn.input_kind_codes),
        "requested_selection_mode": turn.requested_selection_mode.value,
        "queue_sequence": queue_status.queue_sequence,
        "conversation_position": queue_status.conversation_position,
        "attempt_count": queue_status.attempt_count,
        "cancel_requested": queue_status.cancel_requested_at is not None,
        "error_code": turn.error_code,
        "error_message": turn.error_message,
        "created_at": turn.created_at.isoformat(),
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "completed_at": turn.completed_at.isoformat() if turn.completed_at else None,
    }


def _sse_event(
    event_type: str,
    payload: dict[str, object],
    *,
    event_id: int | None = None,
) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"
