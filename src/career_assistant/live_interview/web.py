"""Windows 实时面试助手的 REST 配置接口与业务 WebSocket。"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import date
from threading import Event, Lock
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from src.career_assistant.contracts import ModelCapability, ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.live_interview.answer_service import LIVE_ANSWER_OPTIONS, LiveAnswerService
from src.career_assistant.live_interview.archive import LiveInterviewArchiveService
from src.career_assistant.live_interview.audio_relay import (
    DEFAULT_AUDIO_BUFFER_SECONDS,
    DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS,
    AudioRelayError,
    LiveAudioRelay,
)
from src.career_assistant.live_interview.asr.dashscope_realtime import (
    DEFAULT_DASHSCOPE_ASR_MODEL,
    DEFAULT_DASHSCOPE_ASR_URL,
    DEFAULT_DASHSCOPE_MAX_SENTENCE_SILENCE_MS,
    DEFAULT_DASHSCOPE_VAD_MODEL,
    DashScopeRealtimeAsrProvider,
)
from src.career_assistant.live_interview.asr.fake import FakeAsrProvider
from src.career_assistant.live_interview.asr.openai_realtime import OpenAIRealtimeAsrProvider
from src.career_assistant.live_interview.context_builder import LiveAnswerContext
from src.career_assistant.live_interview.desktop_launcher import (
    DesktopLauncherError,
    launch_windows_desktop_assistant,
)
from src.career_assistant.live_interview.contracts import (
    AnswerStatus,
    AudioAppendEvent,
    AudioChannel,
    AudioChannelEndEvent,
    AudioCommitEvent,
    SessionEndEvent,
    SessionPauseEvent,
    LiveInterviewStatus,
    TranscriptEvent,
    parse_client_event,
)
from src.career_assistant.live_interview.persistence import (
    LiveInterviewRepository,
    LiveInterviewSessionRecord,
    session_payload,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.live_interview.terminology import TerminologyCorrector
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
from src.platform_access.contracts import PlatformRole


router = APIRouter(prefix="/api/career/live-interviews", tags=["live-interview"])
logger = logging.getLogger(__name__)


class _ClosingAnswerIterator:
    """取消不等待阻塞中的 SSE 读取，由读取线程在返回后关闭上游。"""

    def __init__(self, iterator: Iterator[str]) -> None:
        self._iterator = iter(iterator)
        self._lock = Lock()
        self._close_requested = Event()
        self._closed = False

    def __iter__(self) -> "_ClosingAnswerIterator":
        return self

    def __next__(self) -> str:
        with self._lock:
            if self._close_requested.is_set():
                self._close_locked()
                raise StopIteration
            try:
                return next(self._iterator)
            finally:
                if self._close_requested.is_set():
                    self._close_locked()

    def close(self) -> None:
        self._close_requested.set()
        # next 尚在执行时不能调用 generator.close，也不能让取消等待模型返回。
        if self._lock.acquire(blocking=False):
            try:
                self._close_locked()
            finally:
                self._lock.release()

    def _close_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._iterator, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                logger.warning("live_answer_stream_close_failed error=%s", type(exc).__name__)


async def _iterate_answer_stream(iterator: Iterator[str]) -> AsyncIterator[str]:
    source = _ClosingAnswerIterator(iterator)
    try:
        async for chunk in iterate_in_threadpool(source):
            yield chunk
    finally:
        await asyncio.to_thread(source.close)


class _FatalAsrStreamError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CreateLiveInterviewRequest(BaseModel):
    asr_model_profile_id: UUID | None = None
    answer_model_profile_id: UUID | None = None
    client_kind: Literal["desktop", "browser"] = "desktop"
    candidate_audio_enabled: bool = True


class LiveInterviewArchivePreviewRequest(BaseModel):
    session_ids: list[UUID] = Field(min_length=1, max_length=20)


class LiveInterviewArchiveRequest(LiveInterviewArchivePreviewRequest):
    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date


def get_live_actor():
    from src.career_assistant.web.router import get_request_actor

    return get_request_actor()


@router.post("/desktop/launch")
def launch_desktop(request: Request, _actor=Depends(get_live_actor)) -> dict[str, str]:
    """从求职助手页面启动本机 Windows 双路音频采集器。"""

    try:
        result = launch_windows_desktop_assistant(
            api_base_url=str(request.base_url).rstrip("/"),
        )
    except DesktopLauncherError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": result.status, "message": result.message}


def get_live_read_services(request: Request):
    from src.career_assistant.web.router import get_career_read_services

    return get_career_read_services(request)


def get_live_repository(request: Request) -> LiveInterviewRepository:
    return LiveInterviewRepository(get_live_read_services(request).database)


def get_live_archive_service(request: Request) -> LiveInterviewArchiveService:
    from src.career_assistant.web.router import get_career_services

    services = get_career_services(request)
    return LiveInterviewArchiveService(
        live_repository=LiveInterviewRepository(services.database),
        interview_repository=services.interview_library_repository,
        interview_service=services.interview_library_service,
    )


@router.get("/setup-options")
def setup_options(request: Request, actor=Depends(get_live_actor)) -> dict[str, object]:
    services = get_live_read_services(request)
    models = services.model_gateway.list_availability(actor.organization_id)
    return {
        "environment_asr": _environment_asr_config(),
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
            "platform": "桌面版 Chrome、Edge、Firefox 或 Windows 10/11 Electron",
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
    environment_asr = _environment_asr_config()
    if payload.asr_model_profile_id is None and environment_asr["readiness"] != "ready":
        raise HTTPException(
            status_code=422,
            detail=str(environment_asr["blocked_reason"]),
        )
    repository = get_live_repository(request)
    try:
        record = repository.create_session(
            actor.organization_id,
            actor.actor_id,
            candidate_profile_id=None,
            target_role_profile_id=None,
            interview_experience_ids=(),
            asr_provider=(
                "fake"
                if os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1"
                else (
                    "profile"
                    if payload.asr_model_profile_id is not None
                    else str(environment_asr["provider_key"])
                )
            ),
            asr_model_profile_id=payload.asr_model_profile_id,
            answer_model_profile_id=payload.answer_model_profile_id,
            client_kind=payload.client_kind,
            candidate_audio_enabled=payload.candidate_audio_enabled,
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


@router.post("/sessions/{session_id}/end")
def end_inactive_session(session_id: UUID, request: Request, actor=Depends(get_live_actor)) -> dict[str, str]:
    repository = get_live_repository(request)
    if repository.finish_inactive(actor.organization_id, actor.actor_id, session_id):
        return {"status": "completed"}
    if repository.get_session(actor.organization_id, actor.actor_id, session_id) is None:
        raise HTTPException(status_code=404, detail="实时面试会话不存在或无权访问")
    raise HTTPException(status_code=409, detail="实时面试连接仍在工作，请通过当前连接结束")


@router.post("/archive/preview")
def preview_archive(
    payload: LiveInterviewArchivePreviewRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    """从完整后端历史读取结束弹窗的题数和前五题。"""

    try:
        preview = get_live_archive_service(request).preview(
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            session_ids=tuple(payload.session_ids),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _archive_preview_payload(preview)


@router.post("/archive", status_code=status.HTTP_201_CREATED)
def archive_interview(
    payload: LiveInterviewArchiveRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    """把本场多个实时会话中的问题归并到面经库。"""

    try:
        result = get_live_archive_service(request).archive(
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
            session_ids=tuple(payload.session_ids),
            company_name=payload.company_name,
            role_name=payload.role_name,
            interview_date=payload.interview_date,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        **_archive_preview_payload(result),
        "experience": {
            "id": str(result.experience.id),
            "company_name": result.experience.company_name,
            "role_name": result.experience.role_name,
            "job_name": result.experience.job_name,
            "interview_date": (
                result.experience.interview_date.isoformat()
                if result.experience.interview_date
                else None
            ),
        },
    }


def _archive_preview_payload(preview) -> dict[str, object]:
    return {
        "question_count": preview.question_count,
        "question_preview": list(preview.question_preview),
        "started_at": preview.started_at.isoformat(),
        "ended_at": preview.ended_at.isoformat(),
    }


@router.websocket("/{session_id}/stream")
async def live_interview_stream(websocket: WebSocket, session_id: UUID) -> None:
    actor = await _resolve_websocket_actor(websocket)
    if actor is None:
        return
    repository = await asyncio.to_thread(_repository_for_websocket, websocket)
    record = await asyncio.to_thread(
        repository.get_session,
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if record is None:
        await websocket.close(code=4404, reason="实时面试会话不存在或无权访问")
        return
    if record.status is LiveInterviewStatus.COMPLETED:
        await websocket.accept()
        await websocket.close(code=4410, reason="面试已结束")
        return

    connections: set[UUID] = getattr(websocket.app.state, "live_interview_connections", set())
    websocket.app.state.live_interview_connections = connections
    if session_id in connections:
        await websocket.accept()
        await websocket.close(code=4409, reason="该会话已有活动连接")
        return
    connections.add(session_id)
    manager: LiveSessionManager | None = None
    owns_active = False
    finalized = False

    async def settle_session(event_type: str) -> None:
        nonlocal finalized
        if event_type == "session.pause":
            updated = await asyncio.to_thread(repository.pause, actor.organization_id, actor.actor_id, session_id)
        else:
            updated = await asyncio.to_thread(repository.end, actor.organization_id, actor.actor_id, session_id)
        if not updated:
            raise RuntimeError("会话结束状态未保存")
        finalized = True

    try:
        await websocket.accept()
        owns_active = await asyncio.to_thread(repository.activate, actor.organization_id, actor.actor_id, session_id)
        if not owns_active:
            latest = await asyncio.to_thread(repository.get_session, actor.organization_id, actor.actor_id, session_id)
            if latest and latest.status is LiveInterviewStatus.COMPLETED:
                await websocket.close(code=4410, reason="面试已结束")
            else:
                await websocket.close(code=4409, reason="该会话已有活动连接")
            return
        manager = await _build_live_manager(websocket, repository, record)
        await manager.start()
        channels = (AudioChannel.INTERVIEWER, AudioChannel.CANDIDATE) if (
            record.candidate_audio_enabled
        ) else (AudioChannel.INTERVIEWER,)
        await _run_live_transport(websocket, manager, channels, settle_session=settle_session)
    except WebSocketDisconnect as exc:
        # 只记录标准连接原因，不让客户端可控文本或转写正文进入日志。
        reason = exc.reason if exc.reason in {
            "keepalive ping timeout", "session_ready_timeout", "session_ready_cancelled", ""
        } else "omitted"
        logger.info(
            "live_interview_client_disconnected session_id=%s code=%s reason=%s",
            session_id,
            exc.code,
            reason,
        )
    except _FatalAsrStreamError as exc:
        logger.warning("live_interview_asr_ended session_id=%s code=%s", session_id, exc.code)
        # fatal 事件已由 sender 完整发送，随后关闭连接以触发同会话恢复。
        try:
            await websocket.close(code=1013, reason=exc.code)
        except Exception:
            pass
    except AudioRelayError as exc:
        logger.warning("live_interview_audio_failed session_id=%s code=%s details=%s", session_id, exc.code, exc.details)
        try:
            await websocket.send_json({"type": "error", "code": exc.code, "message": str(exc), "fatal": True})
            await websocket.close(code=1013, reason=exc.code)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("live_interview_session_failed session_id=%s error=%s", session_id, type(exc).__name__)
        try:
            await websocket.send_json(
                {"type": "error", "code": "session_failed", "message": "实时面试会话已中止", "fatal": True}
            )
        except Exception:
            pass
    finally:
        try:
            if manager is not None:
                await manager.close("disconnect")
        except Exception as exc:
            logger.error("live_interview_close_failed session_id=%s error=%s", session_id, type(exc).__name__)
        finally:
            try:
                if owns_active and not finalized:
                    await asyncio.to_thread(repository.end, actor.organization_id, actor.actor_id, session_id, failed=True)
            except Exception as exc:
                logger.error("live_interview_end_failed session_id=%s error=%s", session_id, type(exc).__name__)
            finally:
                # 数据库短暂失败不能留下永久 4409 的内存连接锁。
                connections.discard(session_id)
                try:
                    await websocket.close()
                except Exception:
                    pass


async def _send_manager_events(
    websocket: WebSocket, manager: LiveSessionManager, *, candidate_requested: bool = False,
) -> dict[str, object]:
    while True:
        event = await manager.next_event()
        if event.type == "session.ended":
            return event.to_dict()
        await websocket.send_json(event.to_dict())
        if (event.type == "session.ready" and candidate_requested and "active_channels" in event.payload
                and AudioChannel.CANDIDATE.value not in event.payload["active_channels"]):
            await websocket.send_json({"type": "channel.ended", "channel": "candidate",
                                       "message": "候选人转写暂不可用，继续采集面试官声音。"})
        if event.type == "error" and event.payload.get("fatal"):
            raise _FatalAsrStreamError(str(event.payload.get("code", "asr_stream_failed")))


async def _run_live_transport(
    websocket: WebSocket,
    manager: LiveSessionManager,
    channels: tuple[AudioChannel, ...],
    *,
    audio_send_timeout_seconds: float = DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS,
    max_audio_frames: int | None = None,
    max_buffered_audio_seconds: float = DEFAULT_AUDIO_BUFFER_SECONDS,
    settle_session: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """收包持续运行，音频按轨转发；监控全部后台任务，防止静默失效。"""

    async def write_audio(event) -> None:
        if isinstance(event, AudioChannelEndEvent):
            await manager.finish_channel(event.channel)
        else:
            await manager.handle(event)

    relay = LiveAudioRelay(
        write_audio,
        channels,
        send_timeout_seconds=audio_send_timeout_seconds,
        max_frames=max_audio_frames,
        max_buffered_audio_seconds=max_buffered_audio_seconds,
        on_optional_failure=manager.disable_channel,
    )
    sender = asyncio.create_task(
        _send_manager_events(websocket, manager, candidate_requested=AudioChannel.CANDIDATE in channels),
        name="live-event-sender",
    )
    relay_failure = asyncio.create_task(relay.wait_failed(), name="live-audio-monitor")
    receiver: asyncio.Task | None = None
    finisher: asyncio.Task | None = None
    try:
        while not manager.closed:
            receiver = asyncio.create_task(websocket.receive_json(), name="live-client-receiver")
            done, _ = await asyncio.wait(
                (receiver, sender, relay_failure),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if relay_failure in done:
                relay_failure.result()
            if sender in done:
                sender.result()
                raise RuntimeError("实时事件发送任务意外结束")
            payload = receiver.result()
            try:
                event = parse_client_event(payload)
                if isinstance(event, AudioAppendEvent | AudioCommitEvent | AudioChannelEndEvent):
                    relay.enqueue(event)
                elif isinstance(event, SessionEndEvent | SessionPauseEvent):
                    await relay.drain()
                    finisher = asyncio.create_task(manager.handle(event))
                    first, _ = await asyncio.wait((finisher, sender), return_when=asyncio.FIRST_COMPLETED)
                    if sender in first:
                        sender.result()
                    await finisher
                    if settle_session is not None:
                        await settle_session(event.type)
                    # 先落定会话状态并发送全部尾句，客户端收到结束确认即可读取归档或接续。
                    ended = await asyncio.wait_for(sender, timeout=5)
                    await websocket.send_json(ended)
                    return
                else:
                    await manager.handle(event)
            except ValueError as exc:
                await websocket.send_json(
                    {"type": "error", "code": "invalid_event", "message": str(exc)}
                )
                continue
    finally:
        tasks = [sender, relay_failure]
        if receiver is not None:
            tasks.append(receiver)
        if finisher is not None:
            tasks.append(finisher)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await relay.close()


async def _resolve_websocket_actor(websocket: WebSocket):
    from src.career_assistant.web.router import CareerRequestActor

    requires_session = platform_auth_required()
    token = websocket.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        if not requires_session:
            return CareerRequestActor(DEFAULT_ORGANIZATION_ID, DEFAULT_ACTOR_ID)
        await websocket.close(code=4401, reason="请先登录后继续")
        return None
    try:
        def resolve():
            return get_platform_access_service(websocket).resolve_session(token)
        resolution = await asyncio.to_thread(resolve)
    except HTTPException:
        resolution = None
    if resolution is None:
        if not requires_session:
            return CareerRequestActor(DEFAULT_ORGANIZATION_ID, DEFAULT_ACTOR_ID)
        await websocket.close(code=4401, reason="请先登录后继续")
        return None
    return CareerRequestActor(
        resolution.user.organization_id,
        resolution.user.id,
        resolution.user.role,
    )


def _repository_for_websocket(websocket: WebSocket) -> LiveInterviewRepository:
    from src.career_assistant.web.router import get_career_read_services

    return LiveInterviewRepository(get_career_read_services(websocket).database)


async def _build_live_manager(
    websocket: WebSocket,
    repository: LiveInterviewRepository,
    record: LiveInterviewSessionRecord,
) -> LiveSessionManager:
    from src.career_assistant.web.router import get_career_services

    services = await asyncio.to_thread(get_career_services, websocket)
    resume = await asyncio.to_thread(
        repository.resume_state, record.organization_id, record.actor_id, record.id,
    )
    if resume is None:
        raise RuntimeError("实时面试会话不存在或无权恢复")
    terms: tuple[str, ...] = ()
    corrector = TerminologyCorrector(terms)

    answer_resolution = await asyncio.to_thread(
        services.model_gateway.resolve,
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

    def prompt_streamer(prompt: str) -> AsyncIterator[str]:
        iterator = services.model_connection_client.stream_complete(
            answer_resolution.profile,
            answer_resolution.credential_env_name,
            [
                ChatMessage(role="system", content="你是低延迟实时面试回答助手。"),
                ChatMessage(role="user", content=prompt),
            ],
            api_key=answer_resolution.credential,
            options=LIVE_ANSWER_OPTIONS,
        )
        return _iterate_answer_stream(iterator)

    async def save_transcript(event: TranscriptEvent) -> None:
        if record.client_kind == "browser" and event.channel is AudioChannel.CANDIDATE:
            return
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

    async def save_queue(queued, phase) -> None:
        if record.client_kind == "browser":
            # 麦克风文本仅供当前连接使用，恢复元数据不能间接持久化它。
            queued = replace(queued, recent_conversation=tuple(
                line for line in queued.recent_conversation if not line.startswith("candidate:")
            ))
        await asyncio.to_thread(
            repository.save_queued_answer,
            record.organization_id, record.actor_id, record.id, queued, phase,
        )

    asr_provider = await asyncio.to_thread(_resolve_asr_provider, services, record)
    sessions = {}
    try:
        sessions[AudioChannel.INTERVIEWER] = await asr_provider.start(AudioChannel.INTERVIEWER)
        if record.candidate_audio_enabled:
            try:
                sessions[AudioChannel.CANDIDATE] = await asr_provider.start(AudioChannel.CANDIDATE)
            except Exception as exc:
                logger.warning("live_interview_candidate_unavailable session_id=%s error=%s",
                               record.id, type(exc).__name__)
        return LiveSessionManager(
            asr_sessions=sessions, answer_service=LiveAnswerService(prompt_streamer),
            answer_context=LiveAnswerContext(), transcript_hook=save_transcript,
            answer_hook=save_answer, resume_state=resume, queue_hook=save_queue,
        )
    except BaseException:
        # 成功后由 manager 接管；构造失败或握手取消时由此处归还。
        await asyncio.gather(*(item.close() for item in sessions.values()), return_exceptions=True)
        raise


def _resolve_asr_provider(services, record: LiveInterviewSessionRecord):
    if record.asr_provider == "fake" and os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1":
        return FakeAsrProvider()
    if record.asr_model_profile_id is None:
        if record.asr_provider == "dashscope":
            return _dashscope_environment_provider()
        if record.asr_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY 尚未配置")
            return OpenAIRealtimeAsrProvider(
                api_key,
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
            )
        raise RuntimeError("实时转写 Provider 配置无效")
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
    if resolution.profile.provider_key in {"qwen", "dashscope", "aliyun"}:
        return DashScopeRealtimeAsrProvider(
            resolution.credential,
            model=resolution.profile.model_id or DEFAULT_DASHSCOPE_ASR_MODEL,
            base_url=os.getenv("DASHSCOPE_ASR_WEBSOCKET_URL", DEFAULT_DASHSCOPE_ASR_URL),
            workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", ""),
            max_sentence_silence_ms=_dashscope_sentence_silence_ms(),
            vad_model=os.getenv("DASHSCOPE_ASR_VAD_MODEL", DEFAULT_DASHSCOPE_VAD_MODEL),
        )
    if resolution.profile.provider_key != "openai":
        raise RuntimeError("实时转写仅支持 DashScope 或 OpenAI Provider")
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


def _environment_asr_config() -> dict[str, object]:
    if os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1":
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "本地测试转写",
            "provider_key": "fake",
            "model_id": "fake-asr",
        }
    if os.getenv("DASHSCOPE_API_KEY", "").strip():
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "阿里云百炼 · Qwen 实时转写",
            "provider_key": "dashscope",
            "model_id": os.getenv("DASHSCOPE_ASR_MODEL", DEFAULT_DASHSCOPE_ASR_MODEL),
            "max_sentence_silence_ms": _dashscope_sentence_silence_ms(),
        }
    if os.getenv("OPENAI_API_KEY", "").strip():
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "OpenAI 实时转写",
            "provider_key": "openai",
            "model_id": os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        }
    return {
        "readiness": "blocked",
        "blocked_reason": (
            "尚未配置实时转写模型。请在服务端设置 DASHSCOPE_API_KEY；"
            "DeepSeek 继续负责生成答案，不能直接把微信通话音频转成文字。"
        ),
        "display_name": "未配置实时转写模型",
        "provider_key": None,
        "model_id": None,
    }


def _dashscope_environment_provider() -> DashScopeRealtimeAsrProvider:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 尚未配置")
    return DashScopeRealtimeAsrProvider(
        api_key,
        model=os.getenv("DASHSCOPE_ASR_MODEL", DEFAULT_DASHSCOPE_ASR_MODEL),
        base_url=os.getenv("DASHSCOPE_ASR_WEBSOCKET_URL", DEFAULT_DASHSCOPE_ASR_URL),
        workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", ""),
        max_sentence_silence_ms=_dashscope_sentence_silence_ms(),
        vad_model=os.getenv("DASHSCOPE_ASR_VAD_MODEL", DEFAULT_DASHSCOPE_VAD_MODEL),
    )


def _dashscope_sentence_silence_ms() -> int:
    raw = os.getenv(
        "DASHSCOPE_ASR_MAX_SENTENCE_SILENCE_MS",
        str(DEFAULT_DASHSCOPE_MAX_SENTENCE_SILENCE_MS),
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("DASHSCOPE_ASR_MAX_SENTENCE_SILENCE_MS 必须是整数") from exc
    if not 200 <= value <= 6_000:
        raise RuntimeError("DASHSCOPE_ASR_MAX_SENTENCE_SILENCE_MS 必须在 200 到 6000 之间")
    return value


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
