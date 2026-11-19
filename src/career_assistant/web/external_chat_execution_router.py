"""外部聊天执行资格与独立发送操作 API。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.persistence.external_chat_execution_repository import (
    ExternalChatExecutionError,
    ExternalChatExecutionRepository,
)
from src.career_assistant.web.external_chat_router import _actor, _http_error, get_external_chat_repository


router = APIRouter(prefix="/api/career/external-chat", tags=["external-chat"])


def get_execution_repository(request: Request) -> ExternalChatExecutionRepository:
    return ExternalChatExecutionRepository(get_external_chat_repository(request)._database)


def _run(request: Request, method: str, *args, **kwargs) -> dict:
    from fastapi import HTTPException

    actor = _actor()
    try:
        return getattr(get_execution_repository(request), method)(actor.organization_id, actor.actor_id, *args, **kwargs)
    except ExternalChatExecutionError as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": str(error)}) from error
    except (ValueError, LookupError, SQLAlchemyError) as error:
        raise _http_error(error) from error


def _owner(payload: dict) -> dict:
    # revision 不接受 bool、浮点数或字符串，避免悄悄转换为另一版资格。
    from fastapi import HTTPException

    executor = payload.get("executor_id")
    revision = payload.get("revision")
    if not isinstance(executor, str) or not executor.strip() or len(executor) > 200 or type(revision) is not int or revision < 1:
        raise HTTPException(422, detail={"code": "external_chat_invalid", "message": "缺少有效的执行端标识或资格版本"})
    return {"executor_id": executor, "revision": revision}


@router.post("/accounts/{account_id}/execution")
def acquire_execution(account_id: UUID, payload: dict, request: Request) -> dict:
    from fastapi import HTTPException

    if type(payload.get("takeover", False)) is not bool:
        raise HTTPException(422, detail={"code": "external_chat_invalid", "message": "接管标识必须为布尔值"})
    return _run(request, "acquire", account_id, executor_id=payload.get("executor_id"), takeover=payload.get("takeover", False))


@router.post("/accounts/{account_id}/execution/check")
def check_execution(account_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "check", account_id, **_owner(payload))


@router.post("/accounts/{account_id}/execution/release")
def release_execution(account_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "release", account_id, **_owner(payload))


@router.post("/accounts/{account_id}/pause")
def pause_account(account_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "pause", account_id, code=payload.get("code"), message=payload.get("message"))


@router.post("/accounts/{account_id}/resume")
def resume_account(account_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "resume", account_id, **_owner(payload))


@router.post("/conversations/{conversation_id}/operations")
def create_operation(conversation_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "create_operation", conversation_id, client_operation_id=payload.get("client_operation_id"), body=payload.get("text"), **_owner(payload))


@router.get("/accounts/{account_id}/operations")
def list_operations(account_id: UUID, request: Request) -> dict:
    return _run(request, "list_operations", account_id)


@router.get("/operations/{client_operation_id}")
def get_operation(client_operation_id: UUID, request: Request) -> dict:
    return _run(request, "get_operation", client_operation_id)


@router.post("/operations/{client_operation_id}/transition")
def transition_operation(client_operation_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "transition", client_operation_id, status=payload.get("status"),
                platform_message_id=payload.get("platform_message_id"), error=payload.get("error"), **_owner(payload))


@router.post("/operations/{client_operation_id}/cancel")
def cancel_operation(client_operation_id: UUID, payload: dict, request: Request) -> dict:
    return _run(request, "cancel_operation", client_operation_id, reason=payload.get("reason", "user_cancelled"))
