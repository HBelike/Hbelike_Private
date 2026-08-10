"""求职助手的独立 FastAPI Router。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextvars import ContextVar, Token
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from queue import Empty, Queue
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.agent_loop import CareerAgentLoop
from src.career_assistant.attachments import (
    AttachmentParser,
    AttachmentSettings,
    TemporaryAttachmentStore,
)
from src.career_assistant.contracts import (
    AgentStepName,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.cloud_vision import CloudVisionRouter
from src.career_assistant.free_model_catalog import build_free_model_catalog_payload
from src.career_assistant.intake_graph import CareerIntakeGraph
from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewSourceType,
)
from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewCollectionService,
)
from src.career_assistant.interview_library.embedding import (
    OpenAICompatibleEmbeddingClient,
)
from src.career_assistant.interview_library.metadata import (
    InterviewMaterialMetadataExtractor,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.retrieval import (
    InterviewRetrievalService,
)
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.job_sources import JobPostingExtractor
from src.career_assistant.legacy_office import GotenbergOfficeConverter
from src.career_assistant.model_gateway import (
    ModelGateway,
    ModelProfileAvailability,
    ModelResolution,
)
from src.career_assistant.model_clients import (
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.privacy import SensitiveDataRedactor
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.persistence import (
    CareerConversationRepository,
    CareerDatabase,
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
    ModelProfileRecord,
)
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)
from src.career_assistant.settings import (
    load_attachment_processing_settings,
    load_career_runtime_settings,
    load_cloud_vision_settings,
    load_document_understanding_settings,
    load_legacy_office_conversion_settings,
    load_model_gateway_settings,
    load_interview_retrieval_settings,
    load_response_generation_settings,
)


router = APIRouter(prefix="/api/career", tags=["career-assistant"])
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CareerRequestActor:
    """当前请求在 Career 数据模型中的组织与操作者边界。"""

    organization_id: UUID
    actor_id: UUID


# API 的认证中间件会在每个已登录请求开始时写入真实平台用户。ContextVar 可以让既有
# 同步路由和流式处理链继续调用 get_request_actor()，无需把身份参数层层穿透到 Task。
# 开发环境未开启 PLATFORM_AUTH_REQUIRED 时保留默认 Actor，避免破坏本地历史验证脚本。
_request_actor_context: ContextVar[CareerRequestActor | None] = ContextVar(
    "career_request_actor",
    default=None,
)


@dataclass
class CareerAssistantServices:
    """一个 FastAPI 进程内共享的求职助手服务容器。"""

    database: CareerDatabase
    conversation_repository: CareerConversationRepository
    model_profile_repository: CareerModelProfileRepository
    interview_library_repository: InterviewLibraryRepository
    interview_library_service: InterviewLibraryService
    interview_retrieval_service: InterviewRetrievalService
    interview_collection_service: InterviewCollectionService
    agent_loop: CareerAgentLoop
    intake_graph: CareerIntakeGraph
    model_gateway: ModelGateway
    response_runner: CareerResponseRunner
    temporary_attachment_store: TemporaryAttachmentStore
    attachment_parser: AttachmentParser
    model_connection_client: OpenAICompatibleChatClient
    document_understanding_client: DoclingServiceDocumentParser | None
    legacy_office_converter: GotenbergOfficeConverter | None
    cloud_vision_client: CloudVisionRouter | None
    stream_turn_slots: BoundedSemaphore
    stream_heartbeat_seconds: float
    turn_expected_seconds: float

    def close(self) -> None:
        """在应用关闭时释放 HTTP 与 PostgreSQL 连接池。"""

        self.model_connection_client.close()
        self.interview_retrieval_service.close()
        if self.document_understanding_client is not None:
            self.document_understanding_client.close()
        if self.legacy_office_converter is not None:
            self.legacy_office_converter.close()
        if self.cloud_vision_client is not None:
            self.cloud_vision_client.close()
        self.database.close()


class CreateConversationRequest(BaseModel):
    """新建求职会话请求。"""

    title: str = Field(min_length=1, max_length=160)


class SubmitIntakeRequest(BaseModel):
    """文本和职位链接输入；文件上传将在附件解析步骤单独实现。"""

    text: str = Field(default="", max_length=30_000)
    job_url: str | None = Field(default=None, max_length=2_000)
    selection_mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    model_profile_id: UUID | None = None
    interview_experience_ids: list[UUID] = Field(default_factory=list, max_length=5)


class UpsertModelProfileRequest(BaseModel):
    """模型设置页保存的无密钥模型档案。"""

    display_name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=200)
    capabilities: set[ModelCapability] = Field(min_length=1)
    cost_tier: ModelCostTier
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    api_base_url: str | None = Field(default=None, max_length=500)
    provider_website_url: str | None = Field(default=None, max_length=500)


class ModelConnectionRequest(UpsertModelProfileRequest):
    """页面配置的完整模型连接；Key 仅用于本次测试与加密入库。"""

    api_key: str = Field(min_length=1, max_length=2_000)


class ResolveModelRequest(BaseModel):
    """聊天页请求预览或确认本轮模型路由。"""

    selection_mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    model_profile_id: UUID | None = None
    required_capabilities: set[ModelCapability] = Field(
        default_factory=lambda: {ModelCapability.TEXT},
        min_length=1,
    )


class CreateInterviewExperienceRequest(BaseModel):
    """手工整理完成的面经 Markdown 入库请求。

    附件和网页的原始内容不会从该接口写入数据库；它们会先经解析器转换成
    Markdown，再由此接口持久化文本、来源与索引切片。
    """

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date | None = None
    markdown_content: str = Field(min_length=1, max_length=300_000)
    source_type: InterviewSourceType = InterviewSourceType.MANUAL_TEXT
    source_platform: str | None = Field(default=None, max_length=80)
    source_url: str | None = Field(default=None, max_length=2_000)
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    job_name: str | None = Field(default=None, max_length=220)


class UpdateInterviewExperienceRequest(BaseModel):
    """编辑面经正文后触发切片重建的请求。"""

    markdown_content: str = Field(min_length=1, max_length=300_000)
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class CreateInterviewCollectionJobRequest(BaseModel):
    """创建一个平台关键词资料发现任务。"""

    platform_key: str = Field(min_length=1, max_length=50)
    keyword: str = Field(min_length=1, max_length=180)
    requested_limit: int = Field(default=10, ge=1, le=50)


class CollectInterviewUrlRequest(BaseModel):
    """读取用户明确提交的单个公开页面。"""

    source_url: str = Field(min_length=8, max_length=2_000)


class ImportInterviewCollectionCandidateRequest(BaseModel):
    """将已选择的候选正文写入面经库，并触发既有 RAG 建索引。"""

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date | None = None
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)


def install_career_assistant_api(app: FastAPI, project_root: Path) -> None:
    """将独立 Router 安装到既有 FastAPI 应用，不初始化旧模块以外的资源。

    PostgreSQL 连接采用懒加载：预览服务即使尚未配置 ``CAREER_DATABASE_URL`` 也能正常
    启动；只有访问求职助手接口时才会返回明确的配置错误。
    """

    app.include_router(router)
    app.state.career_assistant_project_root = project_root
    app.state.career_assistant_services = None
    app.state.career_assistant_services_lock = Lock()

    @app.on_event("shutdown")
    def close_career_assistant_services() -> None:
        """关闭求职助手连接池，不影响既有 SQLite 资源。"""

        services: CareerAssistantServices | None = app.state.career_assistant_services
        if services is not None:
            services.close()
            app.state.career_assistant_services = None


def set_request_actor(actor: CareerRequestActor) -> Token[CareerRequestActor | None]:
    """把认证后的平台用户映射为当前请求的 Career Actor。"""

    return _request_actor_context.set(actor)


def reset_request_actor(token: Token[CareerRequestActor | None]) -> None:
    """在请求结束时清除 ContextVar，避免连接复用时串用上一个用户。"""

    _request_actor_context.reset(token)


def get_request_actor() -> CareerRequestActor:
    """返回当前请求 Actor；仅未启用认证的本地开发保留默认 Actor。"""

    actor = _request_actor_context.get()
    if actor is not None:
        return actor

    return CareerRequestActor(
        organization_id=DEFAULT_ORGANIZATION_ID,
        actor_id=DEFAULT_ACTOR_ID,
    )


def get_career_services(request: Request) -> CareerAssistantServices:
    """懒加载求职助手服务；连接配置缺失时返回 503，不干扰旧接口。"""

    existing_services: CareerAssistantServices | None = request.app.state.career_assistant_services
    if existing_services is not None:
        return existing_services

    services_lock: Lock = request.app.state.career_assistant_services_lock
    with services_lock:
        existing_services = request.app.state.career_assistant_services
        if existing_services is not None:
            return existing_services

        _load_career_environment(request.app.state.career_assistant_project_root)
        database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
        if not database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="求职助手数据库尚未配置 CAREER_DATABASE_URL",
            )

        try:
            database = CareerDatabase(database_url)
            conversation_repository = CareerConversationRepository(database)
            model_profile_repository = CareerModelProfileRepository(database)
            interview_library_repository = InterviewLibraryRepository(database)
            agent_loop = CareerAgentLoop(conversation_repository)
            project_root: Path = request.app.state.career_assistant_project_root
            career_config_path = _resolve_career_config_path(project_root)
            settings = load_model_gateway_settings(
                career_config_path,
            )
            attachment_settings = load_attachment_processing_settings(
                career_config_path,
            )
            document_understanding_settings = load_document_understanding_settings(
                career_config_path,
            )
            cloud_vision_settings = load_cloud_vision_settings(
                career_config_path,
            )
            legacy_office_conversion_settings = load_legacy_office_conversion_settings(
                career_config_path,
            )
            response_generation_settings = load_response_generation_settings(
                career_config_path,
            )
            interview_retrieval_settings = load_interview_retrieval_settings(
                career_config_path,
            )
            runtime_settings = load_career_runtime_settings(
                career_config_path,
            )
            attachment_storage_settings = AttachmentSettings(
                temporary_root=_resolve_temporary_attachment_root(project_root),
                max_size_bytes=attachment_settings.max_size_bytes,
                ttl_seconds=attachment_settings.ttl_seconds,
                max_pdf_pages=attachment_settings.max_pdf_pages,
            )
            temporary_attachment_store = TemporaryAttachmentStore(
                attachment_storage_settings,
            )
            redactor = SensitiveDataRedactor(
                enabled=attachment_settings.redaction_enabled,
            )
            model_gateway = ModelGateway(model_profile_repository, settings)
            model_connection_client = OpenAICompatibleChatClient(
                completion_max_tokens=response_generation_settings.max_completion_tokens,
                request_timeout_seconds=response_generation_settings.request_timeout_seconds,
            )
            interview_retrieval_service = InterviewRetrievalService(
                interview_library_repository,
                interview_retrieval_settings,
                embedding_client=OpenAICompatibleEmbeddingClient(
                    interview_retrieval_settings.embedding,
                ),
            )
            interview_library_service = InterviewLibraryService(
                interview_library_repository,
                retrieval_service=interview_retrieval_service,
            )
            interview_collection_service = InterviewCollectionService(
                interview_library_repository,
                interview_library_service,
            )
            document_understanding_client = (
                DoclingServiceDocumentParser(document_understanding_settings)
                if document_understanding_settings.enabled
                else None
            )
            legacy_office_converter = (
                GotenbergOfficeConverter(legacy_office_conversion_settings)
                if legacy_office_conversion_settings.enabled
                else None
            )
            cloud_vision_client = (
                CloudVisionRouter(cloud_vision_settings)
                if cloud_vision_settings.enabled
                else None
            )
            attachment_parser = AttachmentParser(
                attachment_storage_settings,
                document_understanding_parser=document_understanding_client,
                legacy_office_converter=legacy_office_converter,
                cloud_vision_parser=cloud_vision_client,
            )
            services = CareerAssistantServices(
                database=database,
                conversation_repository=conversation_repository,
                model_profile_repository=model_profile_repository,
                interview_library_repository=interview_library_repository,
                interview_library_service=interview_library_service,
                interview_retrieval_service=interview_retrieval_service,
                interview_collection_service=interview_collection_service,
                agent_loop=agent_loop,
                intake_graph=CareerIntakeGraph(
                    agent_loop,
                    redactor=redactor,
                    attachment_parser=attachment_parser,
                    temporary_attachment_store=temporary_attachment_store,
                    job_posting_extractor=JobPostingExtractor(),
                ),
                model_gateway=model_gateway,
                response_runner=CareerResponseRunner(
                    agent_loop,
                    model_gateway,
                    chat_client=model_connection_client,
                    redactor=redactor,
                    max_persisted_response_characters=(
                        response_generation_settings.max_persisted_response_characters
                    ),
                    max_attempts=response_generation_settings.max_attempts,
                    retry_backoff_seconds=response_generation_settings.retry_backoff_seconds,
                ),
                temporary_attachment_store=temporary_attachment_store,
                attachment_parser=attachment_parser,
                model_connection_client=model_connection_client,
                document_understanding_client=document_understanding_client,
                legacy_office_converter=legacy_office_converter,
                cloud_vision_client=cloud_vision_client,
                stream_turn_slots=BoundedSemaphore(
                    value=runtime_settings.max_concurrent_turns,
                ),
                stream_heartbeat_seconds=runtime_settings.stream_heartbeat_seconds,
                turn_expected_seconds=runtime_settings.turn_timeout_seconds,
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"求职助手初始化失败：{exc}",
            ) from exc

        request.app.state.career_assistant_services = services
        return services


def _load_career_environment(project_root: Path) -> None:
    """仅加载求职助手自己的本地环境文件，不读取或覆盖既有业务密钥。"""

    environment_path = project_root / ".env.career-assistant"
    if not environment_path.is_file():
        return

    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("检测到求职助手环境文件，但缺少 python-dotenv") from exc

    load_dotenv(dotenv_path=environment_path, override=False)


def _resolve_career_config_path(project_root: Path) -> Path:
    """解析求职模块配置路径，允许容器部署通过环境变量替换配置挂载点。

    未设置 ``CAREER_ASSISTANT_CONFIG_PATH`` 时保留当前本地默认路径。相对路径
    始终相对于项目根目录解析，避免工作目录变化后加载到意外的 YAML；路径只由
    服务端环境控制，永不接收浏览器输入。
    """

    configured_path = os.getenv("CAREER_ASSISTANT_CONFIG_PATH", "").strip()
    if not configured_path:
        return project_root / "config" / "career_assistant.yaml"

    candidate = Path(configured_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved_path = candidate.resolve()
    if not resolved_path.is_file():
        raise ValueError(
            "未找到 CAREER_ASSISTANT_CONFIG_PATH 指定的配置文件："
            f"{resolved_path}",
        )
    return resolved_path


def _resolve_temporary_attachment_root(project_root: Path) -> Path:
    """确定附件临时目录；生产容器可把它挂载到 tmpfs，避免原件落盘持久化。"""

    configured_root = os.getenv("CAREER_TEMPORARY_ATTACHMENT_ROOT", "").strip()
    if not configured_root:
        return project_root / "data" / "career-temporary-attachments"

    candidate = Path(configured_root).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


@router.get("/conversations")
def list_conversations(request: Request) -> dict[str, object]:
    """读取当前用户的求职会话历史摘要。"""

    actor = get_request_actor()
    services = get_career_services(request)
    conversations = services.conversation_repository.list_conversations(actor.actor_id)
    return {"items": [_conversation_payload(item) for item in conversations]}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    request_body: CreateConversationRequest,
    request: Request,
) -> dict[str, object]:
    """创建新会话；由 AgentLoop 而不是 Web 层直接调用仓储。"""

    actor = get_request_actor()
    services = get_career_services(request)
    conversation = services.agent_loop.open_conversation(
        actor.organization_id,
        actor.actor_id,
        request_body.title,
    )
    return _conversation_payload(conversation)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, request: Request) -> dict[str, object]:
    """读取会话及其已脱敏消息历史。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        conversation = services.agent_loop.resume_conversation(actor.actor_id, conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    messages = services.conversation_repository.list_messages(actor.actor_id, conversation_id)
    last_model_selection = services.conversation_repository.get_last_model_selection(
        actor.actor_id,
        conversation_id,
    )
    latest_turn = services.conversation_repository.get_latest_agent_turn(
        actor.actor_id,
        conversation_id,
    )
    return {
        "conversation": _conversation_payload(conversation),
        "messages": [_message_payload(item) for item in messages],
        "last_model_selection": _model_selection_payload(last_model_selection),
        "latest_turn": _turn_payload(latest_turn) if latest_turn is not None else None,
    }


@router.post("/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: UUID, request: Request) -> dict[str, object]:
    """归档会话，保留历史但禁止后续继续写入。"""

    actor = get_request_actor()
    services = get_career_services(request)
    archived = services.conversation_repository.archive_conversation(actor.actor_id, conversation_id)
    if not archived:
        raise HTTPException(status_code=404, detail="会话不存在、无访问权限或不可归档")
    conversation = services.agent_loop.resume_conversation(actor.actor_id, conversation_id)
    return _conversation_payload(conversation)


@router.post("/conversations/{conversation_id}/intake", status_code=status.HTTP_202_ACCEPTED)
def submit_intake(
    conversation_id: UUID,
    request_body: SubmitIntakeRequest,
    request: Request,
) -> dict[str, object]:
    """提交一轮文本或职位链接输入，完成处理图后调用已选模型回复。"""

    actor = get_request_actor()
    services = get_career_services(request)
    selection = ModelSelectionRequest(
        mode=request_body.selection_mode,
        profile_id=request_body.model_profile_id,
    )
    try:
        interview_evidence = _build_interview_evidence(
            services,
            actor,
            request_body.interview_experience_ids,
            request_body.text,
        )
        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=request_body.text,
            job_url=request_body.job_url,
            model_selection=selection,
            interview_evidence=interview_evidence,
        )
        result = services.intake_graph.run(inbound_message)
        response_result = services.response_runner.run(
            inbound_message,
            result,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(result.persisted_message),
        "assistant_message": _message_payload(response_result.assistant_message),
        "completed_steps": [step.value for step in result.completed_steps],
        "job_source": _job_source_payload(result),
    }


@router.post("/conversations/{conversation_id}/intake-stream")
def stream_intake(
    conversation_id: UUID,
    request_body: SubmitIntakeRequest,
    request: Request,
) -> StreamingResponse:
    """流式提交文本输入；状态、增量和最终持久化结果均通过 SSE 返回。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        interview_evidence = _build_interview_evidence(
            services,
            actor,
            request_body.interview_experience_ids,
            request_body.text,
        )
        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=request_body.text,
            job_url=request_body.job_url,
            model_selection=ModelSelectionRequest(
                mode=request_body.selection_mode,
                profile_id=request_body.model_profile_id,
            ),
            interview_evidence=interview_evidence,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _create_career_streaming_response(
        services,
        inbound_message,
    )


@router.post(
    "/conversations/{conversation_id}/intake-with-materials",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_intake_with_materials(
    conversation_id: UUID,
    request: Request,
    text: str = Form(default=""),
    job_url: str | None = Form(default=None),
    selection_mode: ModelSelectionMode = Form(
        default=ModelSelectionMode.FREE_QUOTA_FIRST,
    ),
    model_profile_id: UUID | None = Form(default=None),
    interview_experience_ids: list[UUID] = Form(default_factory=list),
    resume_file: UploadFile | None = File(default=None),
    job_description_file: UploadFile | None = File(default=None),
) -> dict[str, object]:
    """临时接收简历或职位材料，完成解析后只持久化脱敏摘要并删除原文件。"""

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
        interview_evidence = _build_interview_evidence(
            services,
            actor,
            interview_experience_ids,
            text,
        )
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

        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=text,
            job_url=job_url,
            model_selection=ModelSelectionRequest(
                mode=selection_mode,
                profile_id=model_profile_id,
            ),
            attachments=tuple(attachments),
            interview_evidence=interview_evidence,
        )
        result = services.intake_graph.run(inbound_message)
        response_result = services.response_runner.run(
            inbound_message,
            result,
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise

    return {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(result.persisted_message),
        "assistant_message": _message_payload(response_result.assistant_message),
        "completed_steps": [step.value for step in result.completed_steps],
        "job_source": _job_source_payload(result),
        "attachment_processing": {
            "temporary_only": True,
            "count": len(attachments),
            "text_characters": _analyzed_material_characters(result.model_context),
            "resume_outline_characters": len(
                result.model_context.redacted_resume_outline,
            ),
            "notices": list(result.model_context.document_processing_notices),
            "items": [
                _attachment_processing_summary_payload(item)
                for item in result.model_context.attachment_processing_summaries
            ],
            "pdf_without_extractable_text_count": (
                result.model_context.pdf_without_extractable_text_count
            ),
            "cleaned_after_turn": True,
        },
    }


@router.post("/conversations/{conversation_id}/intake-with-materials-stream")
async def stream_intake_with_materials(
    conversation_id: UUID,
    request: Request,
    text: str = Form(default=""),
    job_url: str | None = Form(default=None),
    selection_mode: ModelSelectionMode = Form(
        default=ModelSelectionMode.FREE_QUOTA_FIRST,
    ),
    model_profile_id: UUID | None = Form(default=None),
    interview_experience_ids: list[UUID] = Form(default_factory=list),
    resume_file: UploadFile | None = File(default=None),
    job_description_file: UploadFile | None = File(default=None),
) -> StreamingResponse:
    """上传材料后先发送解析状态，再把真实模型输出以 SSE 持续推送给页面。"""

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
        interview_evidence = _build_interview_evidence(
            services,
            actor,
            interview_experience_ids,
            text,
        )
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
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    inbound_message = CareerInboundMessage(
        turn_id=uuid4(),
        conversation_id=conversation_id,
        actor_id=actor.actor_id,
        text=text,
        job_url=job_url,
        model_selection=ModelSelectionRequest(
            mode=selection_mode,
            profile_id=model_profile_id,
        ),
        attachments=tuple(attachments),
        interview_evidence=interview_evidence,
    )
    return _create_career_streaming_response(
        services,
        inbound_message,
        attachment_count=len(attachments),
    )


@router.get("/interview-library/collection-platforms")
def list_interview_collection_platforms(
    request: Request,
) -> dict[str, object]:
    """返回页面可展示的采集连接器边界，不返回账号、Cookie 或会话状态。"""

    services = get_career_services(request)
    return {
        "items": [
            {
                "key": policy.key,
                "label": policy.label,
                "can_run_keyword_search": policy.can_run_keyword_search,
                "connector_kind": policy.connector_kind.value,
                "policy_decision": policy.policy_decision,
            }
            for policy in services.interview_collection_service.list_platform_policies()
        ]
    }


@router.post(
    "/interview-library/collection-jobs",
    status_code=status.HTTP_201_CREATED,
)
def create_interview_collection_job(
    request_body: CreateInterviewCollectionJobRequest,
    request: Request,
) -> dict[str, object]:
    """创建关键词发现任务；无授权连接器时清楚标记为等待用户处理。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.create_keyword_collection_job(
            actor.organization_id,
            platform_key=request_body.platform_key,
            keyword=request_body.keyword,
            requested_limit=request_body.requested_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _collection_job_payload(job)


@router.get("/interview-library/collection-jobs/{job_id}")
def get_interview_collection_job(
    job_id: UUID,
    request: Request,
) -> dict[str, object]:
    """读取采集任务及其候选资料，以便页面恢复中断后的导入动作。"""

    actor = get_request_actor()
    services = get_career_services(request)
    job = services.interview_library_repository.get_collection_job(
        actor.organization_id,
        job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="采集任务不存在或无访问权限")
    candidates = services.interview_library_repository.list_collection_candidates(
        actor.organization_id,
        job_id,
    )
    return {
        **_collection_job_payload(job),
        "candidates": [_collection_candidate_payload(candidate) for candidate in candidates],
    }


@router.post(
    "/interview-library/collect-url",
    status_code=status.HTTP_201_CREATED,
)
def collect_interview_public_url(
    request_body: CollectInterviewUrlRequest,
    request: Request,
) -> dict[str, object]:
    """读取用户主动粘贴的公开 HTTPS 页面，生成待确认的候选资料。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job, candidate = services.interview_collection_service.collect_public_url(
            actor.organization_id,
            source_url=request_body.source_url,
        )
    except CollectionOperationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job": _collection_job_payload(job),
        "candidate": _collection_candidate_payload(candidate),
    }


@router.post("/interview-library/collection-candidates/{candidate_id}/select")
def select_interview_collection_candidate(
    candidate_id: UUID,
    request: Request,
) -> dict[str, object]:
    """标记用户准备入库的候选资料，防止未读正文直接进入 RAG。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        candidate = services.interview_collection_service.select_candidate(
            actor.organization_id,
            candidate_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _collection_candidate_payload(candidate)


@router.post(
    "/interview-library/collection-candidates/{candidate_id}/import",
    status_code=status.HTTP_201_CREATED,
)
def import_interview_collection_candidate(
    candidate_id: UUID,
    request_body: ImportInterviewCollectionCandidateRequest,
    request: Request,
) -> dict[str, object]:
    """将候选网页 Markdown 导入现有面经库，并复用切片与检索链路。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_collection_service.ingest_selected_candidate(
            actor.organization_id,
            candidate_id=candidate_id,
            company_name=request_body.company_name,
            role_name=request_body.role_name,
            interview_date=request_body.interview_date,
            summary_text=request_body.summary_text,
            tags=tuple(request_body.tags),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _interview_experience_payload(experience)


@router.get("/interview-library/tree")
def list_interview_library_tree(
    request: Request,
    query: str | None = None,
) -> dict[str, object]:
    """读取公司 → 岗位 → 面经日期的树形导航数据。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        items = services.interview_library_repository.list_tree(
            actor.organization_id,
            query=query,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items}


@router.get("/interview-library/experiences/{experience_id}")
def get_interview_experience(
    experience_id: UUID,
    request: Request,
) -> dict[str, object]:
    """读取一份面经正文及其可追溯的来源元数据。"""

    actor = get_request_actor()
    services = get_career_services(request)
    experience = services.interview_library_repository.get_experience(
        actor.organization_id,
        experience_id,
    )
    if experience is None:
        raise HTTPException(status_code=404, detail="面经不存在或无访问权限")
    return _interview_experience_payload(experience)


@router.post(
    "/interview-library/experiences",
    status_code=status.HTTP_201_CREATED,
)
def create_interview_experience(
    request_body: CreateInterviewExperienceRequest,
    request: Request,
) -> dict[str, object]:
    """把已解析且清洗完成的面经 Markdown 入库并建立可检索切片。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_library_service.ingest(
            actor.organization_id,
            InterviewExperienceDraft(
                company_name=request_body.company_name,
                role_name=request_body.role_name,
                interview_date=request_body.interview_date,
                markdown_content=request_body.markdown_content,
                source_type=request_body.source_type,
                source_platform=request_body.source_platform,
                source_url=request_body.source_url,
                summary_text=request_body.summary_text,
                tags=tuple(request_body.tags),
                job_name=request_body.job_name,
            ),
            trigger_type=(
                IngestionTriggerType.MANUAL_URL
                if request_body.source_url
                else IngestionTriggerType.MANUAL_UPLOAD
                if request_body.source_type is InterviewSourceType.MANUAL_UPLOAD
                else IngestionTriggerType.MANUAL_URL
                if request_body.source_type is InterviewSourceType.PUBLIC_URL
                else IngestionTriggerType.MANUAL_UPLOAD
            ),
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("面经保存失败，数据库事务已回滚")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="面经暂时无法保存，请稍后重试；已解析的内容仍保留在当前页面。",
        ) from exc
    return _interview_experience_payload(experience)


@router.post(
    "/interview-library/parse-file",
)
async def parse_interview_experience_file(
    request: Request,
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
) -> dict[str, object]:
    """解析面经附件并返回可编辑草稿，不直接持久化原文件或写入知识库。"""

    services = get_career_services(request)
    attachments = []
    try:
        attachment = await services.temporary_attachment_store.save_upload(
            source_file,
            _upload_kind(source_file, is_resume=True),
        )
        attachments.append(attachment)
        # 文档解析可能触发 Docling、OCR 或视觉模型；移至工作线程，避免阻塞流式进度与其他请求。
        parsed = await asyncio.to_thread(services.attachment_parser.parse, attachment)
        extracted_text = parsed.extracted_text.strip()
        if not extracted_text:
            detail = parsed.document_understanding_error or (
                "未能从文件中识别到可用文字；请上传更清晰的原图、可复制文本的 PDF，或改用粘贴正文。"
            )
            raise ValueError(detail)

        inferred = InterviewMaterialMetadataExtractor().extract(
            extracted_text,
            filename=attachment.original_filename,
            source_platform=source_platform,
        )
        company_name = inferred.company_name or "待归档公司"
        role_name = inferred.role_name or "未识别岗位"
        markdown_content = _interview_markdown_from_parsed_attachment(
            extracted_text,
            company_name=company_name,
            role_name=role_name,
        )
        warnings = []
        if inferred.company_name is None:
            warnings.append("未可靠识别公司名称，请在确认入库前补充或修正。")
        if inferred.role_name is None:
            warnings.append("未可靠识别面试岗位，请在确认入库前补充或修正。")
        if parsed.document_understanding_error:
            warnings.append(parsed.document_understanding_error)
        return {
            "draft": {
                "company_name": company_name,
                "role_name": role_name,
                "interview_date": (
                    inferred.interview_date.isoformat()
                    if inferred.interview_date is not None
                    else None
                ),
                "source_platform": inferred.source_platform,
                "source_url": source_url,
                "tags": list(inferred.tags),
                "summary_text": inferred.summary_text,
            },
            "markdown_content": markdown_content,
            "recognition": {
                "confidence": inferred.confidence,
                "evidence": list(inferred.evidence),
                "parser": (
                    "cloud_vision"
                    if parsed.cloud_vision_result is not None
                    else "docling_ocr"
                ),
                "warnings": warnings,
            },
        }
    except (LookupError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - 防止临时文件解析错误被前端压成无意义 500
        logger.exception("面经文件预解析失败：%s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="文件解析服务发生内部错误，未保存任何原始文件；请稍后重试。",
        ) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))


@router.post(
    "/interview-library/parse-file-stream",
)
async def stream_parse_interview_experience_file(
    request: Request,
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
) -> StreamingResponse:
    """以 NDJSON 返回面经材料预解析进度；完成后给出与普通预解析接口一致的草稿。"""

    async def event_stream() -> Iterator[str]:
        def encode(event: str, **payload: object) -> str:
            return json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n"

        yield encode(
            "progress",
            percent=8,
            phase="正在接收材料",
            detail="文件已进入临时解析队列，原件不会写入面经库。",
        )
        parse_task = asyncio.create_task(
            parse_interview_experience_file(
                request=request,
                source_file=source_file,
                source_platform=source_platform,
                source_url=source_url,
            )
        )
        percent = 28
        yield encode(
            "progress",
            percent=percent,
            phase="正在识别版面与正文",
            detail="正在选择 Docling、OCR 或视觉理解链路。",
        )

        while not parse_task.done():
            await asyncio.sleep(0.75)
            next_percent = min(76, percent + 4)
            if next_percent == percent:
                # 解析仍在进行，但不重复发送相同百分比，避免浏览器收到无意义的进度事件。
                continue
            percent = next_percent
            yield encode(
                "progress",
                percent=percent,
                phase="正在提取面经信息",
                detail="正在读取正文、标题、公司和岗位线索。",
            )

        try:
            payload = await parse_task
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "文件解析未完成。"
            yield encode("error", message=detail)
            return
        except Exception as exc:  # pragma: no cover - 流式传输兜底
            logger.exception("面经文件流式预解析失败：%s", type(exc).__name__)
            yield encode("error", message="文件解析服务暂时不可用，请稍后重试。")
            return

        yield encode(
            "progress",
            percent=92,
            phase="正在生成可编辑草稿",
            detail="正在整理 Markdown 正文与自动识别字段。",
        )
        yield encode(
            "progress",
            percent=100,
            phase="识别完成",
            detail="已生成预填草稿，请核对后再保存并建立索引。",
        )
        yield encode("result", payload=payload)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/interview-library/import-file",
    status_code=status.HTTP_201_CREATED,
)
async def import_interview_experience_file(
    request: Request,
    company_name: str = Form(default=""),
    role_name: str = Form(default=""),
    interview_date: date | None = Form(default=None),
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    summary_text: str | None = Form(default=None),
    tags_json: str = Form(default="[]"),
) -> dict[str, object]:
    """复用求职助手附件解析器导入面经，只持久化转换后的 Markdown。

    这里不会调用会话 Graph，也不会把原文件、临时路径或二进制内容写入历史记录。无论
    解析、入库成功或失败，临时目录都会在 finally 中清理。
    """

    actor = get_request_actor()
    services = get_career_services(request)
    attachments = []
    try:
        try:
            tags_value = json.loads(tags_json)
        except json.JSONDecodeError as exc:
            raise ValueError("标签格式无效，请传入 JSON 字符串数组") from exc
        if not isinstance(tags_value, list) or not all(
            isinstance(item, str) for item in tags_value
        ):
            raise ValueError("标签必须是字符串数组")

        attachment = await services.temporary_attachment_store.save_upload(
            source_file,
            _upload_kind(source_file, is_resume=True),
        )
        attachments.append(attachment)
        parsed = services.attachment_parser.parse(attachment)
        inferred = InterviewMaterialMetadataExtractor().extract(
            parsed.extracted_text,
            filename=attachment.original_filename,
            source_platform=source_platform,
        )
        effective_company_name = company_name.strip() or inferred.company_name or "待归档公司"
        effective_role_name = role_name.strip() or inferred.role_name or "未识别岗位"
        markdown_content = _interview_markdown_from_parsed_attachment(
            parsed.extracted_text,
            company_name=effective_company_name,
            role_name=effective_role_name,
        )
        effective_tags = tuple(dict.fromkeys([*tags_value, *inferred.tags]))
        experience = services.interview_library_service.ingest(
            actor.organization_id,
            InterviewExperienceDraft(
                company_name=effective_company_name,
                role_name=effective_role_name,
                interview_date=interview_date or inferred.interview_date,
                markdown_content=markdown_content,
                source_type=InterviewSourceType.MANUAL_UPLOAD,
                source_platform=source_platform or inferred.source_platform,
                source_url=source_url,
                summary_text=summary_text or inferred.summary_text,
                tags=effective_tags,
            ),
            trigger_type=IngestionTriggerType.MANUAL_UPLOAD,
        )
    except (LookupError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))
    return _interview_experience_payload(experience)


@router.put("/interview-library/experiences/{experience_id}")
def update_interview_experience(
    experience_id: UUID,
    request_body: UpdateInterviewExperienceRequest,
    request: Request,
) -> dict[str, object]:
    """保存手动编辑的 Markdown，同时以同一版本重建检索切片。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_library_service.update_markdown(
            actor.organization_id,
            experience_id,
            markdown_content=request_body.markdown_content,
            summary_text=request_body.summary_text,
            tags=tuple(request_body.tags),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _interview_experience_payload(experience)


@router.get("/interview-library/mentions")
def search_interview_library_mentions(
    request: Request,
    query: str,
    limit: int = 8,
) -> dict[str, object]:
    """为求职助手的 @ 面经选择器返回轻量候选项。"""

    if not query.strip():
        return {"items": []}
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=422, detail="面经候选数量必须位于 1 到 20 之间")

    actor = get_request_actor()
    services = get_career_services(request)
    items = services.interview_library_service.search_for_mention(
        actor.organization_id,
        query,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "company_name": item.company_name,
                "role_name": item.role_name,
                "job_name": item.job_name,
                "interview_date": (
                    item.interview_date.isoformat()
                    if item.interview_date is not None
                    else None
                ),
                "summary_text": item.summary_text,
                "tags": list(item.tags),
            }
            for item in items
        ]
    }


@router.get("/interview-library/search")
def retrieve_interview_library_chunks(
    request: Request,
    query: str,
    limit: int | None = None,
) -> dict[str, object]:
    """按 RAG 策略检索面经证据片段，供面经库与后续 ``@面经`` 对话共用。

    端点只返回必要的、带来源元数据的文本片段；不会返回原始上传文件、Embedding
    向量或模型凭据。未配置向量模型时会透明退化为 PostgreSQL 关键词检索，保证面经库
    在本地开发和生产冷启动时仍然可用。
    """

    normalized_query = query.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="检索关键词不能为空")

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        result = services.interview_retrieval_service.retrieve(
            actor.organization_id,
            normalized_query,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "query": result.query,
        "retrieval_mode": result.retrieval_mode,
        "degraded_reason": result.degraded_reason,
        "items": [_interview_chunk_candidate_payload(item) for item in result.candidates],
    }


