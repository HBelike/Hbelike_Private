"""Windows 实时面试助手的 REST 配置接口与业务 WebSocket。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from src.career_assistant.contracts import ModelCapability, ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.live_interview.answer_service import LiveAnswerService
from src.career_assistant.live_interview.asr.fake import FakeAsrProvider
from src.career_assistant.live_interview.asr.openai_realtime import OpenAIRealtimeAsrProvider
from src.career_assistant.live_interview.context_builder import LiveAnswerContext
from src.career_assistant.live_interview.desktop_launcher import (
    DesktopLauncherError,
    launch_windows_desktop_assistant,
)
from src.career_assistant.live_interview.contracts import (
    AnswerStatus,
    AudioChannel,
    SessionEndEvent,
    TranscriptEvent,
    parse_client_event,
)
from src.career_assistant.live_interview.persistence import (
    LiveInterviewRepository,
    LiveInterviewSessionRecord,
    session_payload,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.live_interview.terminology import TerminologyCorrector, extract_terms
from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.model_gateway import ModelReadiness
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)
from src.platform_access.web import (
    SESSION_COOKIE_NAME,
    get_platform_access_service,
    platform_auth_required,
)


router = APIRouter(prefix="/api/career/live-interviews", tags=["live-interview"])


class CreateLiveInterviewRequest(BaseModel):
    candidate_profile_id: UUID | None = None
    target_role_profile_id: UUID | None = None
    interview_experience_ids: list[UUID] = Field(default_factory=list, max_length=5)
    asr_model_profile_id: UUID | None = None
    answer_model_profile_id: UUID | None = None


def get_live_actor():
    from src.career_assistant.web.router import get_request_actor

    return get_request_actor()


@router.post("/desktop/launch")
def launch_desktop(_actor=Depends(get_live_actor)) -> dict[str, str]:
    """从求职助手页面启动本机 Windows 双路音频采集器。"""

    try:
        result = launch_windows_desktop_assistant()
    except DesktopLauncherError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": result.status, "message": result.message}


def get_live_read_services(request: Request):
    from src.career_assistant.web.router import get_career_read_services

    return get_career_read_services(request)


def get_live_repository(request: Request) -> LiveInterviewRepository:
    return LiveInterviewRepository(get_live_read_services(request).database)


@router.get("/setup-options")
def setup_options(request: Request, actor=Depends(get_live_actor)) -> dict[str, object]:
    services = get_live_read_services(request)
    candidates = services.context_repository.list_candidate_profiles(actor.actor_id)
    targets = services.context_repository.list_target_roles(actor.actor_id)
    models = services.model_gateway.list_availability(actor.organization_id)
    return {
        "candidate_profiles": [
            {
                "id": str(item.id),
                "display_name": item.display_name,
                "source_filename": item.source_filename,
                "version": item.version,
            }
            for item in candidates
        ],
        "target_roles": [
            {
                "id": str(item.id),
                "company_name": item.company_name,
                "role_name": item.role_name,
                "version": item.version,
            }
            for item in targets
        ],
        "asr_models": [
            _model_payload(item)
            for item in models
            if ModelCapability.TRANSCRIPTION in item.profile.capabilities
        ],
        "answer_models": [
            _model_payload(item)
            for item in models
            if ModelCapability.TEXT in item.profile.capabilities
        ],
        "audio_policy": {
            "platform": "Windows 10/11 x64",
            "raw_audio_persisted": False,
            "notice": "仅在参与者已知情并允许使用 AI 的场景开始采集。",
        },
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateLiveInterviewRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    repository = get_live_repository(request)
    try:
        record = repository.create_session(
            actor.organization_id,
            actor.actor_id,
            candidate_profile_id=payload.candidate_profile_id,
            target_role_profile_id=payload.target_role_profile_id,
            interview_experience_ids=tuple(payload.interview_experience_ids),
            asr_provider=(
                "fake"
                if os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1"
                else "openai"
            ),
            asr_model_profile_id=payload.asr_model_profile_id,
            answer_model_profile_id=payload.answer_model_profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"session": session_payload(record)}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: UUID,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    record = get_live_repository(request).get_session(
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="实时面试会话不存在或无权访问")
    return {"session": session_payload(record)}


@router.get("/sessions/{session_id}/history")
def get_history(
    session_id: UUID,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    payload = get_live_repository(request).history(
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="实时面试会话不存在或无权访问")
    return payload


@router.websocket("/{session_id}/stream")
async def live_interview_stream(websocket: WebSocket, session_id: UUID) -> None:
    actor = await _resolve_websocket_actor(websocket)
    if actor is None:
        return
    repository = _repository_for_websocket(websocket)
    record = await asyncio.to_thread(
        repository.get_session,
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if record is None:
        await websocket.close(code=4404, reason="实时面试会话不存在或无权访问")
        return

    connections: set[UUID] = getattr(websocket.app.state, "live_interview_connections", set())
    websocket.app.state.live_interview_connections = connections
    if session_id in connections:
        await websocket.close(code=4409, reason="该会话已有活动连接")
        return
    connections.add(session_id)
    manager: LiveSessionManager | None = None
    failed = False
    await websocket.accept()
    try:
        manager = await _build_live_manager(websocket, repository, record)
        await asyncio.to_thread(repository.activate, actor.organization_id, actor.actor_id, session_id)
        await manager.start()
        sender = asyncio.create_task(_send_manager_events(websocket, manager))
        try:
            while not manager.closed:
                payload = await websocket.receive_json()
                try:
                    event = parse_client_event(payload)
                    await manager.handle(event)
                except ValueError as exc:
                    await websocket.send_json(
                        {"type": "error", "code": "invalid_event", "message": str(exc)}
                    )
                    continue
                if isinstance(event, SessionEndEvent):
                    break
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        failed = True
        try:
            await websocket.send_json(
                {"type": "error", "code": "session_failed", "message": "实时面试会话已中止"}
            )
        except Exception:
            pass
    finally:
        if manager is not None:
            await manager.close("disconnect")
        await asyncio.to_thread(
            repository.end,
            actor.organization_id,
            actor.actor_id,
            session_id,
            failed=failed,
        )
        connections.discard(session_id)
        try:
            await websocket.close()
        except Exception:
            pass


async def _send_manager_events(websocket: WebSocket, manager: LiveSessionManager) -> None:
    while True:
        event = await manager.next_event()
        await websocket.send_json(event.to_dict())


async def _resolve_websocket_actor(websocket: WebSocket):
    from src.career_assistant.web.router import CareerRequestActor

    if not platform_auth_required():
        return CareerRequestActor(DEFAULT_ORGANIZATION_ID, DEFAULT_ACTOR_ID)
    token = websocket.cookies.get(SESSION_COOKIE_NAME, "")
    try:
        resolution = get_platform_access_service(websocket).resolve_session(token)
    except HTTPException:
        resolution = None
    if resolution is None:
        await websocket.close(code=4401, reason="请先登录后继续")
        return None
    return CareerRequestActor(resolution.user.organization_id, resolution.user.id)


def _repository_for_websocket(websocket: WebSocket) -> LiveInterviewRepository:
    from src.career_assistant.web.router import get_career_read_services

    return LiveInterviewRepository(get_career_read_services(websocket).database)


async def _build_live_manager(
    websocket: WebSocket,
    repository: LiveInterviewRepository,
    record: LiveInterviewSessionRecord,
) -> LiveSessionManager:
    from src.career_assistant.web.router import get_career_services

    services = get_career_services(websocket)
    candidate = (
        services.context_repository.get_candidate_profile(record.actor_id, record.candidate_profile_id)
        if record.candidate_profile_id
        else None
    )
    target = (
        services.context_repository.get_target_role(record.actor_id, record.target_role_profile_id)
        if record.target_role_profile_id
        else None
    )
    candidate_facts = candidate.resume_outline if candidate else ""
    target_role = target.job_text if target else ""
    terms = extract_terms(candidate_facts, target_role)
    corrector = TerminologyCorrector(terms)
    evidence: tuple[str, ...] = ()
    if record.interview_experience_ids:
        result = await asyncio.to_thread(
            services.interview_retrieval_service.retrieve,
            record.organization_id,
            target_role or candidate_facts[:500] or "面试",
            limit=5,
            experience_ids=record.interview_experience_ids,
        )
        evidence = tuple(item.chunk.contextual_content for item in result.candidates)

    asr_provider = _resolve_asr_provider(services, record)
    sessions = {}
    try:
        for channel in AudioChannel:
            sessions[channel] = await asr_provider.start(channel, prompt="、".join(terms[:100]))
    except Exception:
        await asyncio.gather(*(item.close() for item in sessions.values()), return_exceptions=True)
        raise

    answer_resolution = services.model_gateway.resolve(
        record.organization_id,
        ModelSelectionRequest(
            mode=ModelSelectionMode.SPECIFIC_PROFILE
            if record.answer_model_profile_id
            else ModelSelectionMode.FREE_QUOTA_FIRST,
            profile_id=record.answer_model_profile_id,
            required_capabilities=frozenset({ModelCapability.TEXT}),
        ),
    )
    if answer_resolution.readiness is not ModelReadiness.READY:
        raise RuntimeError("回答模型尚未配置可用凭据")

    async def prompt_streamer(prompt: str) -> AsyncIterator[str]:
        iterator = services.model_connection_client.stream_complete(
            answer_resolution.profile,
            answer_resolution.credential_env_name,
            [
                ChatMessage(role="system", content="你是低延迟实时面试回答助手。"),
                ChatMessage(role="user", content=prompt),
            ],
            api_key=answer_resolution.credential,
        )
        async for chunk in iterate_in_threadpool(iterator):
            yield chunk

    async def save_transcript(event: TranscriptEvent) -> None:
        correction = corrector.correct(event.text)
        await asyncio.to_thread(
            repository.append_final_utterance,
            record.organization_id,
            record.actor_id,
            record.id,
            event,
            corrected_text=correction.corrected_text,
        )

    async def save_answer(version, attempt, question, intent, answer_status, answer_text) -> None:
        await asyncio.to_thread(
            repository.upsert_answer,
            record.organization_id,
            record.actor_id,
            record.id,
            question_version=version,
            attempt=attempt,
            question=question,
            intent=intent,
            status=AnswerStatus(answer_status),
            answer_text=answer_text,
        )

    return LiveSessionManager(
        asr_sessions=sessions,
        answer_service=LiveAnswerService(prompt_streamer),
        answer_context=LiveAnswerContext(
            candidate_facts=candidate_facts,
            target_role=target_role,
            interview_evidence=evidence,
            terminology=terms,
        ),
        transcript_hook=save_transcript,
        answer_hook=save_answer,
    )


def _resolve_asr_provider(services, record: LiveInterviewSessionRecord):
    if record.asr_provider == "fake" and os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1":
        return FakeAsrProvider()
    if record.asr_model_profile_id is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("请配置带 transcription 能力的 OpenAI 模型档案")
        return OpenAIRealtimeAsrProvider(
            api_key,
            model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        )
    resolution = services.model_gateway.resolve(
        record.organization_id,
        ModelSelectionRequest(
            mode=ModelSelectionMode.SPECIFIC_PROFILE,
            profile_id=record.asr_model_profile_id,
            required_capabilities=frozenset({ModelCapability.TRANSCRIPTION}),
        ),
    )
    if resolution.readiness is not ModelReadiness.READY or not resolution.credential:
        raise RuntimeError("实时转写模型尚未配置可用凭据")
    if resolution.profile.provider_key != "openai":
        raise RuntimeError("首版实时转写只支持 OpenAI Provider")
    return OpenAIRealtimeAsrProvider(
        resolution.credential,
        model=resolution.profile.model_id,
        base_url=_realtime_url(resolution.profile.api_base_url),
    )


def _realtime_url(api_base_url: str | None) -> str:
    if not api_base_url:
        return "wss://api.openai.com/v1/realtime"
    base = api_base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    return base if base.endswith("/realtime") else f"{base}/realtime"


def _model_payload(availability) -> dict[str, object]:
    profile = availability.profile
    return {
        "id": str(profile.id),
        "display_name": profile.display_name,
        "provider_key": profile.provider_key,
        "model_id": profile.model_id,
        "capabilities": sorted(item.value for item in profile.capabilities),
        "readiness": availability.readiness.value,
        "blocked_reason": availability.blocked_reason,
    }
