"""浏览器搜索结果保存后，使用现有 Turn 队列执行单次内部推荐。"""

from uuid import UUID, uuid4

from src.career_assistant.contracts import CareerInboundMessage, ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.persistence.job_search_repository import JobSearchRepository
from src.career_assistant.turn_payloads import prepare_turn_payload


JOB_SEARCH_ANALYSIS_SOURCE = "job_search_auto_analysis"


def enqueue_job_search_analysis(services, actor_id, conversation_id, batch, retry_request_id=None):
    """固定此刻的岗位版本；回调重发沿用同一个内部任务。"""
    if not batch.get("analysis_requested") or batch.get("search_status") not in {"succeeded", "empty"}:
        return None
    batch_id = UUID(str(batch["id"]))
    existing_id = batch.get("analysis_turn_id")
    if existing_id:
        existing = services.turn_job_repository.get_turn_status(actor_id, UUID(str(existing_id)))
        if existing is not None and (not retry_request_id or existing.turn.status.value not in {"failed", "cancelled"}):
            return _analysis_status(existing, batch)
    searches = JobSearchRepository(services.database)
    snapshot = searches.capture_snapshot(actor_id, conversation_id, {
        "active_batch_id": str(batch_id), "focused_job_ref": None,
        "revisions": {str(batch_id): batch["revision"]},
    })
    snapshot["catalog"] = [entry for entry in snapshot["catalog"]
                           if str(entry.get("batch_id", entry.get("id"))) == str(batch_id)]
    snapshot["auto_analysis"] = True
    snapshot["analysis_mode"] = "recommend"
    captured = batch.get("analysis_context") or {}
    metadata = captured.get("metadata") or {}
    selection = captured.get("model_selection") or {}
    conditions = batch.get("conditions") or {}
    original_request = str(captured.get("text") or "").strip()
    text = (
        (f"原始用户需求：\n{original_request}\n\n" if original_request else "") +
        f"请分析本次搜索第{batch['batch_no']}批（{conditions.get('keyword', '')}，"
        f"{conditions.get('city') or '已选城市'}）已保存的岗位，结合已提供的简历推荐具体岗位。"
        "沿用原请求指定的推荐数量和偏好；原请求未指定推荐数量时，最多推荐三个。"
        "已保存岗位不足请求数量时，说明实际可推荐数量，不虚构或补搜岗位。"
        "只使用该批次的岗位，给出岗位引用、简历证据、要求与差距；缺少简历或JD时明确说明，"
        "区分初筛与已核对要求，不虚构分数。不要再发起搜索，不修改目标岗位。"
    )
    inbound = CareerInboundMessage(
        turn_id=uuid4(), conversation_id=conversation_id, actor_id=actor_id, text=text,
        model_selection=ModelSelectionRequest(
            mode=ModelSelectionMode(selection.get("mode") or "platform_default"),
            profile_id=UUID(str(selection["profile_id"])) if selection.get("profile_id") else None,
        ),
        candidate_profile_context=str(metadata.get("candidate_profile_context") or ""),
        target_role_context=str(metadata.get("target_role_context") or ""),
        context_binding_version=metadata.get("context_binding_version"),
        job_search_snapshot=snapshot, execution_source=JOB_SEARCH_ANALYSIS_SOURCE,
        parent_turn_id=UUID(str(batch["source_turn_id"])) if batch.get("source_turn_id") else None,
        job_search_batch_id=batch_id,
    )
    inbound.validate()
    suffix = f"retry:{retry_request_id}" if retry_request_id else "initial"
    turn = services.turn_job_repository.enqueue(
        turn_id=inbound.turn_id, conversation_id=conversation_id, actor_id=actor_id,
        model_selection=inbound.model_selection, input_kind_codes=frozenset({JOB_SEARCH_ANALYSIS_SOURCE}),
        payload=prepare_turn_payload(inbound, (), source=JOB_SEARCH_ANALYSIS_SOURCE),
        idempotency_key=f"{JOB_SEARCH_ANALYSIS_SOURCE}:{batch_id}:{suffix}", allow_queue_when_busy=True,
    )
    status = services.turn_job_repository.get_turn_status(actor_id, turn.id)
    return _analysis_status(status, batch) if status is not None else None


def _analysis_status(status, batch):
    from src.career_assistant.web.turn_router import _queue_status_payload
    return {**_queue_status_payload(status), "source": JOB_SEARCH_ANALYSIS_SOURCE,
            "parent_turn_id": str(batch["source_turn_id"]) if batch.get("source_turn_id") else None,
            "batch_id": str(batch["id"])}