@router.get("/model-profiles")
def list_model_profiles(request: Request) -> dict[str, object]:
    """列出模型设置页档案及其无密钥可用性状态。"""

    actor = get_request_actor()
    services = get_career_services(request)
    availability = services.model_gateway.list_availability(actor.organization_id)
    return {"items": [_availability_payload(item) for item in availability]}


@router.get("/free-model-catalog")
def list_free_model_catalog(request: Request) -> dict[str, object]:
    """返回官方免费额度目录与平台当前可供访客直接使用的状态。

    该接口只返回公开的接入说明和无密钥的模型档案摘要。任何 API Key 都留在服务端，
    因此已启用的免费连接可安全复用于后续登录体系下的普通访客。
    """

    actor = get_request_actor()
    services = get_career_services(request)
    availability = services.model_gateway.list_availability(actor.organization_id)
    return {"items": build_free_model_catalog_payload(availability)}


@router.put("/model-profiles/{profile_key}")
def upsert_model_profile(
    profile_key: str,
    request_body: UpsertModelProfileRequest,
    request: Request,
) -> dict[str, object]:
    """创建或更新一个不含密钥的模型档案。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        profile = services.model_profile_repository.upsert_profile(
            actor.organization_id,
            ModelProfileDraft(
                profile_key=profile_key,
                display_name=request_body.display_name,
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                capabilities=frozenset(request_body.capabilities),
                cost_tier=request_body.cost_tier,
                priority=request_body.priority,
                enabled=request_body.enabled,
                api_base_url=request_body.api_base_url,
                provider_website_url=request_body.provider_website_url,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    availability = next(
        item
        for item in services.model_gateway.list_availability(actor.organization_id)
        if item.profile.id == profile.id
    )
    return _availability_payload(availability)


@router.post("/model-connections/test")
def test_model_connection(
    request_body: ModelConnectionRequest,
    request: Request,
) -> dict[str, object]:
    """真实验证浏览器填写的模型连接，但不写入档案或 API Key。"""

    services = get_career_services(request)
    response_preview = _verify_model_connection(services, request_body)
    return {
        "verified": True,
        "provider_key": request_body.provider_key,
        "model_id": request_body.model_id,
        "response_preview": response_preview[:120],
    }


@router.put("/model-connections/{profile_key}")
def save_model_connection(
    profile_key: str,
    request_body: ModelConnectionRequest,
    request: Request,
) -> dict[str, object]:
    """保存前再次真实验证连接，并原子保存档案和加密 API Key。"""

    actor = get_request_actor()
    services = get_career_services(request)
    _verify_model_connection(services, request_body)
    try:
        profile = services.model_profile_repository.upsert_profile(
            actor.organization_id,
            ModelProfileDraft(
                profile_key=profile_key,
                display_name=request_body.display_name,
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                capabilities=frozenset(request_body.capabilities),
                cost_tier=request_body.cost_tier,
                priority=request_body.priority,
                enabled=request_body.enabled,
                api_base_url=request_body.api_base_url,
                provider_website_url=request_body.provider_website_url,
            ),
            api_key=request_body.api_key,
        )
    except (SQLAlchemyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    availability = next(
        item
        for item in services.model_gateway.list_availability(actor.organization_id)
        if item.profile.id == profile.id
    )
    return _availability_payload(availability)


@router.post("/model-resolution")
def resolve_model(
    request_body: ResolveModelRequest,
    request: Request,
) -> dict[str, object]:
    """预览当前选择会路由到哪个模型，但不触发真实模型调用。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        resolution = services.model_gateway.resolve(
            actor.organization_id,
            ModelSelectionRequest(
                mode=request_body.selection_mode,
                profile_id=request_body.model_profile_id,
                required_capabilities=frozenset(request_body.required_capabilities),
            ),
        )
    except (LookupError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _resolution_payload(resolution)


def _verify_model_connection(
    services: CareerAssistantServices,
    request_body: ModelConnectionRequest,
) -> str:
    """验证 OpenAI-compatible 请求能真实完成，失败时不保存任何用户配置。"""

    api_base_url = (request_body.api_base_url or "").strip().rstrip("/")
    if api_base_url and not api_base_url.startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail="请求地址必须使用 HTTPS，且填写到 API Base URL 层级，不要包含 /chat/completions",
        )
    if api_base_url.endswith("/chat/completions"):
        raise HTTPException(
            status_code=422,
            detail="请求地址不应包含 /chat/completions，系统会自动补全该路径",
        )

    try:
        return services.model_connection_client.test_connection(
            ModelConnectionTarget(
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                api_base_url=api_base_url or None,
            ),
            request_body.api_key,
        )
    except ModelInvocationError as exc:
        raise HTTPException(status_code=422, detail=f"模型连接测试未通过：{exc}") from exc


