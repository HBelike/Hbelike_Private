"""外部招聘聊天的只读历史与浏览器快照导入接口。"""

from __future__ import annotations

import asyncio
import logging
import os
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.external_chat import normalize_snapshot
from src.career_assistant.persistence.external_chat_repository import ExternalChatRepository


router = APIRouter(prefix="/api/career/external-chat", tags=["external-chat"])
logger = logging.getLogger(__name__)


def get_external_chat_repository(request: Request) -> ExternalChatRepository:
    # 延迟导入避免与主路由安装循环依赖；读取历史只使用轻量数据库服务。
    from src.career_assistant.web.router import get_career_read_services

    return ExternalChatRepository(get_career_read_services(request).database)


def _actor():
    from src.career_assistant.web.router import get_request_actor

    return get_request_actor()


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, detail={"code": "external_chat_not_found", "message": str(error)})
    if isinstance(error, ValueError):
        return HTTPException(422, detail={"code": "external_chat_invalid", "message": str(error)})
    # 数据库异常可能包含正文参数，不将 SQL 或消息内容写入日志/响应。
    logger.error("外部聊天数据库操作失败：%s", type(error).__name__)
    return HTTPException(503, detail={"code": "external_chat_unavailable", "message": "聊天历史暂不可用，请稍后重试"})


@router.post("/sync")
def sync_external_chat(payload: dict, request: Request) -> dict:
    actor = _actor()
    try:
        snapshot = normalize_snapshot(payload)
        return get_external_chat_repository(request).sync(actor.organization_id, actor.actor_id, snapshot)
    except (ValueError, LookupError, SQLAlchemyError) as error:
        raise _http_error(error) from error


@router.get("/accounts")
def list_external_chat_accounts(request: Request) -> dict:
    actor = _actor()
    try:
        return {"accounts": get_external_chat_repository(request).list_accounts(actor.organization_id, actor.actor_id)}
    except SQLAlchemyError as error:
        raise _http_error(error) from error


@router.get("/accounts/{account_id}/conversations")
def list_external_chat_conversations(account_id: UUID, request: Request) -> dict:
    actor = _actor()
    try:
        return {"conversations": get_external_chat_repository(request).list_conversations(actor.organization_id, actor.actor_id, account_id)}
    except (LookupError, SQLAlchemyError) as error:
        raise _http_error(error) from error


@router.get("/conversations/{conversation_id}/messages")
def list_external_chat_messages(
    conversation_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    before: UUID | None = None,
) -> dict:
    """before 为上一页最早消息的内部 id，服务端以 (sent_at, id) 稳定向前翻页。"""

    actor = _actor()
    try:
        return get_external_chat_repository(request).list_messages(actor.organization_id, actor.actor_id, conversation_id, limit=limit, before=before)
    except (LookupError, SQLAlchemyError) as error:
        raise _http_error(error) from error


def _cleanup_repository(app: FastAPI) -> ExternalChatRepository | None:
    from src.career_assistant.web.router import _load_career_environment

    _load_career_environment(app.state.career_assistant_project_root)
    if not os.getenv("CAREER_DATABASE_URL", "").strip():
        return None
    return get_external_chat_repository(Request({"type": "http", "app": app}))


def install_external_chat_api(app: FastAPI, *, cleanup_interval_seconds: float = 60) -> None:
    """注册现有 API 生命周期内的轻量清理，不增加独立调度系统。"""

    from src.career_assistant.web.external_chat_execution_router import router as execution_router

    app.include_router(router)
    app.include_router(execution_router)
    app.state.external_chat_cleanup_task = None
    app.state.external_chat_cleanup_stop = None

    async def purge(repository: ExternalChatRepository) -> None:
        try:
            if hasattr(repository, "_database"):
                from src.career_assistant.persistence.external_chat_execution_repository import ExternalChatExecutionRepository

                # 先用仍在库内的源时间校准操作截止点，再删除到期历史。
                await asyncio.to_thread(ExternalChatExecutionRepository(repository._database).purge_expired)
            await asyncio.to_thread(repository.purge_expired)
        except (SQLAlchemyError, OSError, ValueError) as error:
            logger.error("外部聊天到期清理失败：%s", type(error).__name__)

    @app.on_event("startup")
    async def start_external_chat_cleanup() -> None:
        try:
            repository = _cleanup_repository(app)
        except (HTTPException, OSError, RuntimeError, ValueError) as error:
            logger.error("外部聊天清理初始化未就绪：%s", type(error).__name__)
            return
        if repository is None:
            return
        await purge(repository)
        stop = asyncio.Event()
        app.state.external_chat_cleanup_stop = stop

        async def cleanup_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cleanup_interval_seconds)
                except TimeoutError:
                    await purge(repository)

        app.state.external_chat_cleanup_task = asyncio.create_task(cleanup_loop())

    @app.on_event("shutdown")
    async def stop_external_chat_cleanup() -> None:
        stop = app.state.external_chat_cleanup_stop
        task = app.state.external_chat_cleanup_task
        if stop is not None:
            stop.set()
        if task is not None:
            # 若已开始一个事务，等待它结束再释放主模块共享的数据库连接池。
            await task
        app.state.external_chat_cleanup_task = None
        app.state.external_chat_cleanup_stop = None
