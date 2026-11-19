"""对话求职批次的保存、预览和自动分析入口。"""
import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.career_assistant.persistence.job_search_repository import JobSearchRepository

router = APIRouter(prefix="/api/career/conversations/{conversation_id}/job-search", tags=["career-job-search"])


def _services(request):
    from src.career_assistant.web.router import get_career_read_services, get_request_actor
    return JobSearchRepository(get_career_read_services(request).database), get_request_actor()


async def _call(request, method, conversation_id, *args, **kwargs):
    repository, actor = _services(request)
    try:
        return await asyncio.to_thread(getattr(repository, method), actor.actor_id, conversation_id, *args, **kwargs)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(422, detail={"code": "job_search_invalid", "message": str(error)}) from error


@router.get("/batches")
async def list_batches(conversation_id: UUID, request: Request):
    return {"batches": await _call(request, "list_batches", conversation_id)}


@router.post("/batches")
async def create_batch(conversation_id: UUID, payload: dict, request: Request):
    return await _call(request, "create_batch", conversation_id, payload)


@router.get("/batches/{batch_id}/jobs")
async def get_jobs(conversation_id: UUID, batch_id: UUID, request: Request,
                   revision: int | None = Query(None, ge=0), cursor: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=50)):
    return await _call(request, "get_jobs", conversation_id, batch_id, revision=revision, cursor=cursor, limit=limit)


@router.post("/batches/{batch_id}/pages")
async def save_page(conversation_id: UUID, batch_id: UUID, payload: dict, request: Request):
    return await _call(request, "save_page", conversation_id, batch_id, payload)


@router.post("/batches/{batch_id}/details")
async def save_details(conversation_id: UUID, batch_id: UUID, payload: dict, request: Request):
    return await _call(request, "save_details", conversation_id, batch_id, payload)


@router.patch("/batches/{batch_id}/focus")
async def focus(conversation_id: UUID, batch_id: UUID, payload: dict, request: Request):
    return await _call(request, "focus", conversation_id, batch_id, payload)


async def _analysis(request, conversation_id, batch, retry_request_id=None):
    from src.career_assistant.web.router import get_career_services, get_request_actor
    from src.career_assistant.job_search_analysis import enqueue_job_search_analysis
    if not batch.get("analysis_requested") or batch["search_status"] not in ("succeeded", "empty"):
        return None
    actor = get_request_actor()
    try:
        return await asyncio.to_thread(enqueue_job_search_analysis, get_career_services(request), actor.actor_id,
                                       conversation_id, batch, retry_request_id=retry_request_id)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, detail={"code": "job_search_analysis_unavailable", "message": str(error)}) from error


@router.post("/batches/{batch_id}/complete")
async def complete(conversation_id: UUID, batch_id: UUID, payload: dict, request: Request):
    result = await _call(request, "complete", conversation_id, batch_id, payload)
    if result["batch"].get("search_status") == "cancelled" and result["batch"].get("analysis_turn_id"):
        from src.career_assistant.persistence.turn_job_repository import CareerTurnJobRepository
        repository, actor = _services(request)
        turns = CareerTurnJobRepository(repository._database)
        turn_id = UUID(result["batch"]["analysis_turn_id"])
        status = await asyncio.to_thread(turns.request_cancel, actor.actor_id, turn_id)
        if status is not None:
            await asyncio.to_thread(turns.append_event, turn_id,
                "cancelled" if status.value == "cancelled" else "cancel_requested", {"state": status.value})
    analysis = await _analysis(request, conversation_id, result["batch"])
    # complete 重试读取最新关联；内部入队具有独立幂等键。
    repository, actor = _services(request)
    batches = await asyncio.to_thread(repository.list_batches, actor.actor_id, conversation_id)
    result["batch"] = next(item for item in batches if item["id"] == str(batch_id))
    return {**result, "analysis_turn": analysis}


@router.post("/batches/{batch_id}/analysis/retry")
async def retry_analysis(conversation_id: UUID, batch_id: UUID, payload: dict, request: Request):
    repository, actor = _services(request)
    batches = await _call(request, "list_batches", conversation_id)
    batch = next((item for item in batches if item["id"] == str(batch_id)), None)
    if batch is None:
        raise HTTPException(404, detail="搜索批次不存在")
    try:
        request_id = repository._request_id(payload)
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    analysis = await _analysis(request, conversation_id, batch, request_id)
    latest = await asyncio.to_thread(repository.list_batches, actor.actor_id, conversation_id)
    return {"batch": next(item for item in latest if item["id"] == str(batch_id)), "analysis_turn": analysis}
