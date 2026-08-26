"""用户可见的职业空间、求职记忆与回答来源 API。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.career_assistant.career_memory import CareerMemoryStatus
from src.career_assistant.web.router import get_career_services, get_request_actor


router = APIRouter(prefix="/api/career", tags=["career-memory"])


class CreateCareerSpaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CorrectMemoryRequest(BaseModel):
    display_text: str = Field(min_length=1, max_length=500)
    normalized_value: dict[str, object]


@router.get("/career-spaces")
def list_spaces(request: Request) -> dict[str, object]:
    actor = get_request_actor()
    items = get_career_services(request).memory_repository.list_spaces(
        actor.organization_id, actor.actor_id
    )
    return {"items": [_space_payload(item) for item in items]}


@router.post("/career-spaces", status_code=status.HTTP_201_CREATED)
def create_space(payload: CreateCareerSpaceRequest, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    try:
        item = get_career_services(request).memory_repository.create_space(
            actor.organization_id, actor.actor_id, payload.name
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": _space_payload(item)}


@router.get("/memories")
def list_memories(
    request: Request,
    career_space_id: UUID | None = None,
    status_filter: CareerMemoryStatus | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    actor = get_request_actor()
    items = get_career_services(request).memory_repository.list_memories(
        actor.organization_id,
        actor.actor_id,
        career_space_id=career_space_id,
        status=status_filter,
    )
    return {"items": [_memory_payload(item) for item in items]}


@router.post("/memories/{memory_id}/confirm")
def confirm_memory(memory_id: UUID, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    item = get_career_services(request).memory_repository.confirm_candidate(
        actor.organization_id, actor.actor_id, memory_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="候选求职记忆不存在")
    return {"item": _memory_payload(item)}


@router.patch("/memories/{memory_id}")
def correct_memory(
    memory_id: UUID,
    payload: CorrectMemoryRequest,
    request: Request,
) -> dict[str, object]:
    actor = get_request_actor()
    try:
        item = get_career_services(request).memory_service.correct(
            actor.organization_id,
            actor.actor_id,
            memory_id,
            display_text=payload.display_text,
            normalized_value=payload.normalized_value,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "item": _memory_payload(item),
        "resume_update_recommended": item.candidate_profile_id is not None,
    }


@router.post("/memories/{memory_id}/disable")
def disable_memory(memory_id: UUID, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    item = get_career_services(request).memory_repository.disable_memory(
        actor.organization_id, actor.actor_id, memory_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="求职记忆不存在")
    return {"item": _memory_payload(item)}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: UUID, request: Request) -> dict[str, bool]:
    actor = get_request_actor()
    deleted = get_career_services(request).memory_repository.delete_memory(
        actor.organization_id, actor.actor_id, memory_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="求职记忆不存在")
    return {"deleted": True}


@router.get("/turns/{turn_id}/memory-usages")
def list_turn_memory_usages(turn_id: UUID, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    rows = get_career_services(request).memory_repository.list_turn_usages(
        actor.organization_id, actor.actor_id, turn_id
    )
    return {
        "items": [
            {
                "memory_id": str(row["memory_id"]) if row["memory_id"] else None,
                "memory_type": row["memory_type"],
                "source_kind": row["source_kind"],
                "display_text": row["display_text"] or "该记忆已由用户删除",
                "candidate_profile_name": row["candidate_profile_name"],
                "candidate_profile_version": row["candidate_profile_version"],
                "source_conversation_created_at": (
                    row["source_conversation_created_at"].isoformat()
                    if row["source_conversation_created_at"] else None
                ),
            }
            for row in rows
        ],
    }


def _space_payload(item) -> dict[str, object]:
    return {
        "id": str(item.id),
        "name": item.name,
        "is_default": item.is_default,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _memory_payload(item) -> dict[str, object]:
    return {
        "id": str(item.id),
        "career_space_id": str(item.career_space_id) if item.career_space_id else None,
        "memory_type": item.memory_type,
        "normalized_value": item.normalized_value,
        "display_text": item.display_text,
        "source_kind": item.source_kind,
        "status": item.status,
        "candidate_profile_id": str(item.candidate_profile_id) if item.candidate_profile_id else None,
        "candidate_profile_version": item.candidate_profile_version,
        "source_conversation_id": str(item.source_conversation_id) if item.source_conversation_id else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
