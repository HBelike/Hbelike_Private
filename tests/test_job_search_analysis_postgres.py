"""本机真实队列＋测试模型的搜索自动推荐验收，不发送任何外部模型请求。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from test_job_search_repository_postgres import store
from src.career_assistant.job_search_analysis import enqueue_job_search_analysis
from src.career_assistant.persistence.turn_job_repository import CareerTurnJobRepository, TurnPayloadRecord
from src.career_assistant.turn_payloads import restore_turn_input


@pytest.fixture
def prepared(store):
    searches, actor, conversation, message = store
    source_turn = uuid4()
    metadata = {"candidate_profile_context": "项目真实使用 LangGraph 与 RAG", "target_role_context": "",
                "context_binding_version": 1, "source": "career_api"}
    with searches._database.transaction() as connection:
        connection.execute(text("""INSERT INTO career_assistant.agent_turns
            (id,conversation_id,actor_id,requested_selection_mode,input_kind_codes,status,started_at,completed_at)
            VALUES (:id,:conversation,:actor,'platform_default',CAST(:kinds AS jsonb),'succeeded',NOW(),NOW())"""),
            {"id": source_turn, "conversation": conversation, "actor": actor, "kinds": json.dumps(["text"])})
        connection.execute(text("""INSERT INTO career_assistant.agent_turn_payloads
            (turn_id,input_text,effective_text,attachment_payloads_json,request_metadata_json)
            VALUES (:id,'帮我找网易云岗位并推荐','帮我找网易云岗位并推荐','[]',CAST(:metadata AS jsonb))"""),
            {"id": source_turn, "metadata": json.dumps(metadata)})
        connection.execute(text("UPDATE career_assistant.messages SET turn_id=:turn WHERE id=:message"), {"turn": source_turn, "message": message})
        connection.execute(text("""INSERT INTO career_assistant.messages(id,conversation_id,turn_id,role,content_text)
            VALUES (:id,:conversation,:turn,'user','帮我找网易云岗位并推荐')"""), {"id": uuid4(), "conversation": conversation, "turn": source_turn})
    batch = searches.create_batch(actor, conversation, {"source_message_id": str(message), "conditions": {"keyword": "网易云", "city": "杭州"}, "request_id": str(uuid4())})["batch"]
    saved = searches.save_page(actor, conversation, batch["id"], {"request_id": str(uuid4()), "page": 1,
        "jobs": [{"id": "job-1", "title": "Agent工程师", "company": "网易云", "description": "要求 LangGraph 与 RAG"}]})
    batch = searches.complete(actor, conversation, batch["id"], {"request_id": str(uuid4())})["batch"]
    services = SimpleNamespace(database=searches._database, turn_job_repository=CareerTurnJobRepository(searches._database))
    return SimpleNamespace(searches=searches, actor=actor, conversation=conversation, message=message,
                           source_turn=source_turn, batch=batch, services=services, saved=saved)


def payload_for(state, turn_id):
    with state.services.database.transaction() as connection:
        row = connection.execute(text("SELECT * FROM career_assistant.agent_turn_payloads WHERE turn_id=:id"), {"id": UUID(str(turn_id))}).mappings().one()
    return TurnPayloadRecord(UUID(str(turn_id)), row["input_text"], row["effective_text"], row["job_url"],
                             tuple(row["attachment_payloads_json"]), row["request_metadata_json"])


def test_auto_complete_enqueues_once_and_freezes_source_and_job_revision(prepared):
    state = prepared
    first = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    second = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    assert first["id"] == second["id"]
    assert first["parent_turn_id"] == str(state.source_turn)
    assert first["batch_id"] == state.batch["id"]
    status = state.services.turn_job_repository.get_turn_status(state.actor, UUID(first["id"]))
    inbound = restore_turn_input(status.turn, payload_for(state, first["id"])).inbound_message
    assert inbound.parent_turn_id == state.source_turn
    assert inbound.execution_source == "job_search_auto_analysis"
    assert inbound.candidate_profile_context == "项目真实使用 LangGraph 与 RAG"
    assert inbound.model_selection.mode.value == "platform_default"
    assert inbound.job_search_snapshot["catalog"][0]["revision"] == state.saved["revision"]
    reference = state.saved["job_refs"]["job-1"]
    state.searches.save_details(state.actor, state.conversation, state.batch["id"], {"request_id": str(uuid4()), "jobs": [{"job_ref": reference, "description": "后来新增的博士要求"}]})
    old = state.searches.resolve_snapshot(state.actor, state.conversation, inbound.job_search_snapshot)
    assert old["batches"][0]["jobs"][0]["description"] == "要求 LangGraph 与 RAG"


def test_automatic_analysis_preserves_original_quantity_and_preferences(prepared):
    state = prepared
    batch = {**state.batch, 'analysis_context': {**state.batch['analysis_context'],
        'text': '帮我找合成云科技岗位，推荐10个适合我的岗位，优先可接受应届生的职位'}}
    queued = enqueue_job_search_analysis(state.services, state.actor, state.conversation, batch)
    status = state.services.turn_job_repository.get_turn_status(state.actor, UUID(queued['id']))
    inbound = restore_turn_input(status.turn, payload_for(state, queued['id'])).inbound_message
    assert '推荐10个' in inbound.text and '应届生' in inbound.text
    assert '原请求未指定推荐数量' in inbound.text


def test_ordinary_chat_worker_does_not_resolve_job_bodies(prepared):
    from src.career_assistant.contracts import CareerInboundMessage
    from src.career_assistant.turn_payloads import prepare_turn_payload
    from src.career_assistant.agent_loop import CareerAgentLoop
    from src.career_assistant.persistence.conversation_repository import CareerConversationRepository
    from src.career_assistant.turn_worker import CareerAgentTurnProcessor
    state = prepared
    snapshot = state.searches.capture_snapshot(state.actor, state.conversation)
    inbound = CareerInboundMessage(uuid4(), state.conversation, state.actor, '你好', job_search_snapshot=snapshot)
    turn = state.services.turn_job_repository.enqueue(turn_id=inbound.turn_id, actor_id=state.actor,
        conversation_id=state.conversation, model_selection=inbound.model_selection,
        input_kind_codes=frozenset({'text'}), payload=prepare_turn_payload(inbound, ()))
    with state.services.database.transaction() as connection:
        connection.execute(text("UPDATE career_assistant.agent_turns SET status='running',started_at=NOW() WHERE id=:id"), {'id': turn.id})
    status = state.services.turn_job_repository.get_turn_status(state.actor, turn.id)
    class StopBeforeModel(Exception):
        pass
    def inspect_input(value, *args):
        assert value.job_search_snapshot['catalog'] == snapshot['catalog']
        assert value.job_search_snapshot['batches'] == []
        raise StopBeforeModel()
    loop = CareerAgentLoop(CareerConversationRepository(state.services.database))
    processor = CareerAgentTurnProcessor(loop, SimpleNamespace(stream_prepared=inspect_input), Mock(), state.services.turn_job_repository)
    with pytest.raises(StopBeforeModel):
        processor.process(SimpleNamespace(turn=status.turn, payload=payload_for(state, turn.id)), lambda *args: None)


def test_thirtieth_user_turn_still_allows_auto_and_does_not_add_user_quota(prepared):
    state = prepared
    with state.services.database.transaction() as connection:
        for _ in range(29):
            connection.execute(text("""INSERT INTO career_assistant.agent_turns
                (id,conversation_id,actor_id,requested_selection_mode,input_kind_codes,status,started_at,completed_at)
                VALUES (:id,:conversation,:actor,'platform_default',CAST(:kinds AS jsonb),'succeeded',NOW(),NOW())"""),
                {"id": uuid4(), "conversation": state.conversation, "actor": state.actor, "kinds": json.dumps(["text"])})
    assert state.services.turn_job_repository.get_turn_limit(state.actor, state.conversation).successful_turns == 30
    queued = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    with state.services.database.transaction() as connection:
        connection.execute(text("UPDATE career_assistant.agent_turns SET status='succeeded',completed_at=NOW() WHERE id=:id"), {"id": UUID(queued["id"])})
    assert state.services.turn_job_repository.get_turn_limit(state.actor, state.conversation).successful_turns == 30
    from src.career_assistant.persistence.conversation_repository import CareerConversationRepository
    assert CareerConversationRepository(state.services.database).count_successful_turns(state.actor, state.conversation) == 30


def test_real_processor_uses_test_client_without_fake_user_history(prepared):
    from src.career_assistant.agent_loop import CareerAgentLoop
    from src.career_assistant.intake_graph import CareerIntakeGraph
    from src.career_assistant.persistence.conversation_repository import CareerConversationRepository
    from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy
    from src.career_assistant.prompt_context import PromptContextService
    from src.career_assistant.response_runner import CareerResponseRunner
    from src.career_assistant.turn_worker import CareerAgentTurnProcessor
    from src.career_assistant.model_gateway import ModelReadiness
    from src.career_assistant.model_clients import CompletionUsage
    state = prepared
    queued = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    turn_id = UUID(queued["id"])
    with state.services.database.transaction() as connection:
        connection.execute(text("UPDATE career_assistant.agent_turns SET status='running',started_at=NOW() WHERE id=:id"), {"id": turn_id})
    status = state.services.turn_job_repository.get_turn_status(state.actor, turn_id)
    claim = SimpleNamespace(turn=status.turn, payload=payload_for(state, turn_id))
    repository = CareerConversationRepository(state.services.database)
    loop = CareerAgentLoop(repository)
    prompts = []
    class TestClient:
        def stream_complete(self, profile, env_name, prompt, **kwargs):
            prompts.append(prompt)
            kwargs["usage_callback"](CompletionUsage(100, 30))
            yield "推荐 S1-J1 Agent工程师：简历已有 LangGraph 与 RAG 项目证据，岗位也要求这些能力。"
    profile = SimpleNamespace(id=uuid4(), provider_key="synthetic", model_id="synthetic", display_name="测试模型",
                              context_policy=ModelContextPolicy(context_window_tokens=128000, reserved_output_tokens=4096))
    gateway = SimpleNamespace(resolve=Mock(return_value=SimpleNamespace(readiness=ModelReadiness.READY,
        profile=profile, credential_env_name="TEST_ONLY", credential="test-only")))
    runner = CareerResponseRunner(loop, gateway, chat_client=TestClient(), prompt_context=PromptContextService(repository, Mock()))
    processor = CareerAgentTurnProcessor(loop, CareerIntakeGraph(loop), runner, state.services.turn_job_repository)
    events = []
    outcome = processor.process(claim, lambda kind, data: events.append((kind, data)))
    assert outcome.status.value == "succeeded"
    prompt_text = "\n".join(str(item.content) for item in prompts[0])
    assert "要求 LangGraph 与 RAG" in prompt_text
    messages = repository.list_messages(state.actor, state.conversation)
    assert len([item for item in messages if item.role.value == "user"]) == 1
    generated = [item for item in messages if item.turn_id == turn_id]
    assert len(generated) == 1 and generated[0].role.value == "assistant"
    assert generated[0].ui_payload["type"] == "job_search_analysis"
    assert generated[0].ui_payload["batch_id"] == state.batch["id"]
    assert generated[0].ui_payload["parent_turn_id"] == str(state.source_turn)
    from src.career_assistant.conversation_memory import group_complete_turns
    conversation = repository.get_conversation(state.actor, state.conversation)
    history = repository.list_completed_dialogue_messages(conversation.organization_id, state.actor, state.conversation)
    automatic = next(block for block in group_complete_turns(history) if block.turn_id == turn_id)
    assert automatic.user_message is None and automatic.assistant_message.id == generated[0].id
    assert outcome.event_payload["assistant_message"]["source"] == "job_search_auto_analysis"


def test_real_complete_api_returns_same_analysis_turn_on_duplicate_callback(prepared, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.career_assistant.web import router as main_router
    from src.career_assistant.web.job_search_router import router
    state = prepared
    monkeypatch.setattr(main_router, "get_request_actor", lambda: SimpleNamespace(actor_id=state.actor))
    monkeypatch.setattr(main_router, "get_career_read_services", lambda request: state.services)
    monkeypatch.setattr(main_router, "get_career_services", lambda request: state.services)
    app = FastAPI()
    app.include_router(router)
    url = f"/api/career/conversations/{state.conversation}/job-search/batches/{state.batch['id']}/complete"
    body = {"request_id": str(uuid4())}
    with TestClient(app) as client:
        first, second = client.post(url, json=body), client.post(url, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["analysis_turn"]["id"] == second.json()["analysis_turn"]["id"]
    assert first.json()["batch"]["analysis_turn_id"] == first.json()["analysis_turn"]["id"]


def test_old_batch_auto_uses_original_model_selection_without_resetting_latest_choice(prepared):
    from src.career_assistant.persistence.conversation_repository import CareerConversationRepository
    state = prepared
    with state.services.database.transaction() as connection:
        connection.execute(text("""INSERT INTO career_assistant.agent_turns
            (id,conversation_id,actor_id,requested_selection_mode,input_kind_codes,status,started_at,completed_at)
            VALUES (:id,:conversation,:actor,'free_quota_first',CAST(:kinds AS jsonb),'succeeded',NOW(),NOW())"""),
            {"id": uuid4(), "conversation": state.conversation, "actor": state.actor, "kinds": json.dumps(["text"])})
    queued = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    status = state.services.turn_job_repository.get_turn_status(state.actor, UUID(queued["id"]))
    assert status.turn.requested_selection_mode.value == "platform_default"
    latest = CareerConversationRepository(state.services.database).get_last_model_selection(state.actor, state.conversation)
    assert latest.mode.value == "free_quota_first"


def test_only_explicit_retry_of_failed_analysis_creates_new_attempt(prepared):
    state = prepared
    first = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    batch = state.searches.list_batches(state.actor, state.conversation)[0]
    assert enqueue_job_search_analysis(state.services, state.actor, state.conversation, batch, retry_request_id="still-running")["id"] == first["id"]
    with state.services.database.transaction() as connection:
        connection.execute(text("UPDATE career_assistant.agent_turns SET status='failed',completed_at=NOW() WHERE id=:id"), {"id": UUID(first["id"])})
    retry = enqueue_job_search_analysis(state.services, state.actor, state.conversation, batch, retry_request_id="retry-1")
    assert retry["id"] != first["id"]
    assert enqueue_job_search_analysis(state.services, state.actor, state.conversation, batch, retry_request_id="retry-1")["id"] == retry["id"]


def test_cancel_after_snapshot_before_queue_admission_creates_no_analysis(prepared, monkeypatch):
    from src.career_assistant.persistence.job_search_repository import JobSearchRepository
    from src.career_assistant.persistence.turn_job_repository import ConversationTurnAdmissionError
    state = prepared
    original = JobSearchRepository.capture_snapshot
    def capture_then_cancel(repository, *args, **kwargs):
        snapshot = original(repository, *args, **kwargs)
        state.searches.complete(state.actor, state.conversation, state.batch["id"],
            {"request_id": str(uuid4()), "status": "cancelled"})
        return snapshot
    monkeypatch.setattr(JobSearchRepository, "capture_snapshot", capture_then_cancel)
    with pytest.raises(ConversationTurnAdmissionError, match="取消"):
        enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    with state.services.database.transaction() as connection:
        count = connection.execute(text("""SELECT count(*) FROM career_assistant.agent_turns
            WHERE conversation_id=:id AND input_kind_codes @> '["job_search_auto_analysis"]'::jsonb"""),
            {"id": state.conversation}).scalar_one()
    assert count == 0


@pytest.mark.parametrize("status", ["queued", "running"])
def test_stale_batch_and_different_retry_ids_do_not_duplicate_active_analysis(prepared, status):
    state = prepared
    first = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    if status == "running":
        with state.services.database.transaction() as connection:
            connection.execute(text("UPDATE career_assistant.agent_turns SET status='running',started_at=NOW() WHERE id=:id"), {"id": UUID(first["id"])})
    # 故意沿用关联建立之前的批次副本，覆盖 helper 的前置检查失去最新状态的场景。
    assert state.batch["analysis_turn_id"] is None
    second = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch, retry_request_id="different-request")
    assert second["id"] == first["id"]
    with state.services.database.transaction() as connection:
        count = connection.execute(text("""SELECT count(*) FROM career_assistant.agent_turns
            WHERE conversation_id=:id AND input_kind_codes @> '["job_search_auto_analysis"]'::jsonb"""),
            {"id": state.conversation}).scalar_one()
    assert count == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_complete_cancel_api_cancels_associated_analysis(prepared, monkeypatch, status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.career_assistant.web import router as main_router
    from src.career_assistant.web.job_search_router import router
    state = prepared
    queued = enqueue_job_search_analysis(state.services, state.actor, state.conversation, state.batch)
    turn_id = UUID(queued["id"])
    if status == "running":
        with state.services.database.transaction() as connection:
            connection.execute(text("UPDATE career_assistant.agent_turns SET status='running',started_at=NOW() WHERE id=:id"), {"id": turn_id})
    monkeypatch.setattr(main_router, "get_request_actor", lambda: SimpleNamespace(actor_id=state.actor))
    monkeypatch.setattr(main_router, "get_career_read_services", lambda request: state.services)
    monkeypatch.setattr(main_router, "get_career_services", lambda request: state.services)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(f"/api/career/conversations/{state.conversation}/job-search/batches/{state.batch['id']}/complete",
            json={"request_id": str(uuid4()), "status": "cancelled"})
    assert response.status_code == 200 and response.json()["analysis_turn"] is None
    cancelled = state.services.turn_job_repository.get_turn_status(state.actor, turn_id)
    assert cancelled.turn.status.value == ("cancelled" if status == "queued" else "running")
    assert cancelled.cancel_requested_at is not None


def test_real_user_enqueue_captures_search_view_before_later_jobs_and_batches(prepared):
    from src.career_assistant.contracts import CareerInboundMessage
    from src.career_assistant.turn_payloads import prepare_turn_payload
    state = prepared
    batch_id = state.batch["id"]
    original_revision = state.batch["revision"]
    focused = state.saved["job_refs"]["job-1"]
    inbound = CareerInboundMessage(turn_id=uuid4(), conversation_id=state.conversation, actor_id=state.actor,
        text="这个岗位适合我吗？", job_search_view={"active_batch_id": batch_id,
        "revisions": {batch_id: original_revision}, "focused_job_ref": focused, "uncommitted_batch_ids": []})
    # 文本与已解析附件入口均调用这份序列化；空 parsed_attachments 是合法输入。
    payload = prepare_turn_payload(inbound, (), source="career_api")
    assert payload.attachment_payloads == () and payload.request_metadata["job_search_view"]["focused_job_ref"] == focused
    turn = state.services.turn_job_repository.enqueue(turn_id=inbound.turn_id, conversation_id=state.conversation,
        actor_id=state.actor, model_selection=inbound.model_selection, input_kind_codes=frozenset({"text"}), payload=payload)
    stored = payload_for(state, turn.id)
    snapshot = stored.request_metadata["job_search_snapshot"]
    assert "job_search_view" not in stored.request_metadata
    assert snapshot["catalog"][0]["revision"] == original_revision
    assert snapshot["focused_job_ref"] == focused
    state.searches.save_details(state.actor, state.conversation, batch_id,
        {"request_id": str(uuid4()), "jobs": [{"job_ref": focused, "description": "入队之后的新要求"}]})
    newer = state.searches.create_batch(state.actor, state.conversation,
        {"request_id": str(uuid4()), "source_message_id": str(state.message), "analysis_requested": False,
         "conditions": {"keyword": "腾讯", "city": "上海"}})["batch"]
    state.searches.save_page(state.actor, state.conversation, newer["id"],
        {"request_id": str(uuid4()), "page": 1, "jobs": [{"id": "tencent-job", "company": "腾讯", "description": "腾讯JD"}]})
    restored = restore_turn_input(turn, payload_for(state, turn.id))
    assert restored.parsed_attachments == ()
    resolved = state.searches.resolve_snapshot(state.actor, state.conversation, restored.inbound_message.job_search_snapshot)
    assert len(resolved["batches"]) == 1
    assert resolved["batches"][0]["id"] == batch_id
    assert resolved["batches"][0]["revision"] == original_revision
    assert resolved["batches"][0]["jobs"][0]["description"] == "要求 LangGraph 与 RAG"