def _stream_career_response(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> Iterator[str]:
    """为真实处理流补充 SSE 心跳，Turn 完成后才归还并发名额。"""

    yield from _stream_events_with_heartbeats(
        _stream_career_response_events(
            services,
            inbound_message,
            attachment_count=attachment_count,
        ),
        heartbeat_seconds=services.stream_heartbeat_seconds,
        expected_turn_seconds=services.turn_expected_seconds,
        on_processing_complete=services.stream_turn_slots.release,
    )


def _stream_career_response_events(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> Iterator[str]:
    """将输入处理、模型增量和最终落库结果组织为浏览器可消费的 SSE 事件。

    事件约定保持稳定：``progress`` 是经 LangGraph 节点确认过的处理进展，``accepted``
    表示用户消息已写入会话历史，``delta`` 是模型正文 Token，``done`` 是本轮最终结果。
    前端不需要猜测请求是否仍在运行，也不会把临时消息重复追加为正式消息。
    """

    if attachment_count:
        initial_message = "正在校验附件并建立本轮上下文…"
    else:
        initial_message = "正在建立本轮对话上下文…"
    yield _sse_event("status", {"message": initial_message})
    yield _sse_event(
        "progress",
        {
            "key": "intake_started",
            "label": initial_message,
            "state": "running",
        },
    )

    try:
        intake_result = None
        for intake_event in services.intake_graph.stream(inbound_message):
            if intake_event.step is not None:
                yield _sse_event(
                    "progress",
                    _intake_progress_payload(
                        intake_event.step,
                        attachment_count=attachment_count,
                    ),
                )
            if intake_event.result is not None:
                intake_result = intake_event.result
        if intake_result is None:
            raise RuntimeError("输入处理未返回最终结果")
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(inbound_message.attachments)
        yield _sse_event("error", {"detail": str(exc)})
        return
    except Exception:
        services.temporary_attachment_store.cleanup(inbound_message.attachments)
        yield _sse_event("error", {"detail": "材料处理出现异常，请稍后重新提交。"})
        return

    yield _sse_event(
        "accepted",
        {
            "message": _message_payload(intake_result.persisted_message),
            "turn": _turn_payload(intake_result.active_turn.turn),
        },
    )
    yield _sse_event("status", {"message": "材料处理完成，正在调用模型…"})
    yield _sse_event(
        "progress",
        {
            "key": "model_generation",
            "label": "上下文已就绪，正在组织分析建议…",
            "state": "running",
        },
    )
    try:
        for event in services.response_runner.stream(inbound_message, intake_result):
            if event.event_type == "delta" and event.content:
                yield _sse_event("delta", {"content": event.content})
                continue
            if event.event_type == "progress" and event.content:
                yield _sse_event(
                    "progress",
                    {
                        "key": "model_retry",
                        "label": event.content,
                        "state": "running",
                    },
                )
                continue
            if event.result is None:
                yield _sse_event("error", {"detail": "模型响应未生成最终结果。"})
                return
            payload = _stream_result_payload(
                intake_result,
                event.result,
                attachment_count=attachment_count,
            )
            if event.event_type == "done":
                yield _sse_event(
                    "progress",
                    {
                        "key": "model_generation",
                        "label": "分析建议已生成",
                        "state": "completed",
                    },
                )
            yield _sse_event(event.event_type, payload)
            return
    except Exception:
        yield _sse_event(
            "error",
            {"detail": "模型响应处理出现异常，请稍后重试。"},
        )


def _create_career_streaming_response(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> StreamingResponse:
    """在 HTTP 响应建立前占用一个 Turn 名额，满载时返回可理解的 429。"""

    if not services.stream_turn_slots.acquire(blocking=False):
        services.temporary_attachment_store.cleanup(inbound_message.attachments)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="当前正在处理的求职任务较多，请稍后再试。",
        )

    return StreamingResponse(
        _stream_career_response(
            services,
            inbound_message,
            attachment_count=attachment_count,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


_SSE_STREAM_END = object()


def _stream_events_with_heartbeats(
    events: Iterator[str],
    *,
    heartbeat_seconds: float,
    expected_turn_seconds: float,
    on_processing_complete: Callable[[], None] | None = None,
) -> Iterator[str]:
    """将阻塞式处理流桥接为可保活的 SSE 流。

    Docling、OCR 或上游模型在开始产出前可能长时间无数据。这里用一个守护线程
    消费业务事件，主线程按固定周期输出 SSE 注释帧，浏览器与代理会据此保持连接。
    注释帧不包含业务数据，前端无需也不会展示它。超过预期耗时仅给出一次安全进度
    提示，绝不伪造模型思考过程，也不从线程外强制中断外部请求。
    """

    output_queue: Queue[object] = Queue()
    client_disconnected = Event()

    def pump_events() -> None:
        try:
            for event in events:
                # 浏览器断开时，仍让 Graph / 模型流完整走到持久化收口；只丢弃
                # 已无人消费的 SSE 帧，避免未完成 Turn 在刷新页面后永久停在 running。
                if not client_disconnected.is_set():
                    output_queue.put(event)
        except BaseException as exc:  # 保证 SSE 可得到友好错误，不让线程异常丢失。
            if not client_disconnected.is_set():
                output_queue.put(exc)
        finally:
            if not client_disconnected.is_set():
                output_queue.put(_SSE_STREAM_END)
            if on_processing_complete is not None:
                on_processing_complete()

    worker = Thread(
        target=pump_events,
        name="career-sse-event-pump",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        if on_processing_complete is not None:
            on_processing_complete()
        raise
    started_at = monotonic()
    expected_duration_notified = False

    try:
        while True:
            try:
                queued_event = output_queue.get(timeout=heartbeat_seconds)
            except Empty:
                yield ": keepalive\n\n"
                if (
                    not expected_duration_notified
                    and monotonic() - started_at >= expected_turn_seconds
                ):
                    expected_duration_notified = True
                    yield _sse_event(
                        "progress",
                        {
                            "key": "extended_processing",
                            "label": "当前材料处理时间较长，任务仍在继续，请保持页面开启。",
                            "state": "running",
                        },
                    )
                continue

            if queued_event is _SSE_STREAM_END:
                return
            if isinstance(queued_event, BaseException):
                yield _sse_event(
                    "error",
                    {"detail": "服务处理出现异常，请稍后重新提交。"},
                )
                return
            if not isinstance(queued_event, str):
                yield _sse_event(
                    "error",
                    {"detail": "服务流返回了无效数据，请稍后重新提交。"},
                )
                return
            yield queued_event
    finally:
        client_disconnected.set()


def _intake_progress_payload(
    step: AgentStepName,
    *,
    attachment_count: int,
) -> dict[str, str]:
    """将已完成的 Graph 节点翻译为面向用户的安全进展摘要。"""

    labels = {
        AgentStepName.VALIDATE_INPUT: "输入校验完成，正在建立本轮上下文…",
        AgentStepName.BUILD_CONTEXT: (
            "上下文已建立，正在解析附件内容…"
            if attachment_count
            else "上下文已建立，正在读取职位信息…"
        ),
        AgentStepName.PARSE_MATERIAL: (
            "附件内容已提取，正在读取职位信息…"
            if attachment_count
            else "当前没有附件，正在读取职位信息…"
        ),
        AgentStepName.EXTRACT_JOB_DESCRIPTION: "职位信息处理完成，正在整理对话上下文…",
        AgentStepName.REDACT_SENSITIVE_DATA: "隐私处理完成，正在保存本轮消息…",
        AgentStepName.PERSIST_HISTORY: "问题已保存，准备调用模型…",
        AgentStepName.CLEANUP_TEMPORARY_FILES: "临时材料已清理，正在生成回复…",
    }
    return {
        "key": step.value,
        "label": labels[step],
        "state": "completed",
    }


def _stream_result_payload(
    intake_result,
    response_result,
    *,
    attachment_count: int,
) -> dict[str, object]:
    """将流结束后的持久化记录转换为与既有接口一致的最终负载。"""

    payload: dict[str, object] = {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(intake_result.persisted_message),
        "assistant_message": _message_payload(response_result.assistant_message),
        "completed_steps": [step.value for step in intake_result.completed_steps],
        "job_source": _job_source_payload(intake_result),
    }
    if attachment_count:
        payload["attachment_processing"] = {
            "temporary_only": True,
            "count": attachment_count,
            "text_characters": _analyzed_material_characters(
                intake_result.model_context,
            ),
            "resume_outline_characters": len(
                intake_result.model_context.redacted_resume_outline,
            ),
            "notices": list(intake_result.model_context.document_processing_notices),
            "items": [
                _attachment_processing_summary_payload(item)
                for item in intake_result.model_context.attachment_processing_summaries
            ],
            "pdf_without_extractable_text_count": (
                intake_result.model_context.pdf_without_extractable_text_count
            ),
            "cleaned_after_turn": True,
        }
    return payload


def _job_source_payload(intake_result) -> dict[str, str | None]:
    """返回职位链接的安全处理状态，不泄露原始链接或页面正文。"""

    return {
        "status": intake_result.job_source_status,
        "message": intake_result.job_source_message,
    }


def _analyzed_material_characters(model_context) -> int:
    """统计实际提供给模型的附件文本，包含已归类的简历提纲。"""

    return len(
        model_context.redacted_material_text
        + model_context.redacted_resume_outline,
    )


def _attachment_processing_summary_payload(summary) -> dict[str, object]:
    """将无敏感的附件处理摘要转换为前端稳定 API 格式。"""

    return {
        "kind": summary.kind.value,
        "page_count": summary.page_count,
        "processing_route": summary.processing_route,
        "parser_name": summary.parser_name,
        "parser_status": summary.parser_status,
        "native_text_quality_score": summary.native_text_quality_score,
        "issue_codes": list(summary.issue_codes),
    }


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    """序列化单个 SSE 数据帧，确保中文正文不会被转义为乱码。"""

    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _upload_kind(upload: UploadFile, *, is_resume: bool) -> AttachmentKind:
    """按上传声明的媒体类型选择受限材料类别，后续由临时存储层双重校验。"""

    media_type = (upload.content_type or "").lower()
    if media_type == "application/pdf":
        return AttachmentKind.RESUME_PDF if is_resume else AttachmentKind.JOB_DESCRIPTION_FILE
    if media_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        return (
            AttachmentKind.RESUME_DOCUMENT
            if is_resume
            else AttachmentKind.JOB_DESCRIPTION_FILE
        )
    if media_type in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
        "image/tiff",
    }:
        return (
            AttachmentKind.RESUME_IMAGE
            if is_resume
            else AttachmentKind.JOB_DESCRIPTION_IMAGE
        )
    raise ValueError(
        "附件类型不受支持；支持 PDF、Word（DOC/DOCX）、Excel（XLS/XLSX）、JPG、PNG、WebP、BMP 或 TIFF",
    )


def _interview_markdown_from_parsed_attachment(
    extracted_text: str,
    *,
    company_name: str,
    role_name: str,
) -> str:
    """把现有文档解析器的临时结果包装成可编辑的面经 Markdown。

    保留 Docling 返回的标题、列表、表格与阅读顺序；空解析结果不允许进入知识库，避免
    后续向量检索命中“空资料”。图片若 OCR 与 Vision 都没有得到可用文本，也会在这里
    返回明确的可操作提示。
    """

    normalized_text = extracted_text.strip()
    if not normalized_text:
        raise ValueError(
            "未从材料中识别到可用文本；请上传更清晰的文件，或改用粘贴 Markdown 入库",
        )
    return (
        f"# {company_name.strip()}｜{role_name.strip()} 面经\n\n"
        "## 解析原文\n\n"
        f"{normalized_text}"
    )


def _collection_job_payload(job) -> dict[str, object]:
    """输出采集任务的稳定 Web 契约，不包含任何第三方登录凭证。"""

    return {
        "id": str(job.id),
        "platform_key": job.platform_key,
        "keyword": job.keyword,
        "requested_limit": job.requested_limit,
        "connector_kind": job.connector_kind.value,
        "status": job.status.value,
        "policy_decision": job.policy_decision,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at is not None else None,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at is not None else None
        ),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _collection_candidate_payload(candidate) -> dict[str, object]:
    """输出候选资料的可审核正文；原始网页 HTML 和会话均不属于此契约。"""

    return {
        "id": str(candidate.id),
        "collection_job_id": str(candidate.collection_job_id),
        "source_url": candidate.source_url,
        "canonical_url": candidate.canonical_url,
        "source_platform": candidate.source_platform,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "published_at": (
            candidate.published_at.isoformat()
            if candidate.published_at is not None
            else None
        ),
        "markdown_content": candidate.extracted_markdown,
        "status": candidate.status.value,
        "error_code": candidate.error_code,
        "error_message": candidate.error_message,
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }


def _interview_experience_payload(experience) -> dict[str, object]:
    """将面经领域记录转换为稳定的前端 JSON，不返回检索向量。"""

    return {
        "id": str(experience.id),
        "company_id": str(experience.company_id),
        "company_name": experience.company_name,
        "job_name": experience.job_name,
        "role_name": experience.role_name,
        "interview_date": (
            experience.interview_date.isoformat()
            if experience.interview_date is not None
            else None
        ),
        "source_type": experience.source_type.value,
        "source_platform": experience.source_platform,
        "source_url": experience.source_url,
        "summary_text": experience.summary_text,
        "markdown_content": experience.markdown_content,
        "tags": list(experience.tags),
        "status": experience.status.value,
        "chunking_version": experience.chunking_version,
        "indexed_at": (
            experience.indexed_at.isoformat()
            if experience.indexed_at is not None
            else None
        ),
        "created_at": experience.created_at.isoformat(),
        "updated_at": experience.updated_at.isoformat(),
    }


def _interview_chunk_candidate_payload(candidate) -> dict[str, object]:
    """将 RAG 候选片段转换为可引用的前端契约，不泄露向量与内部检索实现。"""

    return {
        "chunk_id": str(candidate.chunk.id),
        "experience_id": str(candidate.chunk.experience_id),
        "chunk_index": candidate.chunk.chunk_index,
        "company_name": candidate.company_name,
        "job_name": candidate.job_name,
        "role_name": candidate.role_name,
        "interview_date": (
            candidate.interview_date.isoformat()
            if candidate.interview_date is not None
            else None
        ),
        "source_url": candidate.source_url,
        "heading_path": candidate.chunk.heading_path.split(" > "),
        "content": candidate.chunk.content_text,
        "citation": " · ".join(
            part
            for part in (
                candidate.company_name,
                candidate.job_name,
                candidate.chunk.heading_path,
            )
            if part
        ),
    }


def _build_interview_evidence(
    services: CareerAssistantServices,
    actor: CareerRequestActor,
    experience_ids: list[UUID] | tuple[UUID, ...],
    query: str,
) -> tuple[InterviewEvidence, ...]:
    """将前端 ``@面经`` 选择转换为服务端可校验、可追溯的最小 RAG 证据。

    前端只能提交面经 ID；服务端在组织边界内重新校验资料存在性后，按当前问题检索
    少量切片。这样既避免浏览器伪造或传入整篇 Markdown，也控制单轮 Prompt 的长度。
    """

    normalized_ids = tuple(dict.fromkeys(experience_ids))
    if not normalized_ids:
        return ()
    if len(normalized_ids) > 5:
        raise ValueError("单轮最多引用 5 份面经资料")

    for experience_id in normalized_ids:
        experience = services.interview_library_repository.get_experience(
            actor.organization_id,
            experience_id,
        )
        if experience is None:
            raise LookupError("引用的面经不存在或无访问权限")

    query_for_retrieval = query.strip()[:240] or "面经核心信息"
    retrieval_result = services.interview_retrieval_service.retrieve(
        actor.organization_id,
        query_for_retrieval,
        limit=6,
        experience_ids=normalized_ids,
    )
    evidence: list[InterviewEvidence] = []
    for candidate in retrieval_result.candidates:
        heading = candidate.chunk.parent_heading or "面经片段"
        citation = f"[面经：{candidate.company_name} · {candidate.job_name} · {heading}]"
        evidence.append(
            InterviewEvidence(
                experience_id=candidate.chunk.experience_id,
                citation=citation,
                content=candidate.chunk.contextual_content[:1_600],
                source_url=candidate.source_url,
            ),
        )
    return tuple(evidence)


def _conversation_payload(conversation) -> dict[str, object]:
    """把仓储会话模型转换为 API JSON。"""

    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "status": conversation.status,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "archived_at": conversation.archived_at.isoformat() if conversation.archived_at else None,
    }


def _message_payload(message) -> dict[str, object]:
    """把已脱敏消息转换为 API JSON。"""

    return {
        "id": str(message.id),
        "turn_id": str(message.turn_id) if message.turn_id else None,
        "role": message.role.value,
        "content": message.content_text,
        "is_redacted": message.is_redacted,
        "created_at": message.created_at.isoformat(),
    }


def _model_selection_payload(selection: ModelSelectionRequest | None) -> dict[str, object] | None:
    """将会话最近一次模型选择转换为不含密钥的前端状态。"""

    if selection is None:
        return None
    return {
        "mode": selection.mode.value,
        "profile_id": str(selection.profile_id) if selection.profile_id else None,
    }


def _turn_payload(turn) -> dict[str, object]:
    """把运行状态转换为 API JSON，支持后续前端轮询。"""

    return {
        "id": str(turn.id),
        "conversation_id": str(turn.conversation_id),
        "status": turn.status.value,
        "input_kind_codes": list(turn.input_kind_codes),
        "requested_selection_mode": turn.requested_selection_mode.value,
        "created_at": turn.created_at.isoformat(),
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "completed_at": turn.completed_at.isoformat() if turn.completed_at else None,
    }


def _profile_payload(profile: ModelProfileRecord) -> dict[str, object]:
    """把无密钥模型档案转换为 API JSON。"""

    return {
        "id": str(profile.id),
        "profile_key": profile.profile_key,
        "display_name": profile.display_name,
        "provider_key": profile.provider_key,
        "model_id": profile.model_id,
        "capabilities": sorted(capability.value for capability in profile.capabilities),
        "cost_tier": profile.cost_tier.value,
        "priority": profile.priority,
        "enabled": profile.enabled,
        "api_base_url": profile.api_base_url,
        "provider_website_url": profile.provider_website_url,
    }


def _availability_payload(availability: ModelProfileAvailability) -> dict[str, object]:
    """合并档案与策略/凭证状态，不返回环境变量中的值。"""

    return {
        "profile": _profile_payload(availability.profile),
        "readiness": availability.readiness.value,
        "credential_env_name": availability.credential_env_name,
        "blocked_reason": availability.blocked_reason,
    }


def _resolution_payload(resolution: ModelResolution) -> dict[str, object]:
    """输出模型路由预览，供聊天页在发送前展示。"""

    return {
        "profile": _profile_payload(resolution.profile),
        "reason": resolution.reason.value,
        "readiness": resolution.readiness.value,
        "credential_env_name": resolution.credential_env_name,
    }
